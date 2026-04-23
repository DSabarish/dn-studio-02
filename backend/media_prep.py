from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import ffmpeg
except ModuleNotFoundError:
    ffmpeg = None

logger = logging.getLogger("dn_studio.media_prep")


def prepare_media_for_transcription(source_path: str, transcription_engine: str) -> tuple[str, bool, str]:
    """
    Build an optimized temporary audio derivative for transcription.
    Returns (path_to_use, is_temp_file_created).
    """
    source = Path(source_path)
    engine = (transcription_engine or "").strip().lower()
    if "assemblyai" in engine:
        suffix = ".mp3"
        output_kwargs = {"vn": None, "ac": 1, "ar": 16000, "b:a": "32k"}
        cli_args = ["-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k"]
    else:
        # Whisper path: normalized mono PCM wav is stable for decoding/transcription.
        suffix = ".wav"
        output_kwargs = {"vn": None, "ac": 1, "ar": 16000}
        cli_args = ["-vn", "-ac", "1", "-ar", "16000"]

    try:
        temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_audio.close()
        logger.info(
            "Preparing optimized audio | source=%s | output=%s | engine=%s",
            str(source),
            temp_audio.name,
            transcription_engine,
        )

        if ffmpeg is not None:
            ffmpeg.input(str(source)).output(temp_audio.name, **output_kwargs).overwrite_output().run(quiet=True)
        else:
            ffmpeg_bin = shutil.which("ffmpeg")
            if not ffmpeg_bin:
                logger.info("ffmpeg-python unavailable and ffmpeg binary not found; using source media path directly.")
                return source_path, False, "ffmpeg-python and ffmpeg binary unavailable; using source media directly."
            cmd = [ffmpeg_bin, "-y", "-i", str(source), *cli_args, temp_audio.name]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        prep_note = f"Prepared optimized audio `{Path(temp_audio.name).name}` for {transcription_engine}."
        return temp_audio.name, True, prep_note
    except Exception:
        logger.exception("Audio preparation failed; falling back to source file.")
        return source_path, False, "Audio preparation failed; falling back to source media."
