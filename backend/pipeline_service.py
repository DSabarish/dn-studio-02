from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from backend import build_context, build_prompt, transcriptions
from backend.artifacts import (
    build_step_timings_payload,
    convert_json_to_docx,
    normalize_json_text,
    upload_run_artifacts_to_gcs,
)
from backend.ingest import load_gcs_meeting_inputs, load_uploaded_meeting_inputs
from backend.runner import process_meetings, save_bytes_to_folder
from backend.llm_client import run_prompt_file, run_prompt_text
from backend.timing import StepTimer
from backend.pipeline_helpers import (
    load_assemblyai_transcribe_fn,
    load_meeting_records_from_run,
    validate_r1_schema_text,
)

logger = logging.getLogger("dn_studio.pipeline_service")
MAX_DOC_FILES = 20


@dataclass
class PipelineConfig:
    run_dir: Path
    runs_bucket: str
    run_mode: str
    resume_run_dir: str
    meeting_files: list
    gcs_uris_raw: str
    context_files: list
    meeting_dates: dict[int, date]
    business_context: str
    h1_headers_raw: str
    schema_temperature: float
    schema_max_tokens: int
    r2_temperature: float
    r2_max_tokens: int
    transcription_engine: str
    assembly_parallelism: int
    keep_local_runs: bool
    upload_to_gcs: bool
    signed_url_ttl_minutes: int
    app_root: Path


@dataclass
class PipelineResult:
    run_dir: str
    trigger_zip_autodl: str
    last_errors: list[str]
    gcs_result: dict | None


