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
from backend.media_prep import prepare_media_for_transcription

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
        speaker = segment.get("speaker")
        transcript_entries.append(
            {
                "start_time": format_timestamp(segment.get("start", 0)),
                "end_time": format_timestamp(segment.get("end", 0)),
                "speaker": str(speaker) if speaker is not None else "unknown",
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
        temp_file.write(item.load_content())
        return temp_file.name


def _process_media_item(item: MeetingInput, transcribe_fn, transcription_engine: str, log):
    temp_path = _stage_media_temp_file(item)
    prepared_path = temp_path
    prepared_is_temp = False
    try:
        prepared_path, prepared_is_temp, prep_note = prepare_media_for_transcription(
            source_path=temp_path,
            transcription_engine=transcription_engine,
        )
        log(f"Audio prep: {prep_note}")
        if prepared_is_temp:
            log(f"Audio prep output: `{Path(prepared_path).name}`")
        result = transcribe_fn(prepared_path)
    finally:
        if prepared_is_temp:
            Path(prepared_path).unlink(missing_ok=True)
            log(f"Temporary prepared audio deleted: `{Path(prepared_path).name}`")
        Path(temp_path).unlink(missing_ok=True)
        log(f"Temporary staged media deleted: `{Path(temp_path).name}`")
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
    transcription_engine: str,
    meeting_dates: dict[int, date],
    log,
    progress,
) -> MeetingProcessResult:
    logger.info("process_meetings started | input_count=%s", len(meeting_inputs))
    log("Input processing mode: strict sequential order (1 -> 2 -> 3 -> ... -> n)")
    transcripts_dir = session_base / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[dict] = []
    errors: list[str] = []
    meeting_records: list[dict] = []
    total = len(meeting_inputs)

    for idx, item in enumerate(meeting_inputs, start=1):
        label = item.source_uri or item.name
        log(f"Preparing {idx}/{total} in order: `{label}`")
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
                transcript_text = item.load_content().decode("utf-8", errors="ignore").strip()
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
                log(f"Saved transcript JSON for {idx}/{total}: `{json_path.name}`")
                outputs.append({"name": item.name, "language": "unknown", "duration": 0, "json_text": json_body, "json_name": json_name})
                meeting_records.append(
                    {"meeting_number": idx, "meeting_date": str(meeting_date_val), "transcript_path": str(json_path)}
                )
                item.clear_content()
            elif item.suffix == ".json":
                json_path = save_bytes_to_folder(item.name, item.load_content(), transcripts_dir)
                json_body = json_path.read_text(encoding="utf-8")
                json.loads(json_body)
                log(f"Saved transcript JSON for {idx}/{total}: `{json_path.name}`")
                outputs.append({"name": item.name, "language": "unknown", "duration": 0, "json_text": json_body, "json_name": json_path.name})
                meeting_records.append(
                    {"meeting_number": idx, "meeting_date": str(meeting_date_val), "transcript_path": str(json_path)}
                )
                item.clear_content()
            elif item.suffix in MEDIA_SUFFIXES:
                logger.info("Starting sequential media processing | index=%s/%s | name=%s", idx, total, item.name)
                log(f"Transcribing media {idx}/{total} in sequence: `{item.name}`")
                processed = _process_media_item(item, transcribe_fn, transcription_engine, log)
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
                log(f"Saved transcript JSON for global item #{idx}: `{json_path.name}`")
                processed["json_name"] = json_name
                processed["json_text"] = json_body
                outputs.append(processed)
                meeting_records.append(
                    {"meeting_number": idx, "meeting_date": str(meeting_date_val), "transcript_path": str(json_path)}
                )
                logger.info(
                    "Transcription completed | index=%s/%s | name=%s | duration_seconds=%.3f | segments=%s",
                    idx,
                    total,
                    item.name,
                    float(processed.get("duration", 0) or 0),
                    len(processed.get("segments", []) or []),
                )
                item.clear_content()
                log(
                    f"Completed item #{idx}; media content released from memory. "
                    f"Current transcript count: {len(meeting_records)}"
                )
            else:
                logger.warning("Unsupported file type skipped | name=%s | suffix=%s", item.name, item.suffix)
                errors.append(f"{item.name}: unsupported file type `{item.suffix}`")
        except Exception as exc:
            logger.exception("Failed to prepare input | name=%s", item.name)
            errors.append(f"{item.name}: {exc}")
        finally:
            item.clear_content()
        progress(idx / max(total, 1))

    meeting_records.sort(key=lambda x: x["meeting_number"])
    log(f"Sequential processing complete. Total transcripts ready: {len(meeting_records)}")
    progress(1.0)
    logger.info(
        "process_meetings finished | outputs=%s | meeting_records=%s | errors=%s",
        len(outputs),
        len(meeting_records),
        len(errors),
    )
    return MeetingProcessResult(outputs=outputs, meeting_records=meeting_records, errors=errors)
