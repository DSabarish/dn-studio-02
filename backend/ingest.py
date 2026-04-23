from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from backend.gcs_client import get_gcs_client

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
    content: bytes | None = None
    source_uri: str = ""
    content_loader: Callable[[], bytes] | None = None

    @property
    def is_text(self) -> bool:
        return self.suffix in TEXT_SUFFIXES

    @property
    def is_json(self) -> bool:
        return self.suffix == ".json"

    @property
    def is_media(self) -> bool:
        return self.suffix in MEDIA_SUFFIXES

    def load_content(self) -> bytes:
        if self.content is None:
            if self.content_loader is None:
                raise ValueError(f"No content or content_loader for input: {self.name}")
            self.content = self.content_loader()
        return self.content

    def clear_content(self) -> None:
        self.content = None


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
    client = get_gcs_client()
    items: list[MeetingInput] = []
    for idx, uri in enumerate(uris, start=1):
        bucket_name, blob_name = parse_gcs_uri(uri)
        file_name = Path(blob_name).name or "gcs_input"
        suffix = Path(file_name).suffix.lower()
        if not suffix:
            raise ValueError(f"Unsupported GCS object (missing extension): {uri}")
        logger.info(
            "Queued GCS object for lazy download | index=%s/%s | bucket=%s | object=%s",
            idx,
            len(uris),
            bucket_name,
            blob_name,
        )

        def _build_loader(bucket: str, obj: str, position: int, total_count: int):
            def _loader() -> bytes:
                logger.info(
                    "Downloading GCS object now | index=%s/%s | bucket=%s | object=%s",
                    position,
                    total_count,
                    bucket,
                    obj,
                )
                return client.bucket(bucket).blob(obj).download_as_bytes()

            return _loader

        items.append(
            MeetingInput(
                name=file_name,
                suffix=suffix,
                source_uri=uri,
                content_loader=_build_loader(bucket_name, blob_name, idx, len(uris)),
            )
        )
    logger.info("Loaded GCS meeting inputs | loaded_count=%s", len(items))
    return items


def load_uploaded_meeting_inputs(uploaded_files) -> list[MeetingInput]:
    items: list[MeetingInput] = []
    files = uploaded_files or []
    for idx, uf in enumerate(files, start=1):
        name = Path(uf.name).name
        logger.info("Loaded uploaded meeting input in order | index=%s/%s | name=%s", idx, len(files), name)

        def _build_loader(uploaded_file, position: int, total_count: int, file_name: str):
            def _loader() -> bytes:
                logger.info(
                    "Reading uploaded input now | index=%s/%s | name=%s",
                    position,
                    total_count,
                    file_name,
                )
                uploaded_file.seek(0)
                return uploaded_file.read()

            return _loader

        items.append(
            MeetingInput(
                name=name,
                suffix=Path(name).suffix.lower(),
                content_loader=_build_loader(uf, idx, len(files), name),
            )
        )
    logger.info("Loaded uploaded meeting inputs | loaded_count=%s", len(items))
    return items
