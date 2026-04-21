from __future__ import annotations

import io
import json
import tempfile
import zipfile
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from backend.ingest import MEDIA_SUFFIXES, MeetingInput

logger = logging.getLogger("dn_studio.runner")


@dataclass
class MeetingProcessResult:
    outputs: list[dict]
    meeting_records: list[dict]
    errors: list[str]


def sanitize_stem(filename: str) -> str:
    stem = Path(filename).stem.strip()
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stem)
    return safe or "transcript"


def format_timestamp(seconds) -> str:
    if seconds is None:
        seconds = 0
    total_ms = int(max(float(seconds), 0) * 1000)
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    secs = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def build_transcription_json_payload(source_video: str, language: str, duration: float, segments, file_name: str):
    transcript_entries = []
    for segment in segments or []:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        transcript_entries.append(
            {
                "start_time": format_timestamp(segment.get("start", 0)),
                "end_time": format_timestamp(segment.get("end", 0)),
                "speaker": "unknown",
                "text": text,
            }
        )
    return {
        "file_metadata": {
            "file_name": file_name,
            "source_video": source_video,
            "language": language or "unknown",
            "duration_seconds": float(duration or 0),
        },
        "transcript": transcript_entries,
    }


def build_zip(outputs):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in outputs:
            zf.writestr(item["json_name"], item["json_text"])
    zip_buffer.seek(0)
    return zip_buffer


def save_bytes_to_folder(file_name: str, content: bytes, target_folder: Path) -> Path:
    target_folder.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file_name).name
    path = target_folder / safe_name
    path.write_bytes(content)
    return path


def _stage_media_temp_file(item: MeetingInput) -> str:
    suffix = item.suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(item.content)
        return temp_file.name


def _process_media_item(item: MeetingInput, transcribe_fn):
    temp_path = _stage_media_temp_file(item)
    try:
        result = transcribe_fn(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return {
        "name": item.name,
        "language": result.get("language", "unknown"),
        "duration": result.get("duration", 0),
        "segments": result.get("segments", []),
        "source_uri": item.source_uri,
    }


def process_meetings(
    meeting_inputs: list[MeetingInput],
    session_base: Path,
    transcribe_fn,
    meeting_dates: dict[int, date],
    log,
    progress,
) -> MeetingProcessResult:
    logger.info("process_meetings started | input_count=%s", len(meeting_inputs))
    transcripts_dir = session_base / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[dict] = []
    errors: list[str] = []
    meeting_records: list[dict] = []
    media_items: list[tuple[int, MeetingInput]] = []
    total = len(meeting_inputs)

    for idx, item in enumerate(meeting_inputs, start=1):
        label = item.source_uri or item.name
        log(f"Preparing {idx}/{total}: `{label}`")
        logger.info(
            "Preparing meeting input | index=%s | name=%s | suffix=%s | source_uri=%s",
            idx,
            item.name,
            item.suffix,
            item.source_uri or "-",
        )
        progress(idx / max(total, 1))
        try:
            meeting_date_val = meeting_dates.get(idx, date.today())
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if item.suffix == ".txt":
                transcript_text = item.content.decode("utf-8", errors="ignore").strip()
                json_name = f"{sanitize_stem(item.name)}_{timestamp}.json"
                transcript_payload = {
                    "file_metadata": {
                        "file_name": json_name,
                        "source_video": item.name,
                        "language": "unknown",
                        "duration_seconds": 0.0,
                    },
                    "transcript": (
                        [
                            {
                                "start_time": "00:00:00.000",
                                "end_time": "00:00:00.000",
                                "speaker": "unknown",
                                "text": transcript_text,
                            }
                        ]
                        if transcript_text
                        else []
                    ),
                }
                json_body = json.dumps(transcript_payload, ensure_ascii=False, indent=2)
                json_path = transcripts_dir / json_name
                json_path.write_text(json_body, encoding="utf-8")
                outputs.append({"name": item.name, "language": "unknown", "duration": 0, "json_text": json_body, "json_name": json_name})
                meeting_records.append(
                    {"meeting_number": idx, "meeting_date": str(meeting_date_val), "transcript_path": str(json_path)}
                )
            elif item.suffix == ".json":
                json_path = save_bytes_to_folder(item.name, item.content, transcripts_dir)
                json_body = json_path.read_text(encoding="utf-8")
                json.loads(json_body)
                outputs.append({"name": item.name, "language": "unknown", "duration": 0, "json_text": json_body, "json_name": json_path.name})
                meeting_records.append(
                    {"meeting_number": idx, "meeting_date": str(meeting_date_val), "transcript_path": str(json_path)}
                )
            elif item.suffix in MEDIA_SUFFIXES:
                logger.info("Queued media for transcription | name=%s", item.name)
                media_items.append((idx, item))
            else:
                logger.warning("Unsupported file type skipped | name=%s | suffix=%s", item.name, item.suffix)
                errors.append(f"{item.name}: unsupported file type `{item.suffix}`")
        except Exception as exc:
            logger.exception("Failed to prepare input | name=%s", item.name)
            errors.append(f"{item.name}: {exc}")

    if media_items:
        logger.info(
            "Starting media transcription batch | media_count=%s | mode=sequential",
            len(media_items),
        )
        log(f"Transcribing {len(media_items)} media file(s) sequentially…")
        completed = 0
        for meeting_idx, item in media_items:
            completed += 1
            log(f"Transcribing {completed}/{len(media_items)}: `{item.name}`")
            try:
                processed = _process_media_item(item, transcribe_fn)
            except Exception as exc:
                logger.exception("Transcription failed | name=%s", item.name)
                errors.append(f"{item.name}: {exc}")
                continue
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_name = f"{sanitize_stem(item.name)}_{timestamp}.json"
            payload = build_transcription_json_payload(
                source_video=item.name,
                language=processed["language"],
                duration=processed["duration"],
                segments=processed.get("segments", []),
                file_name=json_name,
            )
            json_body = json.dumps(payload, ensure_ascii=False, indent=2)
            json_path = transcripts_dir / json_name
            json_path.write_text(json_body, encoding="utf-8")
            processed["json_name"] = json_name
            processed["json_text"] = json_body
            outputs.append(processed)
            meeting_date_val = meeting_dates.get(meeting_idx, date.today())
            meeting_records.append(
                {"meeting_number": meeting_idx, "meeting_date": str(meeting_date_val), "transcript_path": str(json_path)}
            )
            progress((total + completed) / max(total + len(media_items), 1))
            logger.info(
                "Transcription completed | name=%s | duration_seconds=%.3f | segments=%s",
                item.name,
                float(processed.get("duration", 0) or 0),
                len(processed.get("segments", []) or []),
            )

    meeting_records.sort(key=lambda x: x["meeting_number"])
    progress(1.0)
    logger.info(
        "process_meetings finished | outputs=%s | meeting_records=%s | errors=%s",
        len(outputs),
        len(meeting_records),
        len(errors),
    )
    return MeetingProcessResult(outputs=outputs, meeting_records=meeting_records, errors=errors)
