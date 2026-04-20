import json
from datetime import date
from pathlib import Path


PROMPT_TEMPLATE_PATH = Path("prompts") / "bpd" / "p1_schema.md"


def _strip_markdown_json_fence(text: str) -> str:
    """Remove optional ```json ... ``` wrapper (common when pasting model output)."""
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


def _loads_json_document(label: str, text: str):
    """Parse JSON with a clear error naming the source (schema vs meeting file, etc.)."""
    cleaned = _strip_markdown_json_fence((text or "").strip())
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


POPULATE_TEMPLATE_PATH = Path("prompts") / "bpd" / "p2_populate.md"
POPULATE_PROMPT_FILENAME = "debug-prompt-populate-content.md"
POPULATE_PROMPT_LEGACY_FILENAME = "final-content-populate-prompt.md"


def _normalize_h1_headers(raw_h1_headers):
    headers = [h.strip() for h in (raw_h1_headers or []) if h and h.strip()]
    return headers


def _normalize_meetings(meetings):
    normalized = []
    for idx, meeting in enumerate(meetings or [], start=1):
        transcript_path_obj = Path(meeting.get("transcript_path", ""))
        meeting_date = str(meeting.get("meeting_date", "")).strip() or str(date.today())
        normalized.append(
            {
                "meeting_number": idx,
                "meeting_date": meeting_date,
                "transcript_json_path": str(transcript_path_obj),
                "transcript_json": _read_transcript_json(transcript_path_obj),
            }
        )
    return normalized


def _next_run_dir(base_run_dir: Path) -> Path:
    base_run_dir.mkdir(parents=True, exist_ok=True)
    existing = []
    for child in base_run_dir.iterdir():
        if child.is_dir() and child.name.startswith("run_"):
            suffix = child.name.replace("run_", "", 1)
            if suffix.isdigit():
                existing.append(int(suffix))
    next_idx = (max(existing) + 1) if existing else 1
    run_dir = base_run_dir / f"run_{next_idx:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def create_new_run_folder(run_base_dir: Path) -> Path:
    """Allocate the next `run/run_NNN` folder (used as the single session output directory)."""
    return _next_run_dir(Path(run_base_dir))


def _read_template(template_path: Path) -> str:
    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


def _read_transcript_json(transcript_path: Path):
    path = Path(transcript_path)
    if not path.exists():
        return {"error": f"Transcript file not found: {path}"}

    if path.suffix.lower() == ".json":
        try:
            raw = path.read_text(encoding="utf-8")
            return _loads_json_document(f"Transcript JSON ({path.name})", raw)
        except ValueError as exc:
            return {"error": str(exc)}

    # Fallback for non-json transcript files.
    try:
        return {"raw_text": path.read_text(encoding="utf-8", errors="ignore")}
    except Exception as exc:
        return {"error": f"Could not read transcript file: {exc}"}


def list_bpd_run_dirs(run_base_dir: Path) -> list[Path]:
    """Sorted `run_NNN` directories under `run_base_dir` (numeric suffix)."""
    base = Path(run_base_dir)
    if not base.is_dir():
        return []
    found = []
    for child in base.iterdir():
        if not child.is_dir() or not child.name.startswith("run_"):
            continue
        suffix = child.name.replace("run_", "", 1)
        if suffix.isdigit():
            found.append((int(suffix), child))
    return [p for _, p in sorted(found)]


def _normalize_schema_json(schema_json):
    if isinstance(schema_json, str):
        parsed = _loads_json_document("Schema JSON (BPD design / r1)", schema_json)
    else:
        parsed = schema_json
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def build_bpd_schema_prompt(
    business_context: str,
    h1_headers,
    meetings,
    run_base_dir: Path,
    run_dir: Path | None = None,
    template_path: Path = PROMPT_TEMPLATE_PATH,
):
    template = _read_template(template_path)
    h1_list = _normalize_h1_headers(h1_headers)
    normalized_meetings = _normalize_meetings(meetings)

    meeting_input_json = json.dumps(normalized_meetings, indent=2)
    h1_json = json.dumps(h1_list, indent=2)

    prompt = template
    prompt = prompt.replace("{{BUSINESS_CONTEXT}}", (business_context or "").strip())
    prompt = prompt.replace("{{APPENDED_MEETING_INPUT}}", meeting_input_json)
    prompt = prompt.replace("{{H1_SECTIONS}}", h1_json)

    if run_dir is None:
        run_dir = _next_run_dir(Path(run_base_dir))
    else:
        run_dir = Path(run_dir).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "debug-prompt-schema.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    meeting_json_path = run_dir / "meeting-input.json"
    meeting_json_path.write_text(meeting_input_json, encoding="utf-8")

    return {
        "prompt": prompt,
        "run_dir": str(run_dir),
        "prompt_path": str(prompt_path),
        "meeting_json_path": str(meeting_json_path),
        "meeting_input": normalized_meetings,
    }


