# DN Studio

Streamlit app for meeting transcription (Faster Whisper or AssemblyAI), BPD schema/populate prompts, GAP analysis, and JSON-to-DOCX/Excel export with Gemini.

## Prerequisites

- **Python 3.12+**
- **FFmpeg** (for extracting audio from video; required for transcription)
- **Google Gemini access** (either mode):
  - Vertex mode (`PROJECT_ID` + `LOCATION`, with ADC via `gcloud auth application-default login`), or
  - API key mode (`GOOGLE_API_KEY` or `GEMINI_API_KEY`)

## Local setup with `uv`

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from the repo root:

```bash
uv venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

uv pip install -r requirements.txt
```

Run the app:

```bash
streamlit run app.py
```

Open `http://localhost:8501`.

The Meetings section supports both local uploads and optional GCS URIs (`gs://...`) for media or transcript `.json` files.
Transcription engine is selectable in the UI (`Transcription engine` expander above `LLM parameters`): `Whisper (local)` or `AssemblyAI (API)`.
Meeting processing is strict sequential order: input 1 is processed and saved to transcript JSON before input 2 starts, and so on through input n.
For media files, the backend first creates an optimized temporary audio derivative (engine-aware), transcribes it with the selected engine, saves JSON, and deletes temporary media before moving to the next item.

## Backup and Claude bundle (`zip_it.py`)

Use `zip_it.py` to create a code backup zip and a Claude-editable text bundle:

```bash
python zip_it.py
```

Non-interactive backup name:

```bash
python zip_it.py --suffix mytag
```

Decode an edited bundle back into files:

```bash
python zip_it.py --decode path/to/bundle.txt
```

Set Gemini credentials (one of):

- Vertex mode: `gcloud auth application-default login` (no key file; good for local dev), or
- Vertex mode with mounted key: `GOOGLE_APPLICATION_CREDENTIALS` pointing at a service account JSON key, or
- API key mode: set `GOOGLE_API_KEY` (or `GEMINI_API_KEY`).

If you choose `AssemblyAI (API)` in the UI, set:

- `ASSEMBLYAI_API_KEY` with your AssemblyAI API key.

## Node.js (`npm`) for DOCX export

The BPD DOCX path uses `templates/bpd_template.js` and the `docx` package.

```bash
cd templates
npm install
```

The UI runs Node when you convert `r2_populated.json` to `doctype_doc.docx`.

## GAP Analysis view

The app now has two views:

- `Doc` — existing BPD flow (`meeting-input.json` -> `r2_populated.json` -> `doctype_doc.docx`)
- `GAP Analysis` — SAP GAP flow from run-folder artifacts

In `GAP Analysis`:

- Select a run folder under `run/`
- The app reads `meeting-input.json` from that run folder
- Click `Run GAP Analysis` to generate:
  - `SAP_Gap_Analysis.docx`
  - `step1_requirements.json`
  - `step2_3_4_normalized_assessed.json`
  - `step5_ricefw_validated.json`
  - `step_final.json`
  - `_pipeline_output.json`
- Click `Generate Excel Report` (enabled when `step_final.json` exists) to generate:
  - `SAP_Gap_Analysis.xlsx`

All GAP outputs are saved in the same selected run folder.

## Docker

Published image (Docker Hub): **`sabs1010/dn-studio:v4`**

```bash
docker pull sabs1010/dn-studio:v4
```

*(Typical size on disk ~2.4GB uncompressed / ~565MB compressed — varies by host.)*

### Build (optional)

From the repository root (where the `Dockerfile` is):

```bash
docker build -t sabs1010/dn-studio:v4 .
```

### Run (Vertex: mount credentials — no `sa.json` file needed)

On the **host**, log in once so Google libraries can use Application Default Credentials (your user must be able to use Vertex on **`dn-studio-01`**):

```bash
gcloud auth application-default login
```

That creates a file on the host. **Mount that file** into the container and point `GOOGLE_APPLICATION_CREDENTIALS` at the path **inside** the container (read-only):

**Linux / macOS:**

```bash
docker run --rm -p 8501:8501 `
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/application_default_credentials.json `
  -e ASSEMBLYAI_API_KEY=your_assemblyai_api_key `
  -v "$HOME/.config/gcloud/application_default_credentials.json:/run/secrets/application_default_credentials.json:ro" `
  sabs1010/dn-studio:v4
```

**Windows PowerShell** (ADC is usually under `%APPDATA%\gcloud\`):

```powershell
docker run --rm -p 8501:8501 `
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/application_default_credentials.json `
  -e ASSEMBLYAI_API_KEY=your_assemblyai_api_key `
  -v "${env:APPDATA}\gcloud\application_default_credentials.json:/run/secrets/application_default_credentials.json:ro" `
  sabs1010/dn-studio:v4
```

If that path does not exist, run `gcloud auth application-default login` again, or check `gcloud info` for the active config paths.

Then open `http://localhost:8501`.

### Run (optional: service account JSON key)

For servers or CI, use a key for **`sa-dn-studio@dn-studio-01.iam.gserviceaccount.com`**, mount it as `sa.json`, and set:

```bash
docker run --rm -p 8501:8501 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/sa.json \
  -e ASSEMBLYAI_API_KEY=your_assemblyai_api_key \
  -v "$PWD/sa.json:/run/secrets/sa.json:ro" \
  sabs1010/dn-studio:v4
```

**Windows PowerShell:**

```powershell
docker run --rm -p 8501:8501 `
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/sa.json `
  -e ASSEMBLYAI_API_KEY=your_assemblyai_api_key `
  -v "${PWD}\sa.json:/run/secrets/sa.json:ro" `
  sabs1010/dn-studio:v4
```

Create the key (requires IAM permission):

```bash
gcloud iam service-accounts keys create sa.json \
  --iam-account=sa-dn-studio@dn-studio-01.iam.gserviceaccount.com \
  --project=dn-studio-01
```

### Push your own build to Docker Hub

Log in, tag, and push (example: your Docker Hub user `sabs1010`):

```bash
docker login
docker push sabs1010/dn-studio:v4
```

## Project layout (short)

- `app.py` — Streamlit UI (one-click BPD pipeline)
- `backend/` — prompts, LLM helper, ingestion, runner, transcription, context building
- `prompts/bpd/` — BPD prompt templates
- `templates/` — Node DOCX generator

## License

See repository owner for license terms.


- Test Files
```
gs://meeting-recordings-dn-studio-01/EMS_20251021.mp4
gs://meeting-recordings-dn-studio-01/EMS_20251106.mp4

gs://meeting-recordings-dn-studio-01/EMS_20251106.json
gs://meeting-recordings-dn-studio-01/EMS_20260416.json


gs://meeting-recordings-dn-studio-01/Workshop 1 SAP Utilities Orientation-20251014_130129-Meeting Recording.mp4
gs://meeting-recordings-dn-studio-01/Workshop 2 Organizational Entities & Master data-20251015_093246-Meeting Recording.mp4
gs://meeting-recordings-dn-studio-01/Workshop 3 Energy Data (Consumption)-20251015_140211-Meeting Recording.mp4


Business Process Overview
Business Process Design
Business Process Flows
Business Process Controls
Business Process Impacts
Business Process - Very Detailed Flowchart
Business Process - RICEFW - GAP ANALYSIS


https://www.assemblyai.com/dashboard/transcription-history


```