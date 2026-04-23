from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

from backend.json_utils import normalize_json_text


def load_assemblyai_transcribe_fn(app_root: Path):
    module_path = app_root / "backend" / "assebly-transcribe.py"
    if not module_path.is_file():
        raise FileNotFoundError(f"AssemblyAI transcriber module not found: {module_path}")

    spec = importlib.util.spec_from_file_location("backend.assembly_transcribe_runtime", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load AssemblyAI transcriber module from: {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    transcribe_fn = getattr(module, "transcribe_media_file", None)
    if not callable(transcribe_fn):
        raise RuntimeError("AssemblyAI module must expose callable `transcribe_media_file`.")
    return transcribe_fn


def load_meeting_records_from_run(run_path: Path) -> list[dict]:
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


def validate_r1_schema_text(schema_text: str, expected_h1_headers: list[str]) -> str:
    normalized = normalize_json_text(schema_text)
    obj = json.loads(normalized)
    if not isinstance(obj, dict):
        raise ValueError("r1_schema must be a JSON object.")

    if "structure" not in obj or not isinstance(obj.get("structure"), list):
        nested = None
        for key in ("schema", "r1_schema", "output", "result", "data"):
            candidate = obj.get(key)
            if isinstance(candidate, dict) and isinstance(candidate.get("structure"), list):
                nested = candidate
                break
        if nested is None:
            for value in obj.values():
                if isinstance(value, dict) and isinstance(value.get("structure"), list):
                    nested = value
                    break
        if nested is not None:
            obj = {
                "document_type": nested.get("document_type") or obj.get("document_type") or "Business Process Document (BPD)",
                "schema_phase": nested.get("schema_phase") or obj.get("schema_phase") or "DESIGN",
                "authoring_mode": nested.get("authoring_mode") or obj.get("authoring_mode") or "AI",
                "structure": nested.get("structure", []),
            }

    if "structure" not in obj or not isinstance(obj["structure"], list):
        raise ValueError("r1_schema must contain a top-level 'structure' array.")

    actual_h1 = []
    for item in obj["structure"]:
        if isinstance(item, dict) and str(item.get("[TAG]", "")).upper() == "H1":
            actual_h1.append(str(item.get("name", "")).strip().lower())

    required = [h.strip().lower() for h in expected_h1_headers if h.strip()]
    missing = [h for h in required if h not in actual_h1]
    if missing:
        raise ValueError("r1_schema is incomplete; missing H1 sections: " + ", ".join(missing))
    return json.dumps(obj, ensure_ascii=False, indent=2)
