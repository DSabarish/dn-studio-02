from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from urllib.parse import urlparse

from google.cloud import storage

logger = logging.getLogger("dn_studio.ingest")


MEDIA_SUFFIXES = {
    ".mp3",
    ".wav",
    ".m4a",
    ".ogg",
    ".flac",
    ".aac",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
}
TEXT_SUFFIXES = {".txt", ".json"}


@dataclass
class MeetingInput:
    name: str
    suffix: str
    content: bytes
    source_uri: str = ""

    @property
    def is_text(self) -> bool:
        return self.suffix in TEXT_SUFFIXES

    @property
    def is_json(self) -> bool:
        return self.suffix == ".json"

    @property
    def is_media(self) -> bool:
        return self.suffix in MEDIA_SUFFIXES


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri.strip())
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid GCS URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def parse_gcs_uri_lines(raw_text: str) -> list[str]:
    return [line.strip() for line in (raw_text or "").splitlines() if line.strip()]


def load_gcs_meeting_inputs(raw_text: str) -> list[MeetingInput]:
    uris = parse_gcs_uri_lines(raw_text)
    if not uris:
        return []

    logger.info("Loading meeting inputs from GCS | uri_count=%s", len(uris))
    client = storage.Client()
    items: list[MeetingInput] = []
    for uri in uris:
        bucket_name, blob_name = parse_gcs_uri(uri)
        logger.info("Downloading GCS object | bucket=%s | object=%s", bucket_name, blob_name)
        blob = client.bucket(bucket_name).blob(blob_name)
        payload = blob.download_as_bytes()
        file_name = Path(blob_name).name or "gcs_input"
        suffix = Path(file_name).suffix.lower()
        if not suffix:
            raise ValueError(f"Unsupported GCS object (missing extension): {uri}")
        items.append(MeetingInput(name=file_name, suffix=suffix, content=payload, source_uri=uri))
    logger.info("Loaded GCS meeting inputs | loaded_count=%s", len(items))
    return items


def load_uploaded_meeting_inputs(uploaded_files) -> list[MeetingInput]:
    items: list[MeetingInput] = []
    for uf in uploaded_files or []:
        uf.seek(0)
        content = uf.read()
        name = Path(uf.name).name
        items.append(MeetingInput(name=name, suffix=Path(name).suffix.lower(), content=content))
    logger.info("Loaded uploaded meeting inputs | loaded_count=%s", len(items))
    return items
