from __future__ import annotations

import json

from backend.helper import strip_markdown_json_fence


def loads_json_document(label: str, text: str):
    """Parse JSON with a clear, source-specific error message."""
    cleaned = strip_markdown_json_fence((text or "").strip())
    if not cleaned:
        raise ValueError(f"{label}: content is empty.")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        pos = getattr(exc, "pos", None) or 0
        start = max(0, pos - 100)
        end = min(len(cleaned), pos + 100)
        snippet = cleaned[start:end].replace("\n", "\\n")
        raise ValueError(
            f"{label}: invalid JSON — {exc.msg} at line {exc.lineno}, column {exc.colno} "
            f"(char {pos}). Snippet: …{snippet}…"
        ) from exc


def normalize_json_text(raw_text: str) -> str:
    """
    Normalize model output to valid pretty-printed JSON.
    Handles plain JSON, fenced JSON, and first decodable fragment in noisy text.
    """
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Empty JSON text.")
    decoder = json.JSONDecoder()

    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        pass

    if "```" in text:
        segments = text.split("```")
        for seg in segments:
            candidate = seg.strip()
            if not candidate:
                continue
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                obj = json.loads(candidate)
                return json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                for i, ch in enumerate(candidate):
                    if ch not in "{[":
                        continue
                    try:
                        obj, _ = decoder.raw_decode(candidate, idx=i)
                        return json.dumps(obj, ensure_ascii=False, indent=2)
                    except Exception:
                        continue

    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _ = decoder.raw_decode(text, idx=i)
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            continue

    raise ValueError("Unable to extract valid JSON from model output (model likely returned malformed JSON).")
