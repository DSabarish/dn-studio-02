import io
import importlib.util
import json
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from google import genai
from google.genai import types

MAX_FILE_SIZE_MB = 2048  # App-side safety limit per file (2 GB)
MAX_PARALLEL_WORKERS = 4
MAX_DOC_FILES = 20
RUN_DIR = Path(__file__).resolve().parent / "run"
TRANSCRIPTS_DIR = RUN_DIR / "transcripts"
DOCS_INPUT_DIR = RUN_DIR / "docs_input"
BPD_MODEL_NAME = "gemini-2.5-flash"
BPD_PROJECT_ID = "dn-studio-01"
BPD_LOCATION = "asia-south1"
DEFAULT_BPD_H1_HEADERS = "\n".join(
    [
        "Business Process Overview",
        "Business Process Design",
        "Business Process Flows",
        "Business Process Controls",
        "Business Process Impacts",
    ]
)


def _extract_first_json_object(text: str) -> str:
    """
    Best-effort extraction of the first valid JSON object from a text blob.
    Handles extra commentary or multiple code fences by scanning for the first
    parseable {...} block.
    """
    if not text:
        raise ValueError("Empty response when JSON was expected.")

    # Quick clean-up for common fenced formats.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    # If direct parse works, return.
    try:
        json.loads(cleaned)
        return cleaned
    except Exception:
        pass

    # Fallback: search between first '{' and successive '}' from the end.
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    while first != -1 and last != -1 and last > first:
        candidate = cleaned[first : last + 1]
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            last = cleaned.rfind("}", 0, last)

    raise ValueError("Could not extract valid JSON object from model response.")


def _generate_model_text(
    prompt_text: str,
    temperature: float,
    max_output_tokens: int,
) -> str:
    client = genai.Client(
        vertexai=True,
        project=BPD_PROJECT_ID,
        location=BPD_LOCATION,
    )
    response = client.models.generate_content(
        model=BPD_MODEL_NAME,
        contents=[(prompt_text or "").strip()],
        config=types.GenerateContentConfig(
            temperature=float(temperature),
            max_output_tokens=int(max_output_tokens),
            response_mime_type="application/json",
        ),
    )

    response_text = (response.text or "").strip()
    if response_text:
        return response_text

    chunks = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(part_text.strip())
    return "\n".join(c for c in chunks if c).strip()


def _repair_json_with_model(raw_text: str, max_output_tokens: int) -> str:
    repair_prompt = (
        "Convert the following into STRICT valid JSON only.\n"
        "No markdown, no prose, no code fences.\n\n"
        "CONTENT:\n"
        f"{raw_text}"
    )
    repaired_text = _generate_model_text(
        prompt_text=repair_prompt,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
    )
    if not repaired_text:
        raise ValueError("JSON repair step returned empty output.")
    return repaired_text


