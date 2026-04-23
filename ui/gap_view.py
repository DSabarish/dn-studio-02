from __future__ import annotations

import contextlib
import io
import os
import re
import zipfile
from pathlib import Path

import streamlit as st

from config import config
from backend import build_prompt
from backend.artifacts import sync_run_folder_from_gcs, upload_run_artifacts_to_gcs
from backend.pipeline_to_excel import build_excel as build_gap_excel
from backend.sap_gap_analyser_updated import run_pipeline as run_gap_pipeline
from ui.shared import cached_list_run_folders_in_gcs


def _build_gap_docx_excel_zip(docx_path: Path, excel_path: Path) -> bytes:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(docx_path, "SAP_Gap_Analysis.docx")
        zf.write(excel_path, "SAP_Gap_Analysis.xlsx")
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def _keep_gap_log_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    lowered = text.lower()
    noise_tokens = (
        "warning",
        "deprecation",
        "deprecated",
        "pkg_resources",
        "tensorflow",
        "grpc",
        "absl",
        "urllib3",
    )
    return not any(token in lowered for token in noise_tokens)


class _StreamlitLogWriter:
    def __init__(self, placeholder, rendered_logs: list[str]) -> None:
        self._buf = ""
        self._placeholder = placeholder
        self._rendered_logs = rendered_logs

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buf += data
        parts = re.split(r"\r?\n", self._buf)
        self._buf = parts.pop()
        for raw_line in parts:
            line = raw_line.rstrip()
            if _keep_gap_log_line(line):
                self._rendered_logs.append(line)
                self._placeholder.code("\n".join(self._rendered_logs[-200:]), language="text")
        return len(data)

    def flush(self) -> None:
        tail = self._buf.strip()
        if tail and _keep_gap_log_line(tail):
            self._rendered_logs.append(tail)
            self._placeholder.code("\n".join(self._rendered_logs[-200:]), language="text")
        self._buf = ""


