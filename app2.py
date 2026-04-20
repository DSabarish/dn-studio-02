"""
One-shot BPD pipeline: collect meeting + context inputs, then run everything with a single click.
Progress is shown inline via Streamlit status / step messages.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from backend import build_context, build_prompt, transcriptions
from backend.simple_llm import run_prompt_file, run_prompt_text

MAX_FILE_SIZE_MB = 2048
MAX_PARALLEL_WORKERS = 4
MAX_DOC_FILES = 20
RUN_DIR = Path(__file__).resolve().parent / "run"
DEFAULT_BPD_H1_HEADERS = "\n".join(
    [
        "Business Process Overview",
        "Business Process Design",
        "Business Process Flows",
        "Business Process Controls",
        "Business Process Impacts",
    ]
)


def sanitize_stem(filename: str) -> str:
    stem = Path(filename).stem.strip()
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stem)
    return safe or "transcript"


def stage_uploaded_file(uploaded_file):
    suffix = Path(uploaded_file.name).suffix or ".bin"
    file_size_bytes = getattr(uploaded_file, "size", None)
    if file_size_bytes is not None and file_size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"{uploaded_file.name} is larger than {MAX_FILE_SIZE_MB} MB.")
    uploaded_file.seek(0)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        while True:
            chunk = uploaded_file.read(1024 * 1024)
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
    for segment in segments or []:
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


def _meeting_date_key(idx: int) -> str:
    """Index-only key so duplicate filenames do not collide in session_state."""
    return f"app2_meeting_date_{idx}"


def process_meetings(
    uploaded_files,
    session_base: Path,
    transcribe_fn,
    log,
    progress,
):
    """Returns (outputs, meeting_records, errors)."""
    transcripts_dir = session_base / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    errors = []
    staged_files = []
    meeting_records = []
    total = len(uploaded_files or [])

    for idx, uploaded_file in enumerate(uploaded_files or [], start=1):
        log(f"Staging {idx}/{total}: `{uploaded_file.name}`")
        progress.progress(idx / max(total, 1))
        try:
            suffix = Path(uploaded_file.name).suffix.lower()
            md_key = _meeting_date_key(idx)
            meeting_date_val = st.session_state.get(md_key, date.today())

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
                json_path = transcripts_dir / json_name
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
                meeting_records.append(
                    {
                        "meeting_number": idx,
                        "meeting_date": str(meeting_date_val),
                        "transcript_path": str(json_path),
                    }
                )
            elif suffix == ".json":
                saved_json_path = save_uploaded_to_folder(uploaded_file, transcripts_dir)
                json_body = saved_json_path.read_text(encoding="utf-8")
                json.loads(json_body)
                outputs.append(
                    {
                        "name": uploaded_file.name,
                        "language": "unknown",
                        "duration": 0,
                        "json_text": json_body,
                        "json_name": saved_json_path.name,
                    }
                )
                meeting_records.append(
                    {
                        "meeting_number": idx,
                        "meeting_date": str(meeting_date_val),
                        "transcript_path": str(saved_json_path),
                    }
                )
            else:
                staged_files.append((uploaded_file, idx))
        except Exception as exc:
            errors.append(f"{uploaded_file.name}: {exc}")

    if staged_files:
        workers = max(1, min(MAX_PARALLEL_WORKERS, len(staged_files)))
        log(f"Transcribing {len(staged_files)} media file(s) with {workers} worker(s)…")
        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for uploaded_file, meeting_idx in staged_files:
                name, path = stage_uploaded_file(uploaded_file)
                fut = executor.submit(process_staged_file, name, path, transcribe_fn)
                futures[fut] = (name, meeting_idx)

            for future in as_completed(futures):
                name, meeting_idx = futures[future]
                completed += 1
                log(f"Transcribing {completed}/{len(staged_files)}: `{name}`")
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
                json_path = transcripts_dir / json_name
                json_path.write_text(json_body, encoding="utf-8")
                md_key = _meeting_date_key(meeting_idx)
                meeting_date_val = st.session_state.get(md_key, date.today())
                meeting_records.append(
                    {
                        "meeting_number": meeting_idx,
                        "meeting_date": str(meeting_date_val),
                        "transcript_path": str(json_path),
                    }
                )
                outputs.append(processed)
                progress.progress((total + completed) / max(total + len(staged_files), 1))

    meeting_records.sort(key=lambda x: x["meeting_number"])
    progress.progress(1.0)
    return outputs, meeting_records, errors


st.set_page_config(page_title="DN Studio — Full pipeline", layout="wide")
st.title("DN Studio — one-click pipeline")

st.caption(
    "Add meeting media or transcript JSON, optional context documents, and BPD settings. "
    "One button runs: transcripts → context.md → schema prompt → r1_schema.json → populate prompt → r2_populated.json → DOCX (if Node is available)."
)

defaults = {
    "app2_schema_temperature": 0.2,
    "app2_schema_max_tokens": 8192,
    "app2_r2_temperature": 0.2,
    "app2_r2_max_tokens": 65000,
    "app2_last_run_dir": "",
    "app2_last_errors": [],
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

with st.container(border=True):
    st.subheader("1 — Meetings (audio / video / .txt / transcript .json)")
    meeting_files = st.file_uploader(
        "Upload meeting files",
        accept_multiple_files=True,
        type=["mp3", "wav", "m4a", "ogg", "flac", "aac", "mp4", "mov", "mkv", "webm", "avi", "txt", "json"],
        key="app2_meeting_uploader",
    )
    if meeting_files:
        st.caption("Meeting date per file")
        for idx, uf in enumerate(meeting_files, start=1):
            mk = _meeting_date_key(idx)
            st.date_input(
                f"Meeting {idx} — {uf.name}",
                value=st.session_state.get(mk, date.today()),
                key=mk,
            )

with st.container(border=True):
    st.subheader("2 — Context documents (optional)")
    context_files = st.file_uploader(
        "Upload PDF, DOCX, TXT, MD, or images",
        accept_multiple_files=True,
        type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"],
        key="app2_context_uploader",
    )

with st.container(border=True):
    st.subheader("3 — BPD schema settings")
    business_context = st.text_area(
        "Business context",
        placeholder="Enter business context for schema generation…",
        height=120,
        key="app2_business_context",
    )
    h1_headers_raw = st.text_area(
        "H1 headers (one per line)",
        value=DEFAULT_BPD_H1_HEADERS,
        height=140,
        key="app2_h1_headers",
    )

with st.expander("LLM parameters", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.app2_schema_temperature = float(
            st.number_input(
                "Schema: temperature",
                0.0,
                2.0,
                float(st.session_state.app2_schema_temperature),
                0.1,
                key="app2_t_schema",
            )
        )
        st.session_state.app2_schema_max_tokens = int(
            st.number_input(
                "Schema: max output tokens",
                256,
                65000,
                int(st.session_state.app2_schema_max_tokens),
                256,
                key="app2_m_schema",
            )
        )
    with c2:
        st.session_state.app2_r2_temperature = float(
            st.number_input(
                "Populate: temperature",
                0.0,
                2.0,
                float(st.session_state.app2_r2_temperature),
                0.1,
                key="app2_t_r2",
            )
        )
        st.session_state.app2_r2_max_tokens = int(
            st.number_input(
                "Populate: max output tokens",
                256,
                65000,
                int(st.session_state.app2_r2_max_tokens),
                256,
                key="app2_m_r2",
            )
        )

run_clicked = st.button(
    "Run full pipeline",
    type="primary",
    disabled=not meeting_files,
    help="Creates a new run folder, processes all inputs, and writes outputs there.",
)

if run_clicked and meeting_files:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    progress_bar = st.progress(0.0)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        st.write(msg)

    with st.status("Running pipeline…", expanded=True) as status_box:
        try:
            log("**Step 1/8** — Creating run folder…")
            session_base = build_prompt.create_new_run_folder(RUN_DIR)
            st.session_state.app2_last_run_dir = str(session_base)
            log(f"Using `{session_base}`")

            log("**Step 2/8** — Processing meetings (transcribe / normalize)…")
            outputs, meeting_records, meet_errors = process_meetings(
                meeting_files,
                session_base,
                transcriptions.transcribe,
                log,
                progress_bar,
            )
            if meet_errors:
                for e in meet_errors:
                    st.warning(e)
            if not meeting_records:
                raise RuntimeError("No meetings were produced. Fix errors above and retry.")
            st.session_state.app2_last_errors = meet_errors

            log("**Step 3/8** — Building context.md from uploaded documents…")
            docs_dir = session_base / "docs_input"
            docs_dir.mkdir(parents=True, exist_ok=True)
            saved_docs = []
            for doc in (context_files or [])[:MAX_DOC_FILES]:
                saved_docs.append(save_uploaded_to_folder(doc, docs_dir))
            context_md = build_context.build_context_from_files(saved_docs, process_images=True)
            context_md = f"# Document Type\n\nBPD\n\n{context_md}"
            context_path = session_base / "context.md"
            context_path.write_text(context_md, encoding="utf-8")
            log(f"Saved `{context_path}`")

            h1_headers = [line.strip() for line in h1_headers_raw.splitlines() if line.strip()]
            if not h1_headers:
                raise RuntimeError("Add at least one H1 header.")

            log("**Step 4/8** — Building schema prompt and meeting-input.json…")
            prompt_schema = build_prompt.build_bpd_schema_prompt(
                business_context=business_context,
                h1_headers=h1_headers,
                meetings=meeting_records,
                run_base_dir=RUN_DIR,
                run_dir=session_base,
            )
            log(f"Wrote `{prompt_schema['prompt_path']}`")

            log("**Step 5/8** — Calling model for r1_schema.json…")
            generated_json = run_prompt_text(
                prompt_text=prompt_schema["prompt"],
                temperature=st.session_state.app2_schema_temperature,
                max_output_tokens=st.session_state.app2_schema_max_tokens,
            )
            schema_path = session_base / "r1_schema.json"
            schema_path.write_text(generated_json, encoding="utf-8")
            log(f"Saved `{schema_path}`")

            log("**Step 6/8** — Building populate prompt…")
            pop_result = build_prompt.build_bpd_pop_prompt(
                business_context=business_context or "",
                schema_json=generated_json,
                meetings=meeting_records,
                run_base_dir=RUN_DIR,
                run_dir=session_base,
                context_markdown=context_md,
            )
            log(f"Wrote `{pop_result['prompt_path']}`")

            log("**Step 7/8** — Calling model for r2_populated.json…")
            populate_prompt_path = session_base / "debug-prompt-populate-content.md"
            r2_text = run_prompt_file(
                prompt_path=populate_prompt_path,
                temperature=st.session_state.app2_r2_temperature,
                max_output_tokens=st.session_state.app2_r2_max_tokens,
            )
            r2_path = session_base / "r2_populated.json"
            r2_path.write_text(r2_text, encoding="utf-8")
            log(f"Saved `{r2_path}`")

            log("**Step 8/8** — JSON → DOCX (optional)…")
            template_script = Path(__file__).resolve().parent / "templates" / "bpd_template.js"
            docx_output = session_base / "doctype_doc.docx"
            if not template_script.is_file():
                st.warning(f"Skipping DOCX: missing `{template_script}`")
            else:
                result = subprocess.run(
                    ["node", str(template_script), str(r2_path), str(docx_output)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0 or not docx_output.is_file():
                    details = (result.stderr or result.stdout or "").strip()
                    st.warning(
                        "DOCX conversion failed (install Node.js and run `npm install docx` in `templates/`)."
                    )
                    if details:
                        st.code(details)
                else:
                    log(f"Saved `{docx_output}`")

            status_box.update(label="Pipeline finished", state="complete")
        except Exception as exc:
            st.session_state.app2_last_errors = log_lines + [str(exc)]
            st.error(f"Pipeline stopped: {exc}")
            status_box.update(label="Pipeline failed", state="error")
        finally:
            progress_bar.empty()

run_dir_raw = str(st.session_state.get("app2_last_run_dir", "")).strip()
if run_dir_raw:
    run_path = Path(run_dir_raw)
    if run_path.is_dir():
        run_key = run_path.name
        st.divider()
        st.subheader("Last run — downloads")
        st.caption(f"Folder: `{run_path}`")
        dcols = st.columns(3)
        r1 = run_path / "r1_schema.json"
        r2 = run_path / "r2_populated.json"
        ctx = run_path / "context.md"
        docx = run_path / "doctype_doc.docx"
        if r1.is_file():
            dcols[0].download_button(
                "r1_schema.json",
                data=r1.read_text(encoding="utf-8"),
                file_name="r1_schema.json",
                mime="application/json",
                key=f"app2_dl_r1_{run_key}",
            )
        if r2.is_file():
            dcols[1].download_button(
                "r2_populated.json",
                data=r2.read_text(encoding="utf-8"),
                file_name="r2_populated.json",
                mime="application/json",
                key=f"app2_dl_r2_{run_key}",
            )
        if ctx.is_file():
            dcols[2].download_button(
                "context.md",
                data=ctx.read_text(encoding="utf-8"),
                file_name="context.md",
                mime="text/markdown",
                key=f"app2_dl_ctx_{run_key}",
            )
        if docx.is_file():
            st.download_button(
                "doctype_doc.docx",
                data=docx.read_bytes(),
                file_name="doctype_doc.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"app2_dl_docx_{run_key}",
            )

        transcripts_dir = run_path / "transcripts"
        if transcripts_dir.is_dir():
            outs = []
            for p in sorted(transcripts_dir.glob("*.json")):
                outs.append(
                    {
                        "json_name": p.name,
                        "json_text": p.read_text(encoding="utf-8"),
                    }
                )
            if outs:
                z = build_zip(outs)
                st.download_button(
                    "All transcripts (ZIP)",
                    data=z,
                    file_name=f"transcripts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                    mime="application/zip",
                    key=f"app2_dl_zip_{run_key}",
                )
