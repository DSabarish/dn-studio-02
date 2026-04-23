from __future__ import annotations

import logging
import threading
from pathlib import Path

from google import genai
from google.genai import types

from backend.helper import strip_markdown_json_fence
from config import config

PROJECT_ID = config.GEMINI_PROJECT_ID
LOCATION = config.GEMINI_LOCATION
MODEL_NAME = config.GEMINI_MODEL
logger = logging.getLogger("dn_studio.llm_client")
_client_lock = threading.Lock()
_client: genai.Client | None = None

DEFAULT_IMAGE_PROMPT = (
    "Provide a concise description of the provided content. "
    "Return 2-4 short bullet points based only on observable details."
)
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _get_client() -> genai.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
        return _client


def _reset_client() -> genai.Client:
    global _client
    with _client_lock:
        _client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
        return _client


def _is_closed_client_error(exc: Exception) -> bool:
    return "client has been closed" in str(exc).lower()


def _response_text(response) -> str:
    text = strip_markdown_json_fence(getattr(response, "text", "") or "").strip()
    if text:
        return text
    chunks = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(part_text.strip())
    return "\n".join(c for c in chunks if c).strip()


def run_prompt_text(
    prompt_text: str,
    temperature: float = 0.2,
    max_output_tokens: int = 12000,
) -> str:
    prompt = (prompt_text or "").strip()
    if not prompt:
        raise ValueError("Prompt is empty.")

    config_obj = types.GenerateContentConfig(
        temperature=float(temperature),
        max_output_tokens=int(max_output_tokens),
    )
    try:
        response = _get_client().models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=config_obj,
        )
    except RuntimeError as exc:
        if not _is_closed_client_error(exc):
            raise
        logger.warning("Gemini client was closed; reinitializing and retrying once.")
        response = _reset_client().models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=config_obj,
        )
    response_text = _response_text(response)
    if not response_text:
        raise ValueError("Model returned an empty response.")
    logger.info(
        "LLM call completed | model=%s | prompt_chars=%s | response_chars=%s | temperature=%s | max_output_tokens=%s",
        MODEL_NAME,
        len(prompt),
        len(response_text),
        float(temperature),
        int(max_output_tokens),
    )
    return response_text


def run_prompt_file(
    prompt_path: Path,
    temperature: float = 0.2,
    max_output_tokens: int = 30000,
) -> str:
    prompt_text = Path(prompt_path).read_text(encoding="utf-8", errors="ignore").strip()
    if not prompt_text:
        raise ValueError(f"Prompt file is empty: {prompt_path}")
    return run_prompt_text(
        prompt_text=prompt_text,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def gemini_call(image_path: Path, prompt: str = DEFAULT_IMAGE_PROMPT) -> str:
    path = Path(image_path)
    mime_type = _MIME_TYPES.get(path.suffix.lower())
    if mime_type is None:
        raise ValueError(f"Unsupported image type: {path.suffix}")

    final_prompt = (prompt or DEFAULT_IMAGE_PROMPT).strip()
    config_obj = types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=300,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    contents = [
        final_prompt,
        types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type),
    ]
    try:
        response = _get_client().models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=config_obj,
        )
    except RuntimeError as exc:
        if not _is_closed_client_error(exc):
            raise
        logger.warning("Gemini client was closed; reinitializing and retrying once.")
        response = _reset_client().models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=config_obj,
        )
    return _response_text(response)
