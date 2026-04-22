from __future__ import annotations

from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from backend.pipeline_service import PipelineConfig, run_pipeline


def main() -> int:
    load_dotenv()

    gcs_uris_raw = "\n".join(
        [
            "gs://meeting-recordings-dn-studio-01/Workshop 1 SAP Utilities Orientation-20251014_130129-Meeting Recording.mp4",
            "gs://meeting-recordings-dn-studio-01/Workshop 2 Organizational Entities & Master data-20251015_093246-Meeting Recording.mp4",
            "gs://meeting-recordings-dn-studio-01/Workshop 3 Energy Data (Consumption)-20251015_140211-Meeting Recording.mp4",
        ]
    )

    cfg = PipelineConfig(
        run_dir=Path("run"),
        runs_bucket="dn-studio-runs-01",
        run_mode="New run",
        resume_run_dir="",
        meeting_files=[],
        gcs_uris_raw=gcs_uris_raw,
        context_files=[],
        meeting_dates={1: date.today(), 2: date.today(), 3: date.today()},
        business_context="BPD generation test from GCS media (AssemblyAI)",
        h1_headers_raw="\n".join(
            [
                "Business Process Overview",
                "Business Process Design",
                "Business Process Flows",
                "Business Process Controls",
                "Business Process Impacts",
            ]
        ),
        schema_temperature=0.2,
        schema_max_tokens=8192,
        r2_temperature=0.2,
        r2_max_tokens=65000,
        transcription_engine="AssemblyAI (API)",
        keep_local_runs=True,
        upload_to_gcs=False,
        signed_url_ttl_minutes=30,
        app_root=Path(__file__).resolve().parent.parent,
    )

    res = run_pipeline(cfg=cfg, log=print, progress=lambda v: None, warn=print)
    runp = Path(res.run_dir)
    docx = runp / "doctype_doc.docx"
    print("RUN_DIR", res.run_dir)
    print("DOCX_EXISTS", docx.is_file())
    print("DOCX_PATH", str(docx))
    return 0 if docx.is_file() else 2


if __name__ == "__main__":
    raise SystemExit(main())

