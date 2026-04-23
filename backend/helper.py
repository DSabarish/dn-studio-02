from __future__ import annotations


def strip_markdown_json_fence(text: str) -> str:
    """Remove optional ```json ... ``` wrapper from model output."""
    cleaned = (text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    first_newline = cleaned.find("\n")
    if first_newline == -1:
        return cleaned
    body = cleaned[first_newline + 1 :].rstrip()
    if body.endswith("```"):
        body = body[:-3].rstrip()
    return body


def format_timestamp(seconds: float | int | None) -> str:
    """Format a timestamp value as HH:MM:SS.mmm."""
    if seconds is None:
        seconds = 0
    total_ms = int(max(float(seconds), 0.0) * 1000)
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    secs = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