def load_transcription_module():
    module_path = Path(__file__).resolve().parent / "backend" / "transcriptions.py"
    spec = importlib.util.spec_from_file_location("text_transcriptions", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load transcription module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def load_context_builder_module():
    module_path = Path(__file__).resolve().parent / "backend" / "build_context.py"
    spec = importlib.util.spec_from_file_location("build_context", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load context builder module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_prompt_builder_module():
    module_path = Path(__file__).resolve().parent / "build_prompt.py"
    spec = importlib.util.spec_from_file_location("build_prompt", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load prompt builder module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sanitize_stem(filename: str) -> str:
    stem = Path(filename).stem.strip()
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stem)
    return safe or "transcript"

def stage_uploaded_file(uploaded_file):
    suffix = Path(uploaded_file.name).suffix or ".bin"

    file_size_bytes = getattr(uploaded_file, "size", None)
    if file_size_bytes is not None and file_size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(
            f"{uploaded_file.name} is larger than {MAX_FILE_SIZE_MB} MB."
        )

    uploaded_file.seek(0)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        # Stream upload to disk to avoid loading huge files fully in memory.
        while True:
            chunk = uploaded_file.read(1024 * 1024)  # 1 MB chunks
            if not chunk:
                break
            temp_file.write(chunk)
        temp_path = temp_file.name

    return uploaded_file.name, temp_path


def process_staged_file(file_name, temp_path, transcribe_fn):
    try:
        result = transcribe_fn(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)

    return {
        "name": file_name,
        "language": result.get("language", "unknown"),
        "duration": result.get("duration", 0),
        "segments": result.get("segments", []),
    }

def build_zip(outputs):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in outputs:
            zf.writestr(item["json_name"], item["json_text"])
    zip_buffer.seek(0)
    return zip_buffer


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


def build_transcription_json_payload(
    source_video: str,
    language: str,
    duration: float,
    segments,
    file_name: str,
):
    transcript_entries = []
    for segment in (segments or []):
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

def save_uploaded_to_folder(uploaded_file, target_folder: Path) -> Path:
    target_folder.mkdir(parents=True, exist_ok=True)
    safe_name = Path(uploaded_file.name).name
    target_path = target_folder / safe_name
    uploaded_file.seek(0)
    with target_path.open("wb") as out:
        while True:
            chunk = uploaded_file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return target_path


def generate_schema_json(
    prompt_text: str,
    temperature: float = 0.2,
    max_output_tokens: int = 8192,
) -> str:
    response_text = _generate_model_text(
        prompt_text=prompt_text,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
    if not response_text:
        raise ValueError(
            "Model returned an empty response when JSON was expected. "
            "Try reducing input size or increasing max output tokens."
        )
    try:
        json_payload = _extract_first_json_object(response_text)
        parsed = json.loads(json_payload)
    except Exception:
        repaired_text = _repair_json_with_model(response_text, max_output_tokens)
        json_payload = _extract_first_json_object(repaired_text)
        parsed = json.loads(json_payload)
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def generate_r2_populated_from_prompt(
    p2_prompt: str,
    appended_meeting,
    schema_json,
    business_context: str,
    context_input_md: str = "",
    temperature: float = 0.2,
    max_output_tokens: int = 30000,
) -> str:
    """
    Build populate prompt from explicit inputs and return pretty-printed JSON.
    Uses the same model call and JSON extraction/repair pattern as schema generation.
    """
    if isinstance(appended_meeting, str):
        appended_meeting_text = appended_meeting.strip()
        if not appended_meeting_text:
            raise ValueError("Meeting input cannot be empty.")
        try:
            appended_meeting_parsed = json.loads(appended_meeting_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid meeting input JSON: {exc}") from exc
    else:
        appended_meeting_parsed = appended_meeting
    appended_meeting_text = json.dumps(
        appended_meeting_parsed, ensure_ascii=False, indent=2
    )

    if isinstance(schema_json, str):
        schema_json_text_raw = schema_json.strip()
        if not schema_json_text_raw:
            raise ValueError("Schema JSON cannot be empty.")
        try:
            schema_parsed = json.loads(schema_json_text_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid schema JSON: {exc}") from exc
    else:
        schema_parsed = schema_json
    schema_json_text = json.dumps(schema_parsed, ensure_ascii=False, indent=2)

    prompt_text = (p2_prompt or "").strip()
    if not prompt_text:
        raise ValueError("p2_prompt cannot be empty.")
    prompt_text = prompt_text.replace("{{BUSINESS_CONTEXT}}", (business_context or "").strip())
    prompt_text = prompt_text.replace("{{SCHEMA_JSON}}", schema_json_text)
    prompt_text = prompt_text.replace("{{APPENDED_MEETING_INPUT}}", appended_meeting_text)
    prompt_text = prompt_text.replace("{{CONTEXT_INPUT_MD}}", (context_input_md or "").strip())

    response_text = _generate_model_text(
        prompt_text=prompt_text,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
    if not response_text:
        raise ValueError(
            "Model returned an empty response when JSON was expected. "
            "Try reducing input size or increasing max output tokens."
        )
    try:
        json_payload = _extract_first_json_object(response_text)
        parsed = json.loads(json_payload)
    except Exception:
        repaired_text = _repair_json_with_model(response_text, max_output_tokens)
        json_payload = _extract_first_json_object(repaired_text)
        parsed = json.loads(json_payload)
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def list_meeting_transcripts():
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    json_files = sorted(TRANSCRIPTS_DIR.glob("*.json"))
    txt_files = sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    return json_files if json_files else txt_files


# Streamlit UI #########################################################
st.set_page_config(page_title="Lite Transcription", layout="wide")
st.title("DN Studio")

if "outputs" not in st.session_state:
    st.session_state.outputs = []
if "meeting_records" not in st.session_state:
    st.session_state.meeting_records = []
if "meeting_dates" not in st.session_state:
    st.session_state.meeting_dates = {}
if "bpd_prompt_result" not in st.session_state:
    st.session_state.bpd_prompt_result = None
if "bpd_schema_json_output" not in st.session_state:
    st.session_state.bpd_schema_json_output = ""
if "bpd_business_context" not in st.session_state:
    st.session_state.bpd_business_context = ""
if "bpd_schema_temperature" not in st.session_state:
    st.session_state.bpd_schema_temperature = 0.2
if "bpd_schema_max_tokens" not in st.session_state:
    st.session_state.bpd_schema_max_tokens = 8192
if "bpd_r2_pop_temperature" not in st.session_state:
    st.session_state.bpd_r2_pop_temperature = 0.2
if "bpd_r2_pop_max_tokens" not in st.session_state:
    st.session_state.bpd_r2_pop_max_tokens = 30000
if "bpd_populate_prompt_result" not in st.session_state:
    st.session_state.bpd_populate_prompt_result = None
if "bpd_pop_schema_json_editable" not in st.session_state:
    st.session_state.bpd_pop_schema_json_editable = st.session_state.bpd_schema_json_output or ""
if "bpd_pop_business_context_editable" not in st.session_state:
    st.session_state.bpd_pop_business_context_editable = st.session_state.bpd_business_context or ""

left_col, schema_col, context_col = st.columns(3)

with left_col:
    with st.container(border=True):
        st.subheader("Meeting Recordings/Transcripts Uploader")
        uploaded_files = st.file_uploader(
            "Drag and drop audio/video files (or transcript .txt)",
            accept_multiple_files=True,
            type=["mp3", "wav", "m4a", "ogg", "flac", "aac", "mp4", "mov", "mkv", "webm", "avi", "txt", "json"],
            key="meeting_files_uploader",
        )
        st.info(f"Parallel transcription is enabled (up to {MAX_PARALLEL_WORKERS} files at once).")

        if uploaded_files:
            st.caption("Add meeting dates for uploaded recordings.")
            for idx, uploaded_file in enumerate(uploaded_files, start=1):
                key = f"meeting_date_upload_{uploaded_file.name}_{idx}"
                selected_date = st.date_input(
                    f"Meeting {idx} date - {uploaded_file.name}",
                    value=st.session_state.meeting_dates.get(key, date.today()),
                    key=key,
                )
                st.session_state.meeting_dates[key] = selected_date

        if st.button("Process Files", type="primary", disabled=not uploaded_files):
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
            transcription_module = load_transcription_module()
            transcribe_fn = transcription_module.transcribe
            outputs = []
            errors = []
            staged_files = []
            meeting_records = []

            progress = st.progress(0)
            status = st.empty()
            parallel_status = st.empty()

            total = len(uploaded_files or [])
            for idx, uploaded_file in enumerate(uploaded_files or [], start=1):
                status.write(f"Staging {idx}/{total}: `{uploaded_file.name}`")
                try:
                    suffix = Path(uploaded_file.name).suffix.lower()
                    if suffix == ".txt":
                        uploaded_file.seek(0)
                        transcript_text = uploaded_file.read().decode("utf-8", errors="ignore").strip()
                        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        json_name = f"{sanitize_stem(uploaded_file.name)}_{file_timestamp}.json"
                        transcript_payload = {
                            "file_metadata": {
                                "file_name": json_name,
                                "source_video": uploaded_file.name,
                                "language": "unknown",
                                "duration_seconds": 0.0,
                            },
                            "transcript": [
                                {
                                    "start_time": "00:00:00.000",
                                    "end_time": "00:00:00.000",
                                    "speaker": "unknown",
                                    "text": transcript_text,
                                }
                            ]
                            if transcript_text
                            else [],
                        }
                        json_body = json.dumps(transcript_payload, ensure_ascii=False, indent=2)
                        json_path = TRANSCRIPTS_DIR / json_name
                        json_path.write_text(json_body, encoding="utf-8")
                        outputs.append(
                            {
                                "name": uploaded_file.name,
                                "language": "unknown",
                                "duration": 0,
                                "json_text": json_body,
                                "json_name": json_name,
                            }
                        )
                        meeting_date_key = f"meeting_date_upload_{uploaded_file.name}_{idx}"
                        meeting_records.append(
                            {
                                "meeting_number": idx,
                                "meeting_date": str(
                                    st.session_state.meeting_dates.get(meeting_date_key, date.today())
                                ),
                                "transcript_path": str(json_path),
                            }
                        )
                    elif suffix == ".json":
                        # Accept an already-prepared transcript JSON as-is.
                        saved_json_path = save_uploaded_to_folder(uploaded_file, TRANSCRIPTS_DIR)
                        try:
                            json_body = saved_json_path.read_text(encoding="utf-8")
                            # Validate it's at least parseable JSON.
                            json.loads(json_body)
                        except Exception as exc:
                            raise ValueError(f"Invalid JSON transcript file: {exc}") from exc

                        outputs.append(
                            {
                                "name": uploaded_file.name,
                                "language": "unknown",
                                "duration": 0,
                                "json_text": json_body,
                                "json_name": saved_json_path.name,
                            }
                        )
                        meeting_date_key = f"meeting_date_upload_{uploaded_file.name}_{idx}"
                        meeting_records.append(
                            {
                                "meeting_number": idx,
                                "meeting_date": str(
                                    st.session_state.meeting_dates.get(meeting_date_key, date.today())
                                ),
                                "transcript_path": str(saved_json_path),
                            }
                        )
                    else:
                        staged_files.append(stage_uploaded_file(uploaded_file))
                except Exception as exc:
                    errors.append(f"{uploaded_file.name}: {exc}")
                progress.progress(idx / total)

            if staged_files:
                workers = max(1, min(MAX_PARALLEL_WORKERS, len(staged_files)))
                completed = 0
                parallel_status.info(
                    f"Parallel transcription running with {workers} worker(s) for {len(staged_files)} file(s)."
                )
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(process_staged_file, name, path, transcribe_fn): (name, idx)
                        for idx, (name, path) in enumerate(staged_files, start=1)
                    }
                    for future in as_completed(futures):
                        name, meeting_idx = futures[future]
                        completed += 1
                        status.write(f"Transcribing {completed}/{len(staged_files)}: `{name}`")
                        remaining = len(staged_files) - completed
                        parallel_status.info(
                            f"Parallel mode: {workers} worker(s) | Completed: {completed} | Remaining: {remaining}"
                        )
                        try:
                            processed = future.result()
                        except Exception as exc:
                            errors.append(f"{name}: {exc}")
                            continue

                        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        json_name = f"{sanitize_stem(name)}_{file_timestamp}.json"
                        transcription_payload = build_transcription_json_payload(
                            source_video=name,
                            language=processed["language"],
                            duration=processed["duration"],
                            segments=processed.get("segments", []),
                            file_name=json_name,
                        )
                        json_body = json.dumps(transcription_payload, ensure_ascii=False, indent=2)
                        processed["json_name"] = json_name
                        processed["json_text"] = json_body
                        json_path = TRANSCRIPTS_DIR / json_name
                        json_path.write_text(json_body, encoding="utf-8")
                        meeting_date_key = f"meeting_date_upload_{name}_{meeting_idx}"
                        meeting_records.append(
                            {
                                "meeting_number": meeting_idx,
                                "meeting_date": str(st.session_state.meeting_dates.get(meeting_date_key, date.today())),
                                "transcript_path": str(json_path),
                            }
                        )
                        outputs.append(processed)
                        progress.progress((total + completed) / (total + len(staged_files)))

            if errors:
                status.warning("Completed with some file errors.")
                parallel_status.warning("Parallel transcription finished with some errors.")
                for err in errors:
                    st.error(err)
            else:
                status.success("Transcription complete.")
                parallel_status.success("Parallel transcription finished successfully.")
            st.session_state.outputs = outputs
            st.session_state.meeting_records = sorted(
                meeting_records, key=lambda x: x["meeting_number"]
            )

        if st.session_state.outputs:
            st.subheader("Downloads")
            for item in st.session_state.outputs:
                st.download_button(
                    label=f"Download {item['json_name']}",
                    data=item["json_text"],
                    file_name=item["json_name"],
                    mime="application/json",
                )

            zip_data = build_zip(st.session_state.outputs)
            st.download_button(
                label="Download All as ZIP",
                data=zip_data,
                file_name=f"transcripts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                mime="application/zip",
            )

with schema_col:
    with st.container(border=True):
        st.subheader("BPD Schema Builder")

        document_type = st.radio(
            "Document Type",
            options=["BPD", "BRD", "MOM"],
            horizontal=True,
        )

        if document_type == "BPD":
            st.markdown("### BPD Schema Prompt Builder")
            business_context = st.text_area(
                "Business Context",
                placeholder="Enter business context used in BPD schema design...",
                height=120,
            )
            h1_headers_raw = st.text_area(
                "H1 Headers (one per line)",
                placeholder="Overview\nCurrent Process\nTo-Be Process\nControls",
                height=140,
                value=DEFAULT_BPD_H1_HEADERS,
            )

            if st.session_state.meeting_records:
                st.caption("Meeting inputs use dates and transcript JSON from uploaded recordings.")
                for meeting in st.session_state.meeting_records:
                    st.write(
                        f"Meeting {meeting['meeting_number']} | Date: {meeting['meeting_date']} | "
                        f"JSON: {Path(meeting['transcript_path']).name}"
                    )
            else:
                st.warning("Process meeting recordings first to prepare meeting JSON inputs.")

            if st.button("final-schema-prompt", type="primary"):
                h1_headers = [line.strip() for line in h1_headers_raw.splitlines() if line.strip()]
                if not h1_headers:
                    st.error("Please add at least one H1 header for BPD.")
                elif not st.session_state.meeting_records:
                    st.error("No meeting recordings processed yet.")
                else:
                    prompt_builder = load_prompt_builder_module()
                    prompt_result = prompt_builder.build_bpd_schema_prompt(
                        business_context=business_context,
                        h1_headers=h1_headers,
                        meetings=st.session_state.meeting_records,
                        run_base_dir=RUN_DIR,
                    )
                    st.session_state.bpd_prompt_result = prompt_result
                    st.session_state.bpd_business_context = business_context
                    st.session_state.bpd_pop_business_context_editable = business_context
                    st.session_state.bpd_schema_json_output = ""
                    st.success(f"Exported prompt schema: {prompt_result['prompt_path']}")
                    st.caption(f"Saved meeting json: {prompt_result['meeting_json_path']}")
                    st.download_button(
                        label="Download bpd-system-prompt-debugger.md",
                        data=prompt_result["prompt"],
                        file_name="bpd-system-prompt-debugger.md",
                        mime="text/markdown",
                    )

            st.caption("Schema generation settings")
            bpd_schema_temperature = st.number_input(
                "Temperature",
                min_value=0.0,
                max_value=2.0,
                value=float(st.session_state.bpd_schema_temperature),
                step=0.1,
                key="bpd_schema_temperature_input",
            )
            bpd_schema_max_tokens = st.number_input(
                "Max Output Tokens",
                min_value=256,
                max_value=65000,
                value=int(st.session_state.bpd_schema_max_tokens),
                step=256,
                key="bpd_schema_max_tokens_input",
            )
            st.session_state.bpd_schema_temperature = float(bpd_schema_temperature)
            st.session_state.bpd_schema_max_tokens = int(bpd_schema_max_tokens)

            can_build_schema = st.session_state.bpd_prompt_result is not None
            if st.button("Build Schema", disabled=not can_build_schema):
                try:
                    prompt_result = st.session_state.bpd_prompt_result or {}
                    generated_json = generate_schema_json(
                        prompt_result.get("prompt", ""),
                        temperature=st.session_state.bpd_schema_temperature,
                        max_output_tokens=st.session_state.bpd_schema_max_tokens,
                    )
                    run_dir = Path(prompt_result.get("run_dir", RUN_DIR))
                    run_dir.mkdir(parents=True, exist_ok=True)
                    schema_path = run_dir / "r1_schema.json"
                    schema_path.write_text(generated_json, encoding="utf-8")
                    st.session_state.bpd_schema_json_output = generated_json
                    st.session_state.bpd_pop_schema_json_editable = generated_json
                    st.success(f"Saved schema JSON: {schema_path}")
                    st.download_button(
                        label="Export r1_schema.json",
                        data=generated_json,
                        file_name="r1_schema.json",
                        mime="application/json",
                    )
                except Exception as exc:
                    st.error(f"Failed to build schema JSON: {exc}")

            if not can_build_schema:
                st.info("final-schema-prompt first before building schema.")

with context_col:
    with st.container(border=True):
        st.subheader("Context Builder")

        document_files = st.file_uploader(
            "Drag and drop multiple input documents",
            accept_multiple_files=True,
            type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"],
            key="docs_uploader",
        )

        if st.button(f"Build context.md (max {MAX_DOC_FILES} files)", disabled=not document_files):
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            DOCS_INPUT_DIR.mkdir(parents=True, exist_ok=True)

            saved_paths = []
            for doc_file in (document_files or [])[:MAX_DOC_FILES]:
                saved_paths.append(save_uploaded_to_folder(doc_file, DOCS_INPUT_DIR))

            builder = load_context_builder_module()
            context_md = builder.build_context_from_files(saved_paths, process_images=True)
            context_md = f"# Document Type\n\nBPD\n\n{context_md}"
            context_path = RUN_DIR / "context.md"
            context_path.write_text(context_md, encoding="utf-8")

            st.success(f"Saved: {context_path}")
            st.download_button(
                label="Download context.md",
                data=context_md,
                file_name="context.md",
                mime="text/markdown",
            )

populate_left, populate_right = st.columns(2)

with populate_left:
    with st.container(border=True):
        st.subheader("Build prompt template for Doc (BPD)")
        st.caption(
            "Template: `prompts/bpd/p2_populate.md` — fills "
            "`{{BUSINESS_CONTEXT}}`, `{{SCHEMA_JSON}}`, `{{APPENDED_MEETING_INPUT}}`, "
            "`{{CONTEXT_INPUT_MD}}` via the prompt builder."
        )

        prompt_builder_mod = load_prompt_builder_module()
        run_subdirs = prompt_builder_mod.list_bpd_run_dirs(RUN_DIR)

        pop_input_mode = st.radio(
            "Input source",
            options=("live_session", "run_folder"),
            format_func=lambda x: (
                "Live session (Schema + Process Files below)"
                if x == "live_session"
                else "Existing run folder (meeting-input.json + schema file only)"
            ),
            horizontal=True,
            key="bpd_pop_input_mode",
        )

        global_context_path = RUN_DIR / "context.md"
        selected_run_path: Path | None = None
        if pop_input_mode == "run_folder":
            if not run_subdirs:
                st.warning("No `run_NNN` folders under `run/`. Export a schema or populate inputs first.")
            else:
                labels = [p.name for p in run_subdirs]
                pick = st.selectbox(
                    "Run folder (inputs read only from this directory)",
                    options=labels,
                    key="bpd_populate_run_folder_select",
                )
                selected_run_path = RUN_DIR / pick
                st.markdown("**Files used from this folder** (no live transcript reload):")
                req_meet = selected_run_path / "meeting-input.json"
                sch_a = selected_run_path / "schema-input.json"
                sch_b = selected_run_path / "r1_schema.json"
                ctx_local = selected_run_path / "context.md"
                st.write(
                    f"{'✓' if req_meet.is_file() else '✗'} `meeting-input.json` — required"
                )
                if sch_a.is_file():
                    st.write("✓ `schema-input.json`")
                elif sch_b.is_file():
                    st.write("✓ `r1_schema.json`")
                else:
                    st.write("✗ `schema-input.json` or `r1_schema.json` — one required")
                st.write(
                    f"{'✓' if ctx_local.is_file() else '○'} `context.md` (optional; falls back to `run/context.md`)"
                )
        else:
            if global_context_path.exists():
                st.caption(
                    f"`{{{{CONTEXT_INPUT_MD}}}}`: `{global_context_path}`"
                )
            else:
                st.info(
                    "No `run/context.md` — `{{CONTEXT_INPUT_MD}}` uses a placeholder until Context Builder runs."
                )

        sync_s, sync_b = st.columns(2)
        with sync_s:
            if st.button("Pull schema JSON from Schema Builder", disabled=pop_input_mode == "run_folder"):
                st.session_state.bpd_pop_schema_json_editable = (
                    st.session_state.bpd_schema_json_output or ""
                )
                st.rerun()
        with sync_b:
            if st.button("Pull business context from Schema Builder"):
                st.session_state.bpd_pop_business_context_editable = (
                    st.session_state.bpd_business_context or ""
                )
                st.rerun()

        st.text_area(
            "Schema JSON",
            key="bpd_pop_schema_json_editable",
            height=180,
            help="BPD design JSON (schema_phase DESIGN). Pull from Schema Builder or paste.",
            disabled=pop_input_mode == "run_folder",
        )
        if pop_input_mode == "run_folder":
            st.caption("Schema JSON field ignored — using `schema-input.json` or `r1_schema.json` from the selected folder.")

        st.text_area(
            "Business Context",
            key="bpd_pop_business_context_editable",
            height=100,
            help="Always injected into `{{BUSINESS_CONTEXT}}` (not loaded from the run folder).",
        )

        if pop_input_mode == "live_session":
            if st.session_state.meeting_records:
                st.caption("Meetings (transcript paths) from Process Files:")
                for meeting in st.session_state.meeting_records:
                    st.write(
                        f"Meeting {meeting['meeting_number']} | Date: {meeting['meeting_date']} | "
                        f"JSON: {Path(meeting['transcript_path']).name}"
                    )
            else:
                st.warning("Process meeting recordings first — populate prompt needs transcript JSON.")

        if st.button("Export final-content-populate-prompt", type="primary"):
            try:
                bc = st.session_state.bpd_pop_business_context_editable or ""

                if pop_input_mode == "run_folder":
                    if not selected_run_path or not selected_run_path.is_dir():
                        st.error("Select a valid run folder.")
                    else:
                        ctx_path = selected_run_path / "context.md"
                        if ctx_path.is_file():
                            context_md_text = ctx_path.read_text(encoding="utf-8", errors="ignore")
                        elif global_context_path.is_file():
                            context_md_text = global_context_path.read_text(
                                encoding="utf-8", errors="ignore"
                            )
                        else:
                            context_md_text = ""
                        pop_result = prompt_builder_mod.build_bpd_pop_prompt_from_run_folder(
                            run_dir=selected_run_path,
                            business_context=bc,
                            context_markdown=context_md_text,
                        )
                        st.session_state.bpd_populate_prompt_result = pop_result
                        st.success(
                            f"Saved populate prompt: `{pop_result['prompt_path']}`"
                        )
                        st.caption(f"Meeting input: {pop_result['meeting_json_path']}")
                        st.caption(f"Schema input: {pop_result['schema_json_path']}")
                        st.download_button(
                            label="Download final-content-populate-prompt.md",
                            data=pop_result["prompt"],
                            file_name="final-content-populate-prompt.md",
                            mime="text/markdown",
                        )
                else:
                    schema_text = (st.session_state.bpd_pop_schema_json_editable or "").strip()
                    if not schema_text:
                        st.error("Schema JSON is empty. Build or paste schema JSON first.")
                    elif not st.session_state.meeting_records:
                        st.error("No meeting recordings processed yet.")
                    else:
                        context_md_text = ""
                        if global_context_path.exists():
                            context_md_text = global_context_path.read_text(
                                encoding="utf-8", errors="ignore"
                            )
                        pop_result = prompt_builder_mod.build_bpd_pop_prompt(
                            business_context=bc,
                            schema_json=schema_text,
                            meetings=st.session_state.meeting_records,
                            run_base_dir=RUN_DIR,
                            context_markdown=context_md_text,
                        )
                        st.session_state.bpd_populate_prompt_result = pop_result
                        st.success(
                            f"Saved populate prompt: `{pop_result['prompt_path']}`"
                        )
                        st.caption(f"Meeting input: {pop_result['meeting_json_path']}")
                        st.caption(f"Schema input: {pop_result['schema_json_path']}")
                        st.download_button(
                            label="Download final-content-populate-prompt.md",
                            data=pop_result["prompt"],
                            file_name="final-content-populate-prompt.md",
                            mime="text/markdown",
                        )
            except Exception as exc:
                st.error(f"Failed to build populate prompt: {exc}")

        st.markdown("---")
        st.markdown("**Gemini → `r2_populated.json`**")
        st.caption(
            "Builds populate prompt from template + meeting input + schema + business context, "
            f"calls Vertex (`project={BPD_PROJECT_ID}`, `location={BPD_LOCATION}`), "
            "and saves `r2_populated.json` in the target run folder."
        )
        r2_t_col, r2_m_col, _ = st.columns([1, 1, 2])
        with r2_t_col:
            r2_temp_in = st.number_input(
                "Temperature",
                min_value=0.0,
                max_value=2.0,
                value=float(st.session_state.bpd_r2_pop_temperature),
                step=0.1,
                key="bpd_r2_pop_temperature_input",
            )
        with r2_m_col:
            r2_tok_in = st.number_input(
                "Max output tokens",
                min_value=256,
                max_value=65000,
                value=int(st.session_state.bpd_r2_pop_max_tokens),
                step=256,
                key="bpd_r2_pop_max_tokens_input",
            )
        st.session_state.bpd_r2_pop_temperature = float(r2_temp_in)
        st.session_state.bpd_r2_pop_max_tokens = int(r2_tok_in)

        def _resolve_populate_run_dir() -> Path | None:
            if pop_input_mode == "run_folder":
                return selected_run_path if selected_run_path and selected_run_path.is_dir() else None
            pr = st.session_state.bpd_populate_prompt_result
            if pr and pr.get("run_dir"):
                p = Path(pr["run_dir"])
                return p if p.is_dir() else None
            return None

        if st.button("Generate r2_populated.json", type="primary"):
            try:
                run_target = _resolve_populate_run_dir()
                if not run_target:
                    if pop_input_mode == "run_folder":
                        st.error("Select a valid run folder.")
                    else:
                        st.error(
                            "Export `final-content-populate-prompt` first (live session), "
                            "or switch to **Existing run folder** and pick a run."
                        )
                else:
                    meeting_json_path = run_target / "meeting-input.json"
                    if not meeting_json_path.is_file():
                        st.error(
                            f"Missing `{meeting_json_path.name}` in `{run_target}`. "
                            "Export the populate prompt first so meeting input is available."
                        )
                    else:
                        schema_json_path = run_target / "schema-input.json"
                        if not schema_json_path.is_file():
                            schema_json_path = run_target / "r1_schema.json"
                        if not schema_json_path.is_file():
                            st.error(
                                f"Missing `schema-input.json` or `r1_schema.json` in `{run_target}`."
                            )
                        else:
                            p2_prompt_path = Path(__file__).resolve().parent / "prompts" / "bpd" / "p2_populate.md"
                            if not p2_prompt_path.is_file():
                                st.error(f"Missing populate template: `{p2_prompt_path}`")
                            else:
                                p2_prompt = p2_prompt_path.read_text(encoding="utf-8", errors="ignore")
                                appended_meeting = meeting_json_path.read_text(encoding="utf-8", errors="ignore")
                                schema_json = schema_json_path.read_text(encoding="utf-8", errors="ignore")
                                local_context_md = run_target / "context.md"
                                if local_context_md.is_file():
                                    context_input_md = local_context_md.read_text(encoding="utf-8", errors="ignore")
                                elif global_context_path.is_file():
                                    context_input_md = global_context_path.read_text(encoding="utf-8", errors="ignore")
                                else:
                                    context_input_md = ""
                                business_context = st.session_state.bpd_pop_business_context_editable or ""
                                generated = generate_r2_populated_from_prompt(
                                    p2_prompt=p2_prompt,
                                    appended_meeting=appended_meeting,
                                    schema_json=schema_json,
                                    business_context=business_context,
                                    context_input_md=context_input_md,
                                    temperature=st.session_state.bpd_r2_pop_temperature,
                                    max_output_tokens=st.session_state.bpd_r2_pop_max_tokens,
                                )
                                out_path = run_target / "r2_populated.json"
                                out_path.write_text(generated, encoding="utf-8")
                                st.success(f"Saved `{out_path}`")
                                st.download_button(
                                    label="Download r2_populated.json",
                                    data=generated,
                                    file_name="r2_populated.json",
                                    mime="application/json",
                                    key="download_r2_populated_json",
                                )
            except Exception as exc:
                st.error(f"Failed to generate r2_populated.json: {exc}")

with populate_right:
    with st.container(border=True):
        st.subheader("JSON → DOCX converter")
        st.caption(
            "Converts `r2_populated.json` from the selected run folder into "
            "`doctype_doc.docx` using `templates/bpd_template.js`."
        )

        run_target = _resolve_populate_run_dir()
        template_script = Path(__file__).resolve().parent / "templates" / "bpd_template.js"

        if run_target:
            st.caption(f"Target run folder: `{run_target}`")
            json_input = run_target / "r2_populated.json"
            docx_output = run_target / "doctype_doc.docx"
            st.caption(
                f"Command: `node {template_script} {json_input} {docx_output}`"
            )

            if st.button("Convert r2_populated.json → doctype_doc.docx", type="primary"):
                try:
                    if not template_script.is_file():
                        st.error(f"Missing template script: `{template_script}`")
                    elif not json_input.is_file():
                        st.error(
                            f"Missing `{json_input.name}` in `{run_target}`. "
                            "Generate it first using Gemini."
                        )
                    else:
                        result = subprocess.run(
                            [
                                "node",
                                str(template_script),
                                str(json_input),
                                str(docx_output),
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if result.returncode != 0:
                            details = (result.stderr or result.stdout or "").strip()
                            st.error(
                                "DOCX conversion failed. "
                                "Ensure Node.js is installed and run `npm install docx` "
                                "inside `templates/`."
                            )
                            if details:
                                st.code(details)
                        elif not docx_output.is_file():
                            st.error(
                                "Conversion command completed but output file was not found."
                            )
                        else:
                            st.success(f"Saved `{docx_output}`")
                            st.download_button(
                                label="Download doctype_doc.docx",
                                data=docx_output.read_bytes(),
                                file_name="doctype_doc.docx",
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key="download_doctype_docx",
                            )
                except Exception as exc:
                    st.error(f"Failed to convert JSON to DOCX: {exc}")
        else:
            if pop_input_mode == "run_folder":
                st.info("Select a valid run folder to enable DOCX conversion.")
            else:
                st.info(
                    "Generate `r2_populated.json` first, or switch to **Existing run folder** "
                    "and select a run containing it."
                )

