from __future__ import annotations

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.helper import format_timestamp


def transcribe_media_file(media_path: str, *, use_eu_region: bool = False, poll_interval_seconds: int = 3) -> dict:
    """Transcribe a local audio/video file with AssemblyAI and return structured output."""
    load_dotenv()
    api_key = (os.getenv("ASSEMBLYAI_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("Missing ASSEMBLYAI_API_KEY environment variable.")

    path = Path(media_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Media file not found: {media_path}")

    base_url = "https://api.eu.assemblyai.com" if use_eu_region else "https://api.assemblyai.com"
    headers = {"authorization": api_key}
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.0,
        status_forcelist=[408, 429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    with path.open("rb") as media_file:
        upload_response = session.post(f"{base_url}/v2/upload", headers=headers, data=media_file, timeout=300)
    if upload_response.status_code != 200:
        raise RuntimeError(f"Upload failed ({upload_response.status_code}): {upload_response.text}")

    audio_url = upload_response.json().get("upload_url")
    if not audio_url:
        raise RuntimeError("Upload succeeded but no upload_url was returned.")

    request_payload = {
        "audio_url": audio_url,
        "speech_models": ["universal-3-pro", "universal-2"],
        "language_detection": True,
        "speaker_labels": True,
    }
    create_response = session.post(f"{base_url}/v2/transcript", headers=headers, json=request_payload, timeout=60)
    if create_response.status_code != 200:
        raise RuntimeError(f"Transcription create failed ({create_response.status_code}): {create_response.text}")

    transcript_id = create_response.json().get("id")
    if not transcript_id:
        raise RuntimeError("Transcription create succeeded but no transcript id was returned.")

    poll_url = f"{base_url}/v2/transcript/{transcript_id}"
    while True:
        poll_response = session.get(poll_url, headers=headers, timeout=60)
        if poll_response.status_code != 200:
            raise RuntimeError(f"Polling failed ({poll_response.status_code}): {poll_response.text}")
        transcript = poll_response.json()
        status = transcript.get("status")

        if status == "completed":
            utterances = [
                {
                    "speaker": u.get("speaker"),
                    "start_ms": u.get("start"),
                    "end_ms": u.get("end"),
                    "text": u.get("text", "").strip(),
                }
                for u in (transcript.get("utterances") or [])
                if str(u.get("text", "")).strip()
            ]
            segments = [
                {
                    "start": float((u.get("start") or 0) / 1000.0),
                    "end": float((u.get("end") or 0) / 1000.0),
                    "text": u.get("text", "").strip(),
                    "speaker": u.get("speaker"),
                }
                for u in (transcript.get("utterances") or [])
                if str(u.get("text", "")).strip()
            ]
            # Fallback for edge cases where utterances are absent.
            if not segments:
                segments = [
                    {
                        "start": float((w.get("start") or 0) / 1000.0),
                        "end": float((w.get("end") or 0) / 1000.0),
                        "text": w.get("text", "").strip(),
                        "speaker": None,
                    }
                    for w in (transcript.get("words") or [])
                    if str(w.get("text", "")).strip()
                ]
            full_text = (transcript.get("text") or "").strip()
            transcription_with_timestamps = "\n".join(
                f"[{format_timestamp(s['start'])} - {format_timestamp(s['end'])}] "
                + (f"Speaker {s['speaker']}: " if s.get("speaker") is not None else "")
                + s["text"]
                for s in segments
                if s.get("text")
            )
            return {
                "status": "completed",
                "file_path": str(path),
                "transcript_id": transcript_id,
                # Whisper-compatible contract used by runner.py
                "language": transcript.get("language_code") or "unknown",
                "duration": float(transcript.get("audio_duration") or 0.0),
                "transcription": full_text,
                "segments": segments,
                "transcription_with_timestamps": transcription_with_timestamps,
                # Extra AssemblyAI-specific fields (safe for current pipeline)
                "speaker_labels": utterances,
                "text": full_text,
                "raw": transcript,
            }

        if status == "error":
            raise RuntimeError(f"Transcription failed: {transcript.get('error', 'Unknown error')}")

        time.sleep(max(1, int(poll_interval_seconds)))
