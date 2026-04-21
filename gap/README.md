# GAP Analyser (`gap_analyser.py`)

End-to-end SAP gap analysis pipeline that:
- reads meeting transcripts (`.json` or `.txt`)
- runs multi-step analysis with Vertex AI Gemini
- writes intermediate JSON artifacts
- exports a styled `.docx` report via `js_template.js`

## What it does

The pipeline runs these stages in order:
1. Requirement extraction
2. Requirement normalization
3. SAP capability assessment + gap identification
4. RICEFW classification + solution strategy
5. No-gap confirmations + open actions
6. DOCX generation

Output artifacts:
- `step1_requirements.json`
- `step2_normalized.json`
- `step3_capability.json`
- `step5_ricefw.json`
- `step_final.json`
- `_pipeline_output.json`
- final report (for example: `SAP_Gap_Analysis.docx`)

---

## Prerequisites

- Python 3.10+ (3.11+ recommended)
- Node.js 18+ (for `js_template.js`)
- Google Cloud project with Vertex AI enabled
- Auth configured via ADC or service account key

---

## Setup

### 1) Python environment

From this folder:

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install google-cloud-aiplatform vertexai python-docx
```

> `python-docx` is only used as fallback if `js_template.js` is not found.

### 2) Node dependencies (DOCX template renderer)

```bash
npm install
```

This installs the `docx` package used by `js_template.js`.

### 3) Google auth

Use either:

- **ADC** (`gcloud auth application-default login`), or
- service account key:

```bash
set GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\service-account.json
```

---

## Usage

Run from this folder:

```bash
python "gap_analyser.py" ^
  --transcript "meeting-input.json" ^
  --project "dn-studio-01" ^
  --location "us-central1" ^
  --output "SAP_Gap_Analysis.docx" ^
  --dump-json
```

PowerShell multi-line alternative:

```powershell
python "gap_analyser.py" `
  --transcript "meeting-input.json" `
  --project "dn-studio-01" `
  --location "us-central1" `
  --output "SAP_Gap_Analysis.docx" `
  --dump-json
```

---

## Input format notes

`gap_analyser.py` supports:
- plain text transcript files, and
- EMS-style JSON arrays with `transcript_json.transcript` entries.

For EMS-style input, it combines all meetings in the array into one analysis corpus.

---

## Output rendering behavior

- If `js_template.js` exists, export uses Node renderer (preferred, styled output).
- If `js_template.js` is missing, it falls back to Python (`python-docx`) renderer.

---

## Troubleshooting

- **`LLM returned non-JSON output`**
  - The script already retries malformed JSON responses up to 3 times.
  - Re-run if transient.

- **`js_template.js failed / Cannot find module 'docx'`**
  - Run `npm install` in this folder.

- **Vertex auth errors**
  - Verify `--project`, `--location`, and Google credentials/ADC.

- **Encoding warnings on Windows**
  - Non-fatal. The script uses ASCII-safe console output.