def run_pipeline(
    cfg: PipelineConfig,
    log: Callable[[str], None],
    progress: Callable[[float], None],
    warn: Callable[[str], None],
) -> PipelineResult:
    pipeline_started_at = datetime.utcnow()
    logger.info("Pipeline run started")

    logger.info(
        "Pipeline config | meetings_uploaded=%s | gcs_uri_count=%s | context_docs=%s | keep_local_runs=%s | upload_to_gcs=%s | engine=%s",
        len(cfg.meeting_files or []),
        len([x for x in cfg.gcs_uris_raw.splitlines() if x.strip()]),
        len(cfg.context_files or []),
        cfg.keep_local_runs,
        cfg.upload_to_gcs,
        cfg.transcription_engine,
    )

    step_timer = StepTimer()
    resume_run_raw = str(cfg.resume_run_dir or "").strip()

    log("**Step 1/8** — Selecting run folder…")
    if cfg.run_mode == "Resume run":
        if not resume_run_raw:
            raise RuntimeError("Select an active run folder to resume.")
        session_base = Path(resume_run_raw)
        if not session_base.is_dir():
            raise RuntimeError(f"Run folder not found: {session_base}")
        elapsed = 0.0
        log(f"Resuming existing run folder `{session_base}`")
    else:
        session_base, elapsed = step_timer.run(1, "Creating run folder", lambda: build_prompt.create_new_run_folder(cfg.run_dir))
    log(f"Using `{session_base}` ({elapsed:.2f}s)")

    # Persist a timestamped live log for later debugging and performance analysis.
    log_file_path = session_base / "pipeline_live.log"
    user_log = log

    def _emit(message: str) -> None:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"{ts} | {message}"
        try:
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            with log_file_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.exception("Failed to write live pipeline log line.")
        user_log(line)

    log = _emit
    log(f"Live pipeline log file: `{log_file_path}`")

    log("**Step 2/8** — Processing meetings (upload + GCS, transcribe / normalize)…")
    transcripts_dir = session_base / "transcripts"
    existing_transcripts = sorted(transcripts_dir.glob("*.json")) if transcripts_dir.is_dir() else []
    if cfg.run_mode == "Resume run" and existing_transcripts:
        meeting_records = load_meeting_records_from_run(session_base)
        last_errors: list[str] = []
        log(f"Step 2 skipped; reusing {len(existing_transcripts)} transcript(s) from `{transcripts_dir}`")
    else:
        def step_2():
            if cfg.transcription_engine == "AssemblyAI (API)":
                transcribe_fn = load_assemblyai_transcribe_fn(cfg.app_root)
                engine_label = "AssemblyAI (API)"
            else:
                transcribe_fn = transcriptions.transcribe
                engine_label = "Whisper (local)"

            log(f"Active transcription engine: **{engine_label}**")
            log(f"Active transcribe function: `{transcribe_fn.__module__}.{transcribe_fn.__name__}`")
            logger.info(
                "Step 2 transcriber resolved | engine=%s | function=%s.%s",
                engine_label,
                transcribe_fn.__module__,
                transcribe_fn.__name__,
            )

            uploaded_inputs = load_uploaded_meeting_inputs(cfg.meeting_files or [])
            gcs_inputs = load_gcs_meeting_inputs(cfg.gcs_uris_raw)
            all_inputs = uploaded_inputs + gcs_inputs
            return process_meetings(
                meeting_inputs=all_inputs,
                session_base=session_base,
                transcribe_fn=transcribe_fn,
                transcription_engine=engine_label,
                assembly_parallelism=int(max(1, cfg.assembly_parallelism)),
                meeting_dates=cfg.meeting_dates,
                log=log,
                progress=progress,
            )

        process_result, elapsed = step_timer.run(2, "Process meetings", step_2)
        if process_result.errors:
            for e in process_result.errors:
                warn(e)
        if not process_result.meeting_records:
            raise RuntimeError("No meetings were produced. Fix errors above and retry.")
        last_errors = process_result.errors
        meeting_records = process_result.meeting_records
        log(f"Step 2 completed in {elapsed:.2f}s")

    log("**Step 3/8** — Building context.md from uploaded documents…")
    context_path = session_base / "context.md"
    if cfg.run_mode == "Resume run" and context_path.is_file():
        context_md = context_path.read_text(encoding="utf-8", errors="ignore")
        log(f"Step 3 skipped; reusing `{context_path}`")
    else:
        def step_3():
            docs_dir = session_base / "docs_input"
            docs_dir.mkdir(parents=True, exist_ok=True)
            saved_docs = []
            for doc in (cfg.context_files or [])[:MAX_DOC_FILES]:
                doc.seek(0)
                saved_docs.append(save_bytes_to_folder(doc.name, doc.read(), docs_dir))
            context_md_inner = build_context.build_context_from_files(saved_docs, process_images=True)
            context_md_inner = f"# Document Type\n\nBPD\n\n{context_md_inner}"
            context_path_inner = session_base / "context.md"
            context_path_inner.write_text(context_md_inner, encoding="utf-8")
            return context_md_inner, context_path_inner

        (context_md, context_path), elapsed = step_timer.run(3, "Build context.md", step_3)
        log(f"Saved `{context_path}` ({elapsed:.2f}s)")

    h1_headers = [line.strip() for line in cfg.h1_headers_raw.splitlines() if line.strip()]
    if not h1_headers:
        raise RuntimeError("Add at least one H1 header.")

    log("**Step 4/8** — Building schema prompt and meeting-input.json…")
    schema_prompt_path = session_base / "debug-prompt-schema.md"
    if cfg.run_mode == "Resume run" and schema_prompt_path.is_file():
        prompt_schema = {
            "prompt": schema_prompt_path.read_text(encoding="utf-8", errors="ignore"),
            "prompt_path": str(schema_prompt_path),
        }
        log(f"Step 4 skipped; reusing `{schema_prompt_path}`")
    else:
        prompt_schema, elapsed = step_timer.run(
            4,
            "Build schema prompt",
            lambda: build_prompt.build_bpd_schema_prompt(
                business_context=cfg.business_context,
                h1_headers=h1_headers,
                meetings=meeting_records,
                run_base_dir=cfg.run_dir,
                run_dir=session_base,
            ),
        )
        log(f"Wrote `{prompt_schema['prompt_path']}` ({elapsed:.2f}s)")

    log("**Step 5/8** — Calling model for r1_schema.json…")

    def step_5():
        base_prompt = prompt_schema["prompt"]
        raw = run_prompt_text(
            prompt_text=base_prompt,
            temperature=cfg.schema_temperature,
            max_output_tokens=cfg.schema_max_tokens,
        )
        try:
            return validate_r1_schema_text(raw, h1_headers)
        except Exception:
            retry_prompt = (
                base_prompt
                + "\n\nIMPORTANT: Return ONLY one valid JSON object with top-level keys "
                  "'document_type', 'schema_phase', 'authoring_mode', and 'structure'. "
                  "The 'structure' must include all required H1 headers."
            )
            retry_raw = run_prompt_text(
                prompt_text=retry_prompt,
                temperature=cfg.schema_temperature,
                max_output_tokens=cfg.schema_max_tokens,
            )
            try:
                return validate_r1_schema_text(retry_raw, h1_headers)
            except Exception:
                # Final repair pass: force reformat of prior model output into required schema.
                repair_prompt = (
                    "Reformat the following model output into EXACT required JSON schema.\n"
                    "Rules:\n"
                    "1) Return ONLY valid JSON.\n"
                    "2) Top-level keys must be exactly: document_type, schema_phase, authoring_mode, structure.\n"
                    "3) structure must be an array.\n"
                    "4) Preserve intent; do not add prose.\n\n"
                    f"Expected H1 headers:\n{chr(10).join(h1_headers)}\n\n"
                    "Model output to repair:\n"
                    f"{retry_raw}"
                )
                repaired_raw = run_prompt_text(
                    prompt_text=repair_prompt,
                    temperature=0.0,
                    max_output_tokens=cfg.schema_max_tokens,
                )
                return validate_r1_schema_text(repaired_raw, h1_headers)

    schema_path = session_base / "r1_schema.json"
    if cfg.run_mode == "Resume run" and schema_path.is_file():
        generated_json = schema_path.read_text(encoding="utf-8", errors="ignore")
        log(f"Step 5 skipped; reusing `{schema_path}`")
    else:
        generated_json, elapsed = step_timer.run(5, "Call model for r1_schema.json", step_5)
        schema_path.write_text(generated_json, encoding="utf-8")
        log(f"Saved `{schema_path}` ({elapsed:.2f}s)")

    log("**Step 6/8** — Building populate prompt…")
    populate_prompt_path = session_base / "debug-prompt-populate-content.md"
    if cfg.run_mode == "Resume run" and populate_prompt_path.is_file():
        log(f"Step 6 skipped; reusing `{populate_prompt_path}`")
    else:
        pop_result, elapsed = step_timer.run(
            6,
            "Build populate prompt",
            lambda: build_prompt.build_bpd_pop_prompt(
                business_context=cfg.business_context or "",
                schema_json=generated_json,
                meetings=meeting_records,
                run_base_dir=cfg.run_dir,
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
    if cfg.run_mode == "Resume run" and r2_path.is_file():
        r2_text = r2_path.read_text(encoding="utf-8", errors="ignore")
        log(f"Step 7 skipped; reusing `{r2_path}`")
    else:
        r2_text, elapsed = step_timer.run(
            7,
            "Call model for r2_populated.json",
            lambda: run_prompt_file(
                prompt_path=populate_prompt_path,
                temperature=cfg.r2_temperature,
                max_output_tokens=cfg.r2_max_tokens,
            ),
        )
        r2_text = normalize_json_text(r2_text)
        r2_path.write_text(r2_text, encoding="utf-8")
        log(f"Saved `{r2_path}` ({elapsed:.2f}s)")

    log("**Step 8/8** — JSON -> DOCX (optional)…")

    def step_8():
        return convert_json_to_docx(cfg.app_root, r2_path)

    existing_docx = session_base / "doctype_doc.docx"
    if cfg.run_mode == "Resume run" and existing_docx.is_file():
        docx_output, docx_message = existing_docx, ""
        log(f"Step 8 skipped; reusing `{existing_docx}`")
    else:
        (docx_output, docx_message), elapsed = step_timer.run(8, "JSON -> DOCX", step_8)
        if docx_output is None:
            warn("DOCX conversion failed (install Node.js and run `npm install docx` in `templates/`).")
            if docx_message:
                warn(docx_message)
        else:
            log(f"Saved `{docx_output}` ({elapsed:.2f}s)")

    timings_path = session_base / "step_timings.json"
    pipeline_ended_at = datetime.utcnow()
    timings_payload = build_step_timings_payload(step_timer, pipeline_started_at, pipeline_ended_at)
    timings_path.write_text(json.dumps(timings_payload, indent=2), encoding="utf-8")
    log(f"Saved `{timings_path}`")

    gcs_result = None
    if cfg.upload_to_gcs:
        log("Uploading run artifacts to GCS…")
        gcs_result = upload_run_artifacts_to_gcs(
            run_path=session_base,
            bucket_name=cfg.runs_bucket,
            signed_url_ttl_minutes=cfg.signed_url_ttl_minutes,
        )
        log(f"Uploaded artifacts to `gs://{gcs_result['bucket']}/{gcs_result['prefix']}`")

    trigger_zip_autodl = ""
    transcripts_done = session_base / "transcripts"
    if transcripts_done.is_dir() and any(transcripts_done.glob("*.json")):
        trigger_zip_autodl = str(session_base.resolve())

    final_run_dir = str(session_base)
    if not cfg.keep_local_runs:
        trigger_zip_autodl = ""
        final_run_dir = ""
        shutil.rmtree(session_base, ignore_errors=True)
        log("Local run artifacts removed (toggle is off).")

    logger.info("Pipeline run completed successfully")
    return PipelineResult(
        run_dir=final_run_dir,
        trigger_zip_autodl=trigger_zip_autodl,
        last_errors=last_errors,
        gcs_result=gcs_result,
    )
