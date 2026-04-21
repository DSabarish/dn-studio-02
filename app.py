from __future__ import annotations

import base64
import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from backend import build_context, build_prompt, transcriptions
from backend.artifacts import (
    build_run_zip,
    build_step_timings_payload,
    convert_json_to_docx,
    normalize_json_text,
    upload_run_artifacts_to_gcs,
)
from backend.ingest import load_gcs_meeting_inputs, load_uploaded_meeting_inputs
from backend.runner import process_meetings, save_bytes_to_folder
from backend.simple_llm import run_prompt_file, run_prompt_text
from backend.timing import StepTimer

MAX_DOC_FILES = 20
RUN_DIR = Path(__file__).resolve().parent / "run"
RUNS_BUCKET = "dn-studio-runs-01"
DEFAULT_BPD_H1_HEADERS = "\n".join(
    [
        "Business Process Overview",
        "Business Process Design",
        "Business Process Flows",
        "Business Process Controls",
        "Business Process Impacts",
    ]
)

# Browser / iframe limits: very large ZIPs must use the manual download button.
_AUTO_ZIP_DOWNLOAD_MAX_BYTES = 6 * 1024 * 1024

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("dn_studio.app")


def _trigger_browser_zip_download(zip_bytes: bytes, file_name: str) -> None:
    b64 = base64.b64encode(zip_bytes).decode("ascii")
    components.html(
        f"""
        <script>
            (function () {{
                const b64 = {json.dumps(b64)};
                const name = {json.dumps(file_name)};
                const bin = atob(b64);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                const blob = new Blob([bytes], {{ type: "application/zip" }});
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            }})();
        </script>
        """,
        height=0,
    )


def _meeting_date_key(idx: int) -> str:
    return f"app_meeting_date_{idx}"


def _load_meeting_records_from_run(run_path: Path) -> list[dict]:
    meeting_json_path = run_path / "meeting-input.json"
    if meeting_json_path.is_file():
        items = json.loads(meeting_json_path.read_text(encoding="utf-8"))
        records: list[dict] = []
        for idx, item in enumerate(items, start=1):
            transcript_path = str(item.get("transcript_json_path", "")).strip()
            if transcript_path:
                records.append(
                    {
                        "meeting_number": int(item.get("meeting_number", idx)),
                        "meeting_date": str(item.get("meeting_date", date.today())),
                        "transcript_path": transcript_path,
                    }
                )
        records.sort(key=lambda x: x["meeting_number"])
        return records

    transcripts_dir = run_path / "transcripts"
    transcript_files = sorted(transcripts_dir.glob("*.json"))
    return [
        {
            "meeting_number": idx,
            "meeting_date": str(date.today()),
            "transcript_path": str(path),
        }
        for idx, path in enumerate(transcript_files, start=1)
    ]


def _validate_r1_schema_text(schema_text: str, expected_h1_headers: list[str]) -> str:
    normalized = normalize_json_text(schema_text)
    obj = json.loads(normalized)
    if not isinstance(obj, dict):
        raise ValueError("r1_schema must be a JSON object.")
    if "structure" not in obj or not isinstance(obj["structure"], list):
        raise ValueError("r1_schema must contain a top-level 'structure' array.")

    actual_h1 = []
    for item in obj["structure"]:
        if isinstance(item, dict) and str(item.get("[TAG]", "")).upper() == "H1":
            actual_h1.append(str(item.get("name", "")).strip().lower())

    required = [h.strip().lower() for h in expected_h1_headers if h.strip()]
    missing = [h for h in required if h not in actual_h1]
    if missing:
        raise ValueError(
            "r1_schema is incomplete; missing H1 sections: " + ", ".join(missing)
        )
    return json.dumps(obj, ensure_ascii=False, indent=2)


#$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
#$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$

st.set_page_config(page_title="DN Studio", layout="wide")
st.title("DN Studio")

