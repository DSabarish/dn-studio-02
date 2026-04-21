from __future__ import annotations

import io
import json
import logging
import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from google.cloud import storage

logger = logging.getLogger("dn_studio.artifacts")


def normalize_json_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Empty JSON text.")
    decoder = json.JSONDecoder()

    # Fast path: already valid JSON.
    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # Common LLM output: fenced blocks.
    if "```" in text:
        segments = text.split("```")
        for seg in segments:
            candidate = seg.strip()
            if not candidate:
                continue
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                obj = json.loads(candidate)
                return json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                # Try to decode the first valid JSON fragment inside this segment.
                for i, ch in enumerate(candidate):
                    if ch not in "{[":
                        continue
                    try:
                        obj, _ = decoder.raw_decode(candidate, idx=i)
                        return json.dumps(obj, ensure_ascii=False, indent=2)
                    except Exception:
                        continue

    # Fallback: scan full text for first decodable JSON object/array.
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _ = decoder.raw_decode(text, idx=i)
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            continue

    raise ValueError("Unable to extract valid JSON from model output (model likely returned malformed JSON).")


def build_run_zip(run_path: Path) -> bytes:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(p for p in run_path.rglob("*") if p.is_file()):
            zf.write(file_path, file_path.relative_to(run_path))
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def upload_run_artifacts_to_gcs(run_path: Path, bucket_name: str, signed_url_ttl_minutes: int) -> dict:
    logger.info(
        "Uploading run artifacts to GCS started | run_path=%s | bucket=%s | ttl_minutes=%s",
        str(run_path),
        bucket_name,
        int(signed_url_ttl_minutes),
    )
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    run_prefix = f"runs/{run_path.name}"
    now = datetime.utcnow().isoformat() + "Z"
    ttl = timedelta(minutes=max(1, int(signed_url_ttl_minutes)))
    artifacts: list[dict] = []

    def _signed_url(blob) -> str | None:
        try:
            return blob.generate_signed_url(expiration=ttl, method="GET")
        except Exception:
            return None

    for file_path in sorted(p for p in run_path.rglob("*") if p.is_file()):
        rel = file_path.relative_to(run_path).as_posix()
        object_name = f"{run_prefix}/{rel}"
        blob = bucket.blob(object_name)
        blob.upload_from_filename(str(file_path))
        logger.info("Uploaded artifact | path=%s | size_bytes=%s", rel, file_path.stat().st_size)
        artifacts.append(
            {
                "path": rel,
                "gcs_uri": f"gs://{bucket_name}/{object_name}",
                "size_bytes": file_path.stat().st_size,
                "created_at_utc": now,
                "signed_url": _signed_url(blob),
            }
        )

    run_zip_name = f"{run_prefix}/run_all_files.zip"
    run_zip_blob = bucket.blob(run_zip_name)
    run_zip_bytes = build_run_zip(run_path)
    run_zip_blob.upload_from_string(run_zip_bytes, content_type="application/zip")
    logger.info("Uploaded run zip | size_bytes=%s", len(run_zip_bytes))
    artifacts.append(
        {
            "path": "run_all_files.zip",
            "gcs_uri": f"gs://{bucket_name}/{run_zip_name}",
            "size_bytes": len(run_zip_bytes),
            "created_at_utc": now,
            "signed_url": _signed_url(run_zip_blob),
        }
    )

    manifest = {
        "run_id": run_path.name,
        "bucket": bucket_name,
        "prefix": run_prefix,
        "created_at_utc": now,
        "signed_url_ttl_minutes": int(signed_url_ttl_minutes),
        "artifacts": artifacts,
    }
    manifest_name = f"{run_prefix}/manifest.json"
    manifest_blob = bucket.blob(manifest_name)
    manifest_blob.upload_from_string(
        json.dumps(manifest, indent=2),
        content_type="application/json",
    )
    logger.info("Uploaded manifest | artifact_count=%s", len(artifacts))
    return {
        "bucket": bucket_name,
        "prefix": run_prefix,
        "manifest_gcs_uri": f"gs://{bucket_name}/{manifest_name}",
        "manifest_signed_url": _signed_url(manifest_blob),
        "artifacts": artifacts,
    }


def build_step_timings_payload(step_timer, pipeline_started_at: datetime, pipeline_ended_at: datetime) -> dict:
    return {
        "pipeline": {
            "started_at_utc": pipeline_started_at.isoformat() + "Z",
            "ended_at_utc": pipeline_ended_at.isoformat() + "Z",
            "elapsed_seconds": round((pipeline_ended_at - pipeline_started_at).total_seconds(), 3),
        },
        "steps": [
            {
                "step": t.step,
                "name": t.name,
                "started_at_utc": t.started_at_utc,
                "ended_at_utc": t.ended_at_utc,
                "elapsed_seconds": round(t.elapsed_seconds, 3),
                "success": t.success,
                "error": t.error,
            }
            for t in step_timer.as_list()
        ],
    }


def convert_json_to_docx(base_dir: Path, r2_path: Path) -> tuple[Path | None, str]:
    template_script = base_dir / "templates" / "bpd_template.js"
    docx_output = r2_path.parent / "doctype_doc.docx"
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