def _fill_bpd_populate_template(
    template: str,
    business_context: str,
    schema_json_text: str,
    meeting_input_json: str,
    context_text: str,
) -> str:
    prompt = template
    prompt = prompt.replace("{{BUSINESS_CONTEXT}}", (business_context or "").strip())
    prompt = prompt.replace("{{SCHEMA_JSON}}", schema_json_text)
    prompt = prompt.replace("{{APPENDED_MEETING_INPUT}}", meeting_input_json)
    prompt = prompt.replace("{{CONTEXT_INPUT_MD}}", context_text)
    return prompt


def _write_populate_prompt_files(run_dir: Path, prompt: str) -> Path:
    """
    Write populate prompt using both current and legacy filenames.
    Returns the canonical (current) prompt path.
    """
    prompt_path = run_dir / POPULATE_PROMPT_FILENAME
    prompt_path.write_text(prompt, encoding="utf-8")
    legacy_prompt_path = run_dir / POPULATE_PROMPT_LEGACY_FILENAME
    legacy_prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def build_bpd_pop_prompt(
    business_context: str,
    schema_json,
    meetings,
    run_base_dir: Path,
    run_dir: Path | None = None,
    context_markdown: str = "",
    template_path: Path = POPULATE_TEMPLATE_PATH,
):
    """Fill `prompts/bpd/p2_populate.md` including `{{CONTEXT_INPUT_MD}}` from `run/context.md` when provided."""
    template = _read_template(template_path)
    normalized_meetings = _normalize_meetings(meetings)

    meeting_input_json = json.dumps(normalized_meetings, ensure_ascii=False, indent=2)
    schema_json_text = _normalize_schema_json(schema_json)
    context_text = (context_markdown or "").strip() or (
        "(No context.md available — run Context Builder or add run/context.md.)"
    )

    prompt = _fill_bpd_populate_template(
        template,
        business_context,
        schema_json_text,
        meeting_input_json,
        context_text,
    )

    if run_dir is None:
        run_dir = _next_run_dir(Path(run_base_dir))
    else:
        run_dir = Path(run_dir).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = _write_populate_prompt_files(run_dir, prompt)

    meeting_json_path = run_dir / "meeting-input.json"
    meeting_json_path.write_text(meeting_input_json, encoding="utf-8")

    schema_json_path = run_dir / "schema-input.json"
    schema_json_path.write_text(schema_json_text, encoding="utf-8")

    return {
        "prompt": prompt,
        "run_dir": str(run_dir),
        "prompt_path": str(prompt_path),
        "meeting_json_path": str(meeting_json_path),
        "schema_json_path": str(schema_json_path),
        "meeting_input": normalized_meetings,
        "input_source": "live_session",
    }


def build_bpd_pop_prompt_from_run_folder(
    run_dir: Path,
    business_context: str,
    context_markdown: str = "",
    template_path: Path = POPULATE_TEMPLATE_PATH,
):
    """
    Build populate prompt using only files inside `run_dir`:
    - `meeting-input.json` (required)
    - `schema-input.json` or `r1_schema.json` (required)
    Does not re-read transcripts from disk; uses embedded JSON in meeting-input as-is.
    Writes `debug-prompt-populate-content.md` into the same `run_dir`.
    """
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Not a directory: {run_dir}")

    meeting_path = run_dir / "meeting-input.json"
    if not meeting_path.is_file():
        raise FileNotFoundError(f"Missing meeting-input.json in {run_dir}")

    meeting_raw = meeting_path.read_text(encoding="utf-8")
    parsed_meetings = _loads_json_document(f"meeting-input.json ({meeting_path})", meeting_raw)
    meeting_input_json = json.dumps(parsed_meetings, ensure_ascii=False, indent=2)

    schema_path = run_dir / "schema-input.json"
    if not schema_path.is_file():
        schema_path = run_dir / "r1_schema.json"
    if not schema_path.is_file():
        raise FileNotFoundError(
            f"Missing schema file: need schema-input.json or r1_schema.json in {run_dir}"
        )

    schema_json_text = _normalize_schema_json(
        schema_path.read_text(encoding="utf-8", errors="replace")
    )
    context_text = (context_markdown or "").strip() or (
        "(No context.md available in this run folder or at run/context.md.)"
    )

    template = _read_template(template_path)
    prompt = _fill_bpd_populate_template(
        template,
        business_context,
        schema_json_text,
        meeting_input_json,
        context_text,
    )

    prompt_path = _write_populate_prompt_files(run_dir, prompt)

    return {
        "prompt": prompt,
        "run_dir": str(run_dir),
        "prompt_path": str(prompt_path),
        "meeting_json_path": str(meeting_path),
        "schema_json_path": str(schema_path),
        "meeting_input": parsed_meetings,
        "input_source": "run_folder",
    }