st.caption(
    "Add meeting media or transcript JSON, optional context documents, and BPD settings. "
    "One button runs: transcripts → context.md → schema prompt → r1_schema.json → populate prompt → r2_populated.json → DOCX (if Node is available)."
)

defaults = {
    "app_schema_temperature": 0.2,
    "app_schema_max_tokens": 8192,
    "app_r2_temperature": 0.2,
    "app_r2_max_tokens": 65000,
    "app_last_run_dir": "",
    "app_last_errors": [],
    "app_trigger_zip_autodl": "",
    "app_keep_local_runs": True,
    "app_upload_to_gcs": True,
    "app_signed_url_ttl_minutes": 30,
    "app_last_gcs_result": None,
    "app_run_mode": "New run",
    "app_resume_run_dir": "",
}

for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

available_runs = build_prompt.list_bpd_run_dirs(RUN_DIR)
available_run_labels = [p.name for p in available_runs]

with st.container(border=True):
    st.subheader("Run mode")
    st.session_state.app_run_mode = st.radio(
        "Choose how to run",
        options=["New run", "Resume run"],
        key="app_run_mode_radio",
        horizontal=True,
    )
    selected_resume_path = ""
    if st.session_state.app_run_mode == "Resume run":
        if not available_run_labels:
            st.warning("No existing run folders found under `run/`. Switch to New run.")
        else:
            selected_label = st.selectbox(
                "Select active run folder",
                options=available_run_labels,
                key="app_resume_run_label",
            )
            selected_resume_path = str((RUN_DIR / selected_label).resolve())
            st.caption(f"Selected run folder: `{selected_resume_path}`")
    st.session_state.app_resume_run_dir = selected_resume_path

with st.container(border=True):
    st.subheader("1 — Meetings (audio / video / .txt / transcript .json)")
    meeting_files = st.file_uploader(
        "Upload meeting files",
        accept_multiple_files=True,
        type=["mp3", "wav", "m4a", "ogg", "flac", "aac", "mp4", "mov", "mkv", "webm", "avi", "txt", "json"],
        key="app_meeting_uploader",
    )
    gcs_uris_raw = st.text_area(
        "GCS URIs (optional, one per line: media or transcript .json)",
        placeholder="gs://bucket/path/meeting.mp4\ngs://bucket/path/transcript.json",
        height=90,
        key="app_gcs_uris",
    )
    combined_count = len(meeting_files or []) + len([x for x in gcs_uris_raw.splitlines() if x.strip()])
    if combined_count:
        st.caption("Meeting date per file")
        labels = [uf.name for uf in (meeting_files or [])] + [
            line.strip() for line in gcs_uris_raw.splitlines() if line.strip()
        ]
        for idx, label in enumerate(labels, start=1):
            mk = _meeting_date_key(idx)
            st.date_input(
                f"Meeting {idx} — {label}",
                value=st.session_state.get(mk, date.today()),
                key=mk,
            )

with st.container(border=True):
    st.subheader("2 — Context documents (optional)")
    context_files = st.file_uploader(
        "Upload PDF, DOCX, TXT, MD, or images",
        accept_multiple_files=True,
        type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"],
        key="app_context_uploader",
    )

with st.container(border=True):
    st.subheader("3 — BPD schema settings")
    business_context = st.text_area(
        "Business context",
        placeholder="Enter business context for schema generation…",
        height=120,
        key="app_business_context",
    )
    h1_headers_raw = st.text_area(
        "H1 headers (one per line)",
        value=DEFAULT_BPD_H1_HEADERS,
        height=140,
        key="app_h1_headers",
    )

