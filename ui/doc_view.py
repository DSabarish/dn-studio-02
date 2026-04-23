from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from streamlit.errors import NoSessionContext

from config import config
from backend.artifacts import build_run_zip, sync_run_folder_from_gcs
from backend.pipeline_service import PipelineConfig, run_pipeline
from ui.shared import cached_list_run_folders_in_gcs, meeting_date_key

logger = logging.getLogger("dn_studio.app")


def _init_doc_defaults() -> None:
    defaults = {
        "app_schema_temperature": 0.2,
        "app_schema_max_tokens": 12000,
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
        "app_resume_synced_label": "",
        "app_assembly_parallelism": int(max(1, config.ASSEMBLYAI_PARALLELISM)),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_doc_view(run_dir: Path, runs_bucket: str, default_bpd_h1_headers: str) -> None:
    _init_doc_defaults()

    gcs_run_labels: list[str]
    resume_gcs_error = ""
    try:
        gcs_run_labels = cached_list_run_folders_in_gcs(runs_bucket)
    except Exception as exc:
        gcs_run_labels = []
        resume_gcs_error = str(exc)
    available_run_labels = gcs_run_labels
    available_run_labels_in_gcs = set(gcs_run_labels)

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
            st.caption(f"Listing from `gs://{runs_bucket}/runs/`")
            if resume_gcs_error:
                st.error(f"Could not list GCS run folders: {resume_gcs_error}")
            if not available_run_labels:
                st.warning("No existing run folders found in GCS under `runs/`. Switch to New run.")
            else:
                selected_label = st.selectbox(
                    "Select active run folder",
                    options=available_run_labels,
                    key="app_resume_run_label",
                )
                local_resume_path = (run_dir / selected_label).resolve()
                try:
                    if selected_label in available_run_labels_in_gcs and (
                        st.session_state.get("app_resume_synced_label") != selected_label
                        or not local_resume_path.is_dir()
                    ):
                        with st.status("Syncing selected resume run from GCS…", expanded=False):
                            sync_run_folder_from_gcs(
                                bucket_name=runs_bucket,
                                run_name=selected_label,
                                local_run_base=run_dir,
                            )
                        st.session_state.app_resume_synced_label = selected_label
                except Exception as exc:
                    st.error(f"Failed to sync run folder from GCS: {exc}")
                selected_resume_path = str(local_resume_path)
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
                mk = meeting_date_key(idx)
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
            value=default_bpd_h1_headers,
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
            st.session_state.app_assembly_parallelism = int(
                st.slider(
                    "AssemblyAI parallel workers",
                    min_value=1,
                    max_value=8,
                    value=int(st.session_state.get("app_assembly_parallelism", max(1, config.ASSEMBLYAI_PARALLELISM))),
                    help="Parallel media transcription workers. Increase for speed if your API limits allow it.",
                )
            )
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
        st.caption(f"Runs bucket: {runs_bucket}")
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
            st.session_state.app_run_mode == "New run" and not (meeting_files or gcs_uris_raw.strip())
        ) or (
            st.session_state.app_run_mode == "Resume run" and not st.session_state.app_resume_run_dir
        ),
        help="Creates a new run folder, processes all inputs, and writes outputs there.",
    )

    if run_clicked:
        run_dir.mkdir(parents=True, exist_ok=True)
        progress_bar = st.progress(0.0)
        log_lines: list[str] = []
        live_log_placeholder = st.empty()
        main_thread_id = threading.get_ident()

        def log(msg: str) -> None:
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {msg}"
            log_lines.append(line)
            logger.info(line)
            # Pipeline worker threads can call this logger; only update Streamlit UI
            # from the main script thread to avoid NoSessionContext exceptions.
            if threading.get_ident() != main_thread_id:
                return
            try:
                live_log_placeholder.code(
                    "\n".join(log_lines[-int(max(20, config.LIVE_LOG_MAX_LINES)) :]),
                    language="text",
                )
            except NoSessionContext:
                return

        with st.status("Running pipeline…", expanded=True) as status_box:
            try:
                st.session_state.app_last_gcs_result = None
                meeting_dates_map = {
                    idx: st.session_state.get(meeting_date_key(idx), date.today())
                    for idx in range(
                        1,
                        len(meeting_files or []) + len([x for x in gcs_uris_raw.splitlines() if x.strip()]) + 1,
                    )
                }
                pipeline_cfg = PipelineConfig(
                    run_dir=run_dir,
                    runs_bucket=runs_bucket,
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
                    assembly_parallelism=int(
                        st.session_state.get("app_assembly_parallelism", max(1, config.ASSEMBLYAI_PARALLELISM))
                    ),
                    keep_local_runs=bool(st.session_state.get("app_keep_local_runs", False)),
                    upload_to_gcs=bool(st.session_state.get("app_upload_to_gcs", False)),
                    signed_url_ttl_minutes=int(st.session_state.get("app_signed_url_ttl_minutes", 30)),
                    app_root=Path(__file__).resolve().parent.parent,
                )
                log(f"Selected transcription engine in UI: **{pipeline_cfg.transcription_engine}**")
                logger.info("UI selected transcription engine | engine=%s", pipeline_cfg.transcription_engine)

                result = run_pipeline(
                    cfg=pipeline_cfg,
                    log=log,
                    progress=lambda v: progress_bar.progress(v),
                    warn=st.warning,
                )

                st.session_state.app_last_run_dir = result.run_dir
                st.session_state.app_trigger_zip_autodl = result.trigger_zip_autodl
                st.session_state.app_last_errors = result.last_errors
                st.session_state.app_last_gcs_result = result.gcs_result
                status_box.update(label="Pipeline finished", state="complete")
                if result.run_dir and Path(result.run_dir).is_dir():
                    ui_log_path = Path(result.run_dir) / "pipeline_ui_live.log"
                    ui_log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
                    st.caption(f"Saved UI live log: `{ui_log_path}`")
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
            f"{gcs_result['bucket']}/{gcs_result['prefix']}?project={config.GCP_PROJECT_ID}"
        )
        st.markdown(f"[Open run folder in GCS Console]({folder_url})")
        if gcs_result.get("manifest_signed_url"):
            st.markdown(f"[Download manifest.json]({gcs_result['manifest_signed_url']})")
        artifact_links = [a for a in gcs_result.get("artifacts", []) if a.get("signed_url")]
        if artifact_links:
            for artifact in artifact_links:
                st.markdown(f"- [{artifact['path']}]({artifact['signed_url']})")
