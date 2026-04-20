"""
Transcribe local MP4 (or other ffmpeg-readable media) with speaker diarization.

Requires: google-cloud-speech, ffmpeg on PATH, and ADC credentials
(`gcloud auth application-default login` or GOOGLE_APPLICATION_CREDENTIALS).

Cloud Speech limits inline audio to ~1 minute. Longer files must be referenced
by a Cloud Storage URI: set SPEECH_AUDIO_GCS_URI=gs://bucket/object.flac (or
.wav) and use long_running_recognize.

Project bucket (display intent: "audio meetings audio"): mono 16 kHz FLAC copies
of the two EMS meeting recordings in run/ are stored under meetings/.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from google.api_core import exceptions as google_exceptions
from google.cloud import speech_v1p1beta1 as speech

# Human-friendly name: "audio meetings audio" — GCS bucket names must be DNS-like and global.
DEFAULT_MEETINGS_BUCKET = "audio-meetings-audio-dn-studio-01"
DEFAULT_MEETING_GCS_URIS: Tuple[str, ...] = (
    f"gs://{DEFAULT_MEETINGS_BUCKET}/meetings/ems-wave2-workshop.flac",
    f"gs://{DEFAULT_MEETINGS_BUCKET}/meetings/ems-workshop-5-revenue.flac",
)

# ~55s of mono s16le at 16 kHz stays under the 1 min / 10 MB sync limits.
SAMPLE_RATE_HERTZ = 16_000
MAX_SYNC_SECONDS = 55


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _probe_duration_seconds(path: Path) -> float:
    """Return container duration in seconds (best effort) via ffprobe."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffprobe is required to measure audio duration. Install ffmpeg (includes ffprobe) on PATH."
        ) from exc
    if proc.returncode != 0:
        return -1.0
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return -1.0


def _decode_to_linear16_mono(path: Path) -> bytes:
    """Extract mono 16-bit PCM at SAMPLE_RATE_HERTZ using ffmpeg."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-f",
                "s16le",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE_HERTZ),
                "-",
            ],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg is required to decode MP4 (or other containers) to LINEAR16. "
            "Install ffmpeg and ensure it is on PATH."
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err or 'no stderr'}")
    if not proc.stdout:
        raise RuntimeError("ffmpeg produced no audio data.")
    return proc.stdout


def _decode_linear16_trim_seconds(path: Path, max_seconds: int) -> bytes:
    """Decode only the first max_seconds of audio to limit RAM and sync API size."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-t",
                str(max_seconds),
                "-f",
                "s16le",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE_HERTZ),
                "-",
            ],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg is required to decode MP4 (or other containers) to LINEAR16. "
            "Install ffmpeg and ensure it is on PATH."
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err or 'no stderr'}")
    if not proc.stdout:
        raise RuntimeError("ffmpeg produced no audio data.")
    return proc.stdout


def _pcm_duration_seconds(pcm: bytes) -> float:
    return len(pcm) / (SAMPLE_RATE_HERTZ * 2)


def _build_config(
    *,
    encoding: Optional[Any] = None,
    sample_rate_hertz: Optional[int] = None,
    audio_channel_count: Optional[int] = 1,
    model: Optional[str] = None,
    enable_word_time_offsets: bool = True,
) -> speech.RecognitionConfig:
    diarization_config = speech.SpeakerDiarizationConfig(
        enable_speaker_diarization=True,
        min_speaker_count=2,
        max_speaker_count=10,
    )
    enc = encoding or speech.RecognitionConfig.AudioEncoding.LINEAR16
    kwargs: Dict[str, Any] = {
        "encoding": enc,
        "language_code": "en-US",
        "diarization_config": diarization_config,
        "enable_automatic_punctuation": True,
        "enable_word_time_offsets": enable_word_time_offsets,
    }
    if sample_rate_hertz is not None:
        kwargs["sample_rate_hertz"] = sample_rate_hertz
    if audio_channel_count is not None:
        kwargs["audio_channel_count"] = audio_channel_count
    if model:
        kwargs["model"] = model
    return speech.RecognitionConfig(**kwargs)


def _config_for_gcs_uri(gcs_uri: str, *, model: Optional[str] = None) -> speech.RecognitionConfig:
    lower = gcs_uri.split("?", 1)[0].lower()
    if lower.endswith(".flac"):
        # Sample rate is read from the FLAC header when omitted.
        return _build_config(
            encoding=speech.RecognitionConfig.AudioEncoding.FLAC,
            sample_rate_hertz=None,
            audio_channel_count=None,
            model=model,
        )
    if lower.endswith(".mp3"):
        rate = int(os.environ.get("SPEECH_SAMPLE_RATE_HERTZ", "44100"))
        return _build_config(
            encoding=speech.RecognitionConfig.AudioEncoding.MP3,
            sample_rate_hertz=rate,
            audio_channel_count=None,
            model=model,
        )
    # Default: mono 16 kHz s16le (e.g. WAV produced by ffmpeg -f wav / raw headerless with .pcm).
    return _build_config(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=SAMPLE_RATE_HERTZ,
        audio_channel_count=1,
        model=model,
    )


def _meeting_model_name() -> Optional[str]:
    raw = os.environ.get("SPEECH_MODEL", "video").strip()
    if not raw or raw.lower() in {"default", "none"}:
        return None
    return raw


