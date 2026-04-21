from __future__ import annotations

import base64
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from backend import build_context, build_prompt, transcriptions
from backend.ingest import load_gcs_meeting_inputs, load_uploaded_meeting_inputs
from backend.runner import build_zip, process_meetings, save_bytes_to_folder
from backend.simple_llm import run_prompt_file, run_prompt_text
from backend.timing import StepTimer

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

# Browser / iframe limits: very large ZIPs must use the manual download button.
_AUTO_ZIP_DOWNLOAD_MAX_BYTES = 6 * 1024 * 1024


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


st.set_page_config(page_title="DN Studio — Full pipeline", layout="wide")
st.title("DN Studio — one-click pipeline")

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

run_clicked = st.button(
    "Run full pipeline",
    type="primary",
    disabled=not (meeting_files or gcs_uris_raw.strip()),
    help="Creates a new run folder, processes all inputs, and writes outputs there.",
)

if run_clicked and (meeting_files or gcs_uris_raw.strip()):
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    progress_bar = st.progress(0.0)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        st.write(msg)

    with st.status("Running pipeline…", expanded=True) as status_box:
        try:
            step_timer = StepTimer()
            log("**Step 1/8** — Creating run folder…")
            session_base, elapsed = step_timer.run(1, "Creating run folder", lambda: build_prompt.create_new_run_folder(RUN_DIR))
            st.session_state.app_last_run_dir = str(session_base)
            st.session_state.app_last_run_dir = str(session_base)
            log(f"Using `{session_base}` ({elapsed:.2f}s)")

            log("**Step 2/8** — Processing meetings (upload + GCS, transcribe / normalize)…")

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
            outputs = process_result.outputs
            meeting_records = process_result.meeting_records
            log(f"Step 2 completed in {elapsed:.2f}s")

            log("**Step 3/8** — Building context.md from uploaded documents…")
            def step_3():
                docs_dir = session_base / "docs_input"
                docs_dir.mkdir(parents=True, exist_ok=True)
                saved_docs = []
                for doc in (context_files or [])[:MAX_DOC_FILES]:
                    doc.seek(0)
                    saved_docs.append(save_bytes_to_folder(doc.name, doc.read(), docs_dir))
                context_md = build_context.build_context_from_files(saved_docs, process_images=True)
                context_md = f"# Document Type\n\nBPD\n\n{context_md}"
                context_path = session_base / "context.md"
                context_path.write_text(context_md, encoding="utf-8")
                return context_md, context_path

            (context_md, context_path), elapsed = step_timer.run(3, "Build context.md", step_3)
            log(f"Saved `{context_path}` ({elapsed:.2f}s)")

            h1_headers = [line.strip() for line in h1_headers_raw.splitlines() if line.strip()]
            if not h1_headers:
                raise RuntimeError("Add at least one H1 header.")

            log("**Step 4/8** — Building schema prompt and meeting-input.json…")
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
            generated_json, elapsed = step_timer.run(
                5,
                "Call model for r1_schema.json",
                lambda: run_prompt_text(
                    prompt_text=prompt_schema["prompt"],
                    temperature=st.session_state.app_schema_temperature,
                    max_output_tokens=st.session_state.app_schema_max_tokens,
                ),
            )
            schema_path = session_base / "r1_schema.json"
            schema_path.write_text(generated_json, encoding="utf-8")
            log(f"Saved `{schema_path}` ({elapsed:.2f}s)")

            log("**Step 6/8** — Building populate prompt…")
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
            r2_text, elapsed = step_timer.run(
                7,
                "Call model for r2_populated.json",
                lambda: run_prompt_file(
                    prompt_path=populate_prompt_path,
                    temperature=st.session_state.app_r2_temperature,
                    max_output_tokens=st.session_state.app_r2_max_tokens,
                ),
            )
            r2_path = session_base / "r2_populated.json"
            r2_path.write_text(r2_text, encoding="utf-8")
            log(f"Saved `{r2_path}` ({elapsed:.2f}s)")

            log("**Step 8/8** — JSON → DOCX (optional)…")
            def step_8():
                template_script = Path(__file__).resolve().parent / "templates" / "bpd_template.js"
                docx_output = session_base / "doctype_doc.docx"
                if not template_script.is_file():
                    return None, f"Skipping DOCX: missing `{template_script}`"
                result = subprocess.run(
                    ["node", str(template_script), str(r2_path), str(docx_output)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0 or not docx_output.is_file():
                    details = (result.stderr or result.stdout or "").strip()
                    return None, details or "DOCX conversion failed."
                return docx_output, ""

            (docx_output, docx_message), elapsed = step_timer.run(8, "JSON -> DOCX", step_8)
            if docx_output is None:
                st.warning("DOCX conversion failed (install Node.js and run `npm install docx` in `templates/`).")
                if docx_message:
                    st.code(docx_message)
            else:
                log(f"Saved `{docx_output}` ({elapsed:.2f}s)")

            timings_path = session_base / "step_timings.json"
            timings_payload = [
                {"step": t.step, "name": t.name, "elapsed_seconds": round(t.elapsed_seconds, 3)}
                for t in step_timer.as_list()
            ]
            timings_path.write_text(json.dumps(timings_payload, indent=2), encoding="utf-8")
            log(f"Saved `{timings_path}`")

            transcripts_done = session_base / "transcripts"
            if transcripts_done.is_dir() and any(transcripts_done.glob("*.json")):
                st.session_state.app_trigger_zip_autodl = str(session_base.resolve())
            else:
                st.session_state.app_trigger_zip_autodl = ""

            status_box.update(label="Pipeline finished", state="complete")
        except Exception as exc:
            st.session_state.app_trigger_zip_autodl = ""
            st.session_state.app_last_errors = log_lines + [str(exc)]
            st.error(f"Pipeline stopped: {exc}")
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
                key=f"app_dl_r1_{run_key}",
            )
        if r2.is_file():
            dcols[1].download_button(
                "r2_populated.json",
                data=r2.read_text(encoding="utf-8"),
                file_name="r2_populated.json",
                mime="application/json",
                key=f"app_dl_r2_{run_key}",
            )
        if ctx.is_file():
            dcols[2].download_button(
                "context.md",
                data=ctx.read_text(encoding="utf-8"),
                file_name="context.md",
                mime="text/markdown",
                key=f"app_dl_ctx_{run_key}",
            )
        if docx.is_file():
            st.download_button(
                "doctype_doc.docx",
                data=docx.read_bytes(),
                file_name="doctype_doc.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"app_dl_docx_{run_key}",
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
                zip_buf = build_zip(outs)
                zip_bytes = zip_buf.getvalue()
                zip_name = f"transcripts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                trigger_raw = (st.session_state.get("app_trigger_zip_autodl") or "").strip()
                if trigger_raw and Path(trigger_raw).resolve() == run_path.resolve():
                    if len(zip_bytes) <= _AUTO_ZIP_DOWNLOAD_MAX_BYTES:
                        _trigger_browser_zip_download(zip_bytes, zip_name)
                        st.caption(
                            "Transcript ZIP should download automatically. "
                            "If your browser blocked it, use the button below."
                        )
                    else:
                        st.info(
                            "Transcript ZIP is large — use **All transcripts (ZIP)** below to download."
                        )
                    st.session_state.app_trigger_zip_autodl = ""
                st.download_button(
                    "All transcripts (ZIP)",
                    data=zip_bytes,
                    file_name=zip_name,
                    mime="application/zip",
                    key=f"app_dl_zip_{run_key}",
                )