def render_gap_view(run_dir: Path, runs_bucket: str) -> None:
    st.subheader("GAP Analysis")
    if "app_gap_last_gcs_result" not in st.session_state:
        st.session_state.app_gap_last_gcs_result = None

    local_gap_run_labels = [p.name for p in build_prompt.list_bpd_run_dirs(run_dir)]
    try:
        gcs_gap_run_labels = cached_list_run_folders_in_gcs(runs_bucket)
    except Exception as exc:
        st.warning(f"Could not load run folders from GCS: {exc}")
        gcs_gap_run_labels = []

    gap_run_labels = sorted(set(gcs_gap_run_labels) | set(local_gap_run_labels), reverse=True)
    gap_run_labels_in_gcs = set(gcs_gap_run_labels)

    with st.container(border=True):
        if not gap_run_labels:
            st.warning("No run folders found locally in `run/` or in GCS under `runs/`.")
            return

        selected_gap_run = st.selectbox(
            "Select run folder",
            options=gap_run_labels,
            key="app_gap_run_label",
        )
        selected_gap_path = (run_dir / selected_gap_run).resolve()
        transcript_path = selected_gap_path / "meeting-input.json"
        output_docx_path = selected_gap_path / "SAP_Gap_Analysis.docx"
        st.caption(f"Run folder: `{selected_gap_path}`")
        st.caption(f"Input transcript JSON: `{transcript_path}`")
        st.caption(f"Output DOCX: `{output_docx_path}`")

        gap_run_clicked = st.button("Run GAP Analysis", type="primary")
        if gap_run_clicked:
            if selected_gap_run in gap_run_labels_in_gcs:
                try:
                    with st.status("Syncing selected run folder from GCS…", expanded=False):
                        selected_gap_path = sync_run_folder_from_gcs(
                            bucket_name=runs_bucket,
                            run_name=selected_gap_run,
                            local_run_base=run_dir,
                        )
                        transcript_path = selected_gap_path / "meeting-input.json"
                except Exception as exc:
                    st.error(f"Failed to sync run folder from GCS: {exc}")
                    transcript_path = selected_gap_path / "meeting-input.json"

            if not transcript_path.is_file():
                st.error(f"`meeting-input.json` not found in `{selected_gap_path}`.")
            else:
                log_placeholder = st.empty()
                rendered_logs: list[str] = []
                with st.status("Running GAP Analysis…", expanded=True):
                    try:
                        project_id = (os.getenv("PROJECT_ID") or config.GCP_PROJECT_ID).strip()
                        location = (os.getenv("LOCATION") or config.GCP_LOCATION).strip()
                        log_writer = _StreamlitLogWriter(log_placeholder, rendered_logs)
                        with contextlib.redirect_stdout(log_writer), contextlib.redirect_stderr(log_writer):
                            run_gap_pipeline(
                                transcript_path=str(transcript_path),
                                project=project_id,
                                location=location,
                                output_path=str(output_docx_path),
                                dump_json=True,
                            )
                            final_json_path = selected_gap_path / "step_final.json"
                            if final_json_path.is_file():
                                build_gap_excel(
                                    json_path=str(final_json_path),
                                    output_path=str(selected_gap_path / "SAP_Gap_Analysis.xlsx"),
                                )
                        log_writer.flush()
                        gcs_result = upload_run_artifacts_to_gcs(
                            run_path=selected_gap_path,
                            bucket_name=runs_bucket,
                            signed_url_ttl_minutes=30,
                        )
                        st.session_state.app_gap_last_gcs_result = gcs_result
                        st.success(f"GAP Analysis completed: `{output_docx_path}`")
                    except Exception as exc:
                        st.error(f"GAP Analysis failed: {exc}")

        if output_docx_path.is_file():
            st.download_button(
                "Download SAP_Gap_Analysis.docx",
                data=output_docx_path.read_bytes(),
                file_name="SAP_Gap_Analysis.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"app_dl_gap_docx_{selected_gap_run}",
            )

        final_json_path = selected_gap_path / "step_final.json"
        gap_excel_path = selected_gap_path / "SAP_Gap_Analysis.xlsx"
        if final_json_path.is_file():
            st.download_button(
                "Download GAP final JSON",
                data=final_json_path.read_bytes(),
                file_name="step_final.json",
                mime="application/json",
                key=f"app_dl_gap_final_json_{selected_gap_run}",
            )
            generate_excel_clicked = st.button(
                "Generate Excel Report",
                key=f"app_generate_gap_excel_{selected_gap_run}",
            )
            if generate_excel_clicked:
                with st.status("Generating GAP Excel report…", expanded=False):
                    try:
                        build_gap_excel(
                            json_path=str(final_json_path),
                            output_path=str(gap_excel_path),
                        )
                        gcs_result = upload_run_artifacts_to_gcs(
                            run_path=selected_gap_path,
                            bucket_name=runs_bucket,
                            signed_url_ttl_minutes=30,
                        )
                        st.session_state.app_gap_last_gcs_result = gcs_result
                        st.success(f"Excel report generated: `{gap_excel_path}`")
                    except Exception as exc:
                        st.error(f"Excel generation failed: {exc}")

        if gap_excel_path.is_file():
            st.download_button(
                "Download SAP_Gap_Analysis.xlsx",
                data=gap_excel_path.read_bytes(),
                file_name="SAP_Gap_Analysis.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"app_dl_gap_excel_{selected_gap_run}",
            )

        if output_docx_path.is_file() and gap_excel_path.is_file():
            gap_pair_zip_name = f"{selected_gap_run}_gap_docx_excel.zip"
            gap_pair_zip_bytes = _build_gap_docx_excel_zip(output_docx_path, gap_excel_path)
            st.download_button(
                "Download DOCX + Excel together (ZIP)",
                data=gap_pair_zip_bytes,
                file_name=gap_pair_zip_name,
                mime="application/zip",
                key=f"app_dl_gap_docx_excel_zip_{selected_gap_run}",
            )

        gap_gcs_result = st.session_state.get("app_gap_last_gcs_result")
        if gap_gcs_result and selected_gap_run in str(gap_gcs_result.get("prefix", "")):
            st.divider()
            st.subheader("GAP — GCS artifacts")
            st.caption(f"Location: `gs://{gap_gcs_result['bucket']}/{gap_gcs_result['prefix']}`")
            gap_folder_url = (
                f"https://console.cloud.google.com/storage/browser/"
                f"{gap_gcs_result['bucket']}/{gap_gcs_result['prefix']}?project={config.GCP_PROJECT_ID}"
            )
            st.markdown(f"[Open GAP run folder in GCS Console]({gap_folder_url})")