with st.expander("LLM parameters", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.app_schema_temperature = float(
            st.number_input(
                "Schema: temperature",
                0.0,
                2.0,
                float(st.session_state.app_schema_temperature),
                0.1,
                key="app_t_schema",
            )
        )
        st.session_state.app_schema_max_tokens = int(
            st.number_input(
                "Schema: max output tokens",
                256,
                65000,
                int(st.session_state.app_schema_max_tokens),
                256,
                key="app_m_schema",
            )
        )
    with c2:
        st.session_state.app_r2_temperature = float(
            st.number_input(
                "Populate: temperature",
                0.0,
                2.0,
                float(st.session_state.app_r2_temperature),
                0.1,
                key="app_t_r2",
            )
        )
        st.session_state.app_r2_max_tokens = int(
            st.number_input(
                "Populate: max output tokens",
                256,
                65000,
                int(st.session_state.app_r2_max_tokens),
                256,
                key="app_m_r2",
            )
        )
with st.expander("Artifact storage", expanded=False):
    st.session_state.app_keep_local_runs = True
    st.session_state.app_upload_to_gcs = True
    st.caption("Keep local run artifacts: enabled")
    st.caption("Upload run artifacts to GCS: enabled")
    st.caption(f"Runs bucket: {RUNS_BUCKET}")
    st.session_state.app_signed_url_ttl_minutes = int(
        st.number_input(
            "Signed URL TTL (minutes)",
            min_value=1,
            max_value=1440,
            value=int(st.session_state.app_signed_url_ttl_minutes),
            step=1,
            key="app_signed_url_ttl_input",
        )
    )

run_clicked = st.button(
    "Run full pipeline",
    type="primary",
    disabled=(
        st.session_state.app_run_mode == "New run"
        and not (meeting_files or gcs_uris_raw.strip())
    ) or (
        st.session_state.app_run_mode == "Resume run"
        and not st.session_state.app_resume_run_dir
    ),
    help="Creates a new run folder, processes all inputs, and writes outputs there.",
)

if run_clicked:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    progress_bar = st.progress(0.0)
    log_lines: list[str] = []
    pipeline_started_at = datetime.utcnow()

    def log(msg: str) -> None:
        log_lines.append(msg)
        st.write(msg)
        logger.info(msg)

    with st.status("Running pipeline…", expanded=True) as status_box:
        try:
            logger.info("Pipeline run started")
            keep_local_runs = bool(st.session_state.get("app_keep_local_runs", False))
            upload_to_gcs = bool(st.session_state.get("app_upload_to_gcs", False))
            runs_bucket = RUNS_BUCKET
            signed_url_ttl_minutes = int(st.session_state.get("app_signed_url_ttl_minutes", 30))
            logger.info(
                "Pipeline config | meetings_uploaded=%s | gcs_uri_count=%s | context_docs=%s | keep_local_runs=%s | upload_to_gcs=%s",
                len(meeting_files or []),
                len([x for x in gcs_uris_raw.splitlines() if x.strip()]),
                len(context_files or []),
                keep_local_runs,
                upload_to_gcs,
            )
            st.session_state.app_last_gcs_result = None
            step_timer = StepTimer()
            run_mode = st.session_state.get("app_run_mode", "New run")
            resume_run_raw = str(st.session_state.get("app_resume_run_dir", "")).strip()
            log("**Step 1/8** — Selecting run folder…")
            if run_mode == "Resume run":
                if not resume_run_raw:
                    raise RuntimeError("Select an active run folder to resume.")
                session_base = Path(resume_run_raw)
                if not session_base.is_dir():
                    raise RuntimeError(f"Run folder not found: {session_base}")
                elapsed = 0.0
                log(f"Resuming existing run folder `{session_base}`")
            else:
                session_base, elapsed = step_timer.run(1, "Creating run folder", lambda: build_prompt.create_new_run_folder(RUN_DIR))
            st.session_state.app_last_run_dir = str(session_base)
            st.session_state.app_last_run_dir = str(session_base)
            log(f"Using `{session_base}` ({elapsed:.2f}s)")

            log("**Step 2/8** — Processing meetings (upload + GCS, transcribe / normalize)…")
            transcripts_dir = session_base / "transcripts"
            existing_transcripts = sorted(transcripts_dir.glob("*.json")) if transcripts_dir.is_dir() else []
            if run_mode == "Resume run" and existing_transcripts:
                meeting_records = _load_meeting_records_from_run(session_base)
                st.session_state.app_last_errors = []
                elapsed = 0.0
                log(f"Step 2 skipped; reusing {len(existing_transcripts)} transcript(s) from `{transcripts_dir}`")
            else:
                def step_2():
                    uploaded_inputs = load_uploaded_meeting_inputs(meeting_files or [])
                    gcs_inputs = load_gcs_meeting_inputs(gcs_uris_raw)
                    all_inputs = uploaded_inputs + gcs_inputs
                    meeting_dates = {
                        idx: st.session_state.get(_meeting_date_key(idx), date.today())
                        for idx in range(1, len(all_inputs) + 1)
                    }
                    return process_meetings(
                        meeting_inputs=all_inputs,
                        session_base=session_base,
                        transcribe_fn=transcriptions.transcribe,
                        meeting_dates=meeting_dates,
                        log=log,
                        progress=lambda v: progress_bar.progress(v),
                    )

                process_result, elapsed = step_timer.run(2, "Process meetings", step_2)
                if process_result.errors:
                    for e in process_result.errors:
                        st.warning(e)
                if not process_result.meeting_records:
                    raise RuntimeError("No meetings were produced. Fix errors above and retry.")
                st.session_state.app_last_errors = process_result.errors
                meeting_records = process_result.meeting_records
                log(f"Step 2 completed in {elapsed:.2f}s")

            log("**Step 3/8** — Building context.md from uploaded documents…")
            context_path = session_base / "context.md"
            if run_mode == "Resume run" and context_path.is_file():
                context_md = context_path.read_text(encoding="utf-8", errors="ignore")
                elapsed = 0.0
                log(f"Step 3 skipped; reusing `{context_path}`")
            else:
                def step_3():
                    docs_dir = session_base / "docs_input"
                    docs_dir.mkdir(parents=True, exist_ok=True)
                    saved_docs = []
                    for doc in (context_files or [])[:MAX_DOC_FILES]:
                        doc.seek(0)
                        saved_docs.append(save_bytes_to_folder(doc.name, doc.read(), docs_dir))
                    context_md_inner = build_context.build_context_from_files(saved_docs, process_images=True)
                    context_md_inner = f"# Document Type\n\nBPD\n\n{context_md_inner}"
                    context_path_inner = session_base / "context.md"
                    context_path_inner.write_text(context_md_inner, encoding="utf-8")
                    return context_md_inner, context_path_inner

                (context_md, context_path), elapsed = step_timer.run(3, "Build context.md", step_3)
                log(f"Saved `{context_path}` ({elapsed:.2f}s)")

            h1_headers = [line.strip() for line in h1_headers_raw.splitlines() if line.strip()]
            if not h1_headers:
                raise RuntimeError("Add at least one H1 header.")

            log("**Step 4/8** — Building schema prompt and meeting-input.json…")
            schema_prompt_path = session_base / "debug-prompt-schema.md"
            if run_mode == "Resume run" and schema_prompt_path.is_file():
                prompt_schema = {
                    "prompt": schema_prompt_path.read_text(encoding="utf-8", errors="ignore"),
                    "prompt_path": str(schema_prompt_path),
                }
                elapsed = 0.0
                log(f"Step 4 skipped; reusing `{schema_prompt_path}`")
            else:
                prompt_schema, elapsed = step_timer.run(
                    4,
                    "Build schema prompt",
                    lambda: build_prompt.build_bpd_schema_prompt(
                        business_context=business_context,
                        h1_headers=h1_headers,
                        meetings=meeting_records,
                        run_base_dir=RUN_DIR,
                        run_dir=session_base,
                    ),
                )
                log(f"Wrote `{prompt_schema['prompt_path']}` ({elapsed:.2f}s)")

            log("**Step 5/8** — Calling model for r1_schema.json…")
            def step_5():
                base_prompt = prompt_schema["prompt"]
                raw = run_prompt_text(
                    prompt_text=base_prompt,
                    temperature=st.session_state.app_schema_temperature,
                    max_output_tokens=st.session_state.app_schema_max_tokens,
                )
                try:
                    return _validate_r1_schema_text(raw, h1_headers)
                except Exception:
                    retry_prompt = (
                        base_prompt
                        + "\n\nIMPORTANT: Return ONLY one valid JSON object with top-level keys "
                          "'document_type', 'schema_phase', 'authoring_mode', and 'structure'. "
                          "The 'structure' must include all required H1 headers."
                    )
                    retry_raw = run_prompt_text(
                        prompt_text=retry_prompt,
                        temperature=st.session_state.app_schema_temperature,
                        max_output_tokens=st.session_state.app_schema_max_tokens,
                    )
                    return _validate_r1_schema_text(retry_raw, h1_headers)

            schema_path = session_base / "r1_schema.json"
            if run_mode == "Resume run" and schema_path.is_file():
                generated_json = schema_path.read_text(encoding="utf-8", errors="ignore")
                elapsed = 0.0
                log(f"Step 5 skipped; reusing `{schema_path}`")
            else:
                generated_json, elapsed = step_timer.run(5, "Call model for r1_schema.json", step_5)
                schema_path.write_text(generated_json, encoding="utf-8")
                log(f"Saved `{schema_path}` ({elapsed:.2f}s)")

            log("**Step 6/8** — Building populate prompt…")
            populate_prompt_path = session_base / "debug-prompt-populate-content.md"
            if run_mode == "Resume run" and populate_prompt_path.is_file():
                pop_result = {"prompt_path": str(populate_prompt_path)}
                elapsed = 0.0
                log(f"Step 6 skipped; reusing `{populate_prompt_path}`")
            else:
                pop_result, elapsed = step_timer.run(
                    6,
                    "Build populate prompt",
                    lambda: build_prompt.build_bpd_pop_prompt(
                        business_context=business_context or "",
                        schema_json=generated_json,
                        meetings=meeting_records,
                        run_base_dir=RUN_DIR,
                        run_dir=session_base,
                        context_markdown=context_md,
                    ),
                )
                log(f"Wrote `{pop_result['prompt_path']}` ({elapsed:.2f}s)")

            log("**Step 7/8** — Calling model for r2_populated.json…")
            populate_prompt_path = session_base / "debug-prompt-populate-content.md"
            if not populate_prompt_path.is_file():
                legacy_populate_prompt_path = session_base / "final-content-populate-prompt.md"
                if legacy_populate_prompt_path.is_file():
                    populate_prompt_path = legacy_populate_prompt_path
            r2_path = session_base / "r2_populated.json"
            if run_mode == "Resume run" and r2_path.is_file():
                r2_text = r2_path.read_text(encoding="utf-8", errors="ignore")
                elapsed = 0.0
                log(f"Step 7 skipped; reusing `{r2_path}`")
            else:
                r2_text, elapsed = step_timer.run(
                    7,
                    "Call model for r2_populated.json",
                    lambda: run_prompt_file(
                        prompt_path=populate_prompt_path,
                        temperature=st.session_state.app_r2_temperature,
                        max_output_tokens=st.session_state.app_r2_max_tokens,
                    ),
                )
                r2_text = normalize_json_text(r2_text)
                r2_path.write_text(r2_text, encoding="utf-8")
                log(f"Saved `{r2_path}` ({elapsed:.2f}s)")

            log("**Step 8/8** — JSON → DOCX (optional)…")
            def step_8():
                return convert_json_to_docx(Path(__file__).resolve().parent, r2_path)

            existing_docx = session_base / "doctype_doc.docx"
            if run_mode == "Resume run" and existing_docx.is_file():
                docx_output, docx_message = existing_docx, ""
                elapsed = 0.0
                log(f"Step 8 skipped; reusing `{existing_docx}`")
            else:
                (docx_output, docx_message), elapsed = step_timer.run(8, "JSON -> DOCX", step_8)
                if docx_output is None:
                    st.warning("DOCX conversion failed (install Node.js and run `npm install docx` in `templates/`).")
                    if docx_message:
                        st.code(docx_message)
                else:
                    log(f"Saved `{docx_output}` ({elapsed:.2f}s)")

            timings_path = session_base / "step_timings.json"
            pipeline_ended_at = datetime.utcnow()
            timings_payload = build_step_timings_payload(step_timer, pipeline_started_at, pipeline_ended_at)
            timings_path.write_text(json.dumps(timings_payload, indent=2), encoding="utf-8")
            log(f"Saved `{timings_path}`")
            logger.info(
                "Pipeline timings | run_dir=%s | total_elapsed_seconds=%.3f",
                str(session_base),
                (pipeline_ended_at - pipeline_started_at).total_seconds(),
            )

            if upload_to_gcs:
                log("Uploading run artifacts to GCS…")
                gcs_result = upload_run_artifacts_to_gcs(
                    run_path=session_base,
                    bucket_name=runs_bucket,
                    signed_url_ttl_minutes=signed_url_ttl_minutes,
                )
                st.session_state.app_last_gcs_result = gcs_result
                log(f"Uploaded artifacts to `gs://{gcs_result['bucket']}/{gcs_result['prefix']}`")

            transcripts_done = session_base / "transcripts"
            if transcripts_done.is_dir() and any(transcripts_done.glob("*.json")):
                st.session_state.app_trigger_zip_autodl = str(session_base.resolve())
            else:
                st.session_state.app_trigger_zip_autodl = ""

            if not keep_local_runs:
                st.session_state.app_trigger_zip_autodl = ""
                st.session_state.app_last_run_dir = ""
                shutil.rmtree(session_base, ignore_errors=True)
                log("Local run artifacts removed (toggle is off).")

            logger.info("Pipeline run completed successfully")
            status_box.update(label="Pipeline finished", state="complete")
        except Exception as exc:
            st.session_state.app_trigger_zip_autodl = ""
            st.session_state.app_last_errors = log_lines + [str(exc)]
            st.error(f"Pipeline stopped: {exc}")
            logger.exception("Pipeline run failed")
            status_box.update(label="Pipeline failed", state="error")
        finally:
            progress_bar.empty()

run_dir_raw = str(st.session_state.get("app_last_run_dir", "")).strip()
if run_dir_raw:
    run_path = Path(run_dir_raw)
    if run_path.is_dir():
        run_key = run_path.name
        st.divider()
        st.subheader("Last run — downloads")
        dcols = st.columns(2)
        docx = run_path / "doctype_doc.docx"
        docx_exists = docx.is_file()
        docx_data = docx.read_bytes() if docx_exists else b""

        run_zip_bytes = build_run_zip(run_path)
        run_zip_name = f"{run_path.name}_all_files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        dcols[0].download_button(
            "Download all run files (ZIP)",
            data=run_zip_bytes,
            file_name=run_zip_name,
            mime="application/zip",
            key=f"app_dl_run_zip_{run_key}",
        )

        dcols[1].download_button(
                "Download BPD.docx",
                data=docx_data,
                file_name="BPD.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"app_dl_docx_{run_key}",
                disabled=not docx_exists,
            )

gcs_result = st.session_state.get("app_last_gcs_result")
if gcs_result:
    st.divider()
    st.subheader("Last run — GCS artifacts")
    st.caption(f"Location: `gs://{gcs_result['bucket']}/{gcs_result['prefix']}`")
    folder_url = (
        f"https://console.cloud.google.com/storage/browser/"
        f"{gcs_result['bucket']}/{gcs_result['prefix']}?project=dn-studio-01"
    )
    st.markdown(f"[Open run folder in GCS Console]({folder_url})")
    if gcs_result.get("manifest_signed_url"):
        st.markdown(f"[Download manifest.json]({gcs_result['manifest_signed_url']})")
    artifact_links = [a for a in gcs_result.get("artifacts", []) if a.get("signed_url")]
    if artifact_links:
        for artifact in artifact_links:
            st.markdown(f"- [{artifact['path']}]({artifact['signed_url']})")