def _long_running_gcs(client: speech.SpeechClient, gcs_uri: str) -> Any:
    """Run long-running recognition; retry without model if the API rejects the config."""
    model = _meeting_model_name()
    audio = speech.RecognitionAudio(uri=gcs_uri)
    timeout_sec = int(os.environ.get("SPEECH_OPERATION_TIMEOUT_SEC", str(3_600)))

    def _run(cfg: speech.RecognitionConfig) -> Any:
        operation = client.long_running_recognize(config=cfg, audio=audio)
        print(
            "Waiting for long-running operation to complete (this can take a while)...",
            flush=True,
        )
        return operation.result(timeout=timeout_sec)

    config = _config_for_gcs_uri(gcs_uri, model=model)
    try:
        return _run(config)
    except google_exceptions.InvalidArgument:
        if not model:
            raise
        print(
            f"Warning: model={model!r} was rejected; retrying without an explicit model.",
            file=sys.stderr,
            flush=True,
        )
        return _run(_config_for_gcs_uri(gcs_uri, model=None))


def _collect_words(response: Any) -> List[Any]:
    words = []
    for result in response.results:
        if not result.alternatives:
            continue
        alt = result.alternatives[0]
        if alt.words:
            words.extend(alt.words)
    return words


def _word_start_seconds(word_info: Any) -> Optional[float]:
    st = getattr(word_info, "start_time", None)
    if st is None:
        return None
    sec = int(getattr(st, "seconds", 0) or 0)
    nano = int(getattr(st, "nanos", 0) or 0)
    if sec == 0 and nano == 0:
        return None
    return sec + nano / 1_000_000_000.0


def _print_transcript(response: Any, *, label: str) -> None:
    words_info = _collect_words(response)
    if not words_info and response.results and response.results[-1].alternatives:
        words_info = list(response.results[-1].alternatives[0].words)
    if not words_info:
        print(f"[{label}] No words returned (empty transcript).", file=sys.stderr)
        return

    print(f"\n=== {label} ===", flush=True)
    for word_info in words_info:
        tag = getattr(word_info, "speaker_tag", 0) or 0
        start_s = _word_start_seconds(word_info)
        if start_s is not None:
            print(
                f"word: '{word_info.word}', speaker_tag: {tag}, start_s: {start_s:.2f}",
                flush=True,
            )
        else:
            print(f"word: '{word_info.word}', speaker_tag: {tag}", flush=True)


def _transcribe_gcs_uris(client: speech.SpeechClient, uris: Sequence[str]) -> None:
    for uri in uris:
        print(f"\nUsing Cloud Storage URI (long-running recognize): {uri}", flush=True)
        response = _long_running_gcs(client, uri)
        _print_transcript(response, label=uri)


def main() -> None:
    client = speech.SpeechClient()

    gcs_uri = os.environ.get("SPEECH_AUDIO_GCS_URI", "").strip()
    # Input lives at repo root run/ (s.py is in sub-project/).
    default_file = (
        _script_dir().parent
        / "run"
        / "EMS Wave2 Workshop Account determination and GL integration-20251106_220111-Meeting Recording.mp4"
    )
    speech_file = Path(os.environ.get("SPEECH_LOCAL_FILE", str(default_file))).expanduser()

    if gcs_uri:
        _transcribe_gcs_uris(client, [gcs_uri])
        return

    if not speech_file.is_file():
        print(
            f"Local file not found ({speech_file}); transcribing default meeting FLACs from "
            f"gs://{DEFAULT_MEETINGS_BUCKET}/meetings/ ...",
            flush=True,
        )
        _transcribe_gcs_uris(client, DEFAULT_MEETING_GCS_URIS)
        return

    probed = _probe_duration_seconds(speech_file)
    trim_env = os.environ.get("SPEECH_SYNC_TRIM_SECONDS", "").strip()
    if probed > MAX_SYNC_SECONDS and not trim_env:
        print(
            f"Local recording is about {probed:.0f}s (too long for inline sync). "
            f"Transcribing uploaded FLAC copies from gs://{DEFAULT_MEETINGS_BUCKET}/meetings/ ...",
            flush=True,
        )
        _transcribe_gcs_uris(client, DEFAULT_MEETING_GCS_URIS)
        return

    if probed < 0:
        print(
            "Warning: ffprobe could not read duration; proceeding (long files may use a lot of RAM).",
            file=sys.stderr,
        )

    config = _build_config(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=SAMPLE_RATE_HERTZ,
        audio_channel_count=1,
    )
    if trim_env:
        max_sec = max(1, min(MAX_SYNC_SECONDS, int(trim_env)))
        pcm = _decode_linear16_trim_seconds(speech_file, max_sec)
    else:
        pcm = _decode_to_linear16_mono(speech_file)
    duration = _pcm_duration_seconds(pcm)
    print(f"Decoded audio: {duration:.1f}s mono LINEAR16 @ {SAMPLE_RATE_HERTZ} Hz")

    if duration > MAX_SYNC_SECONDS:
        sys.exit(
            f"Decoded audio is about {duration:.0f}s; sync recognize only supports ~1 minute of inline audio.\n"
            "Upload a FLAC or LINEAR16 WAV to a bucket, then run again with:\n"
            "  set SPEECH_AUDIO_GCS_URI=gs://YOUR_BUCKET/path/to/audio.flac\n"
            "Or set SPEECH_SYNC_TRIM_SECONDS=55 to transcribe only the first ~55 seconds."
        )

    audio = speech.RecognitionAudio(content=pcm)
    print("Sending synchronous recognize request...")
    response = client.recognize(config=config, audio=audio)
    _print_transcript(response, label="sync-recognize")


if __name__ == "__main__":
    main()
