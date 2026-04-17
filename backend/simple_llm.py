from pathlib import Path

from google import genai
from google.genai import types

PROJECT_ID = "dn-studio-01"
LOCATION = "asia-south1"
MODEL_NAME = "gemini-2.5-flash"


def _strip_markdown_json_fence(text: str) -> str:
    """If the model wraps JSON in ```json ... ```, remove it (plain `generate_content` often returns this)."""
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    first_nl = t.find("\n")
    if first_nl == -1:
        return t
    body = t[first_nl + 1 :]
    body = body.rstrip()
    if body.endswith("```"):
        body = body[: -3].rstrip()
    return body


def run_prompt_text(
    prompt_text: str,
    temperature: float = 0.2,
    max_output_tokens: int = 8192,
) -> str:
    """Match minimal Vertex `generate_content` usage (same as `test.py`): plain string `contents`, no forced MIME type."""
    client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
    prompt = (prompt_text or "").strip()
    if not prompt:
        raise ValueError("Prompt is empty.")

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=float(temperature),
            max_output_tokens=int(max_output_tokens),
        ),
    )

    response_text = _strip_markdown_json_fence(response.text or "")
    if not response_text:
        raise ValueError("Model returned an empty response.")

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
