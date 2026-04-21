"""Minimal Vertex call smoke test (same style as backend.simple_llm)."""

import json
import sys
from pathlib import Path

from google import genai

from backend.simple_llm import _strip_markdown_json_fence

PROJECT_ID = "dn-studio-01"
LOCATION = "asia-south1"

# Default to a known populate prompt under run/; override with argv[1].
_default = Path("run") / "run_061" / "debug-prompt-populate-content.md"
prompt_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default
prompt = prompt_path.read_text(encoding="utf-8", errors="ignore")

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
model_name = "gemini-2.5-flash"
response = client.models.generate_content(model=model_name, contents=prompt)

raw = response.text or ""
normalized = _strip_markdown_json_fence(raw)
out_path = Path("test_vertex_out.json")
out_path.write_text(normalized, encoding="utf-8")

try:
    json.loads(normalized)
    ok = "valid JSON"
except json.JSONDecodeError as exc:
    ok = f"not valid JSON: {exc}"

# Avoid UnicodeEncodeError on Windows cp1252 consoles
print(f"Wrote {out_path.resolve()} ({len(normalized)} chars after fence strip, {ok})")