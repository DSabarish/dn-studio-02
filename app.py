from __future__ import annotations

import base64
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from backend import build_prompt
from backend.artifacts import build_run_zip
from backend.pipeline_service import PipelineConfig, run_pipeline

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
load_dotenv()


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
    "app_transcription_engine": "Whisper (local)",
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

with st.expander("Transcription engine", expanded=False):
    st.session_state.app_transcription_engine = st.radio(
        "Choose transcription engine",
        options=["Whisper (local)", "AssemblyAI (API)"],
        key="app_transcription_engine_radio",
        horizontal=True,
    )
    if st.session_state.app_transcription_engine == "AssemblyAI (API)":
        if not (os.getenv("ASSEMBLYAI_API_KEY") or "").strip():
            st.warning("`ASSEMBLYAI_API_KEY` is not set. AssemblyAI transcription will fail until it is configured.")
        st.caption("Uses AssemblyAI upload + async polling and returns Whisper-compatible output.")
    else:
        st.caption("Uses local Faster Whisper (ffmpeg + faster-whisper).")

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

    def log(msg: str) -> None:
        log_lines.append(msg)
        st.write(msg)
        logger.info(msg)

    with st.status("Running pipeline…", expanded=True) as status_box:
        try:
            st.session_state.app_last_gcs_result = None
            meeting_dates_map = {
                idx: st.session_state.get(_meeting_date_key(idx), date.today())
                for idx in range(
                    1,
                    len(meeting_files or []) + len([x for x in gcs_uris_raw.splitlines() if x.strip()]) + 1,
                )
            }
            config = PipelineConfig(
                run_dir=RUN_DIR,
                runs_bucket=RUNS_BUCKET,
                run_mode=st.session_state.get("app_run_mode", "New run"),
                resume_run_dir=str(st.session_state.get("app_resume_run_dir", "")).strip(),
                meeting_files=list(meeting_files or []),
                gcs_uris_raw=gcs_uris_raw,
                context_files=list(context_files or []),
                meeting_dates=meeting_dates_map,
                business_context=business_context,
                h1_headers_raw=h1_headers_raw,
                schema_temperature=float(st.session_state.app_schema_temperature),
                schema_max_tokens=int(st.session_state.app_schema_max_tokens),
                r2_temperature=float(st.session_state.app_r2_temperature),
                r2_max_tokens=int(st.session_state.app_r2_max_tokens),
                transcription_engine=st.session_state.get("app_transcription_engine", "Whisper (local)"),
                keep_local_runs=bool(st.session_state.get("app_keep_local_runs", False)),
                upload_to_gcs=bool(st.session_state.get("app_upload_to_gcs", False)),
                signed_url_ttl_minutes=int(st.session_state.get("app_signed_url_ttl_minutes", 30)),
                app_root=Path(__file__).resolve().parent,
            )
            log(f"Selected transcription engine in UI: **{config.transcription_engine}**")
            logger.info("UI selected transcription engine | engine=%s", config.transcription_engine)

            result = run_pipeline(
                cfg=config,
                log=log,
                progress=lambda v: progress_bar.progress(v),
                warn=st.warning,
            )

            st.session_state.app_last_run_dir = result.run_dir
            st.session_state.app_trigger_zip_autodl = result.trigger_zip_autodl
            st.session_state.app_last_errors = result.last_errors
            st.session_state.app_last_gcs_result = result.gcs_result
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
