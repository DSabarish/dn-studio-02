# DN Studio

Streamlit app for meeting transcription (Faster Whisper), BPD schema/populate prompts, Vertex AI (Gemini), and JSON-to-DOCX export.

## Prerequisites

- **Python 3.12+**
- **FFmpeg** (for extracting audio from video; required for transcription)
- **Google Cloud**: Vertex AI on project **`dn-studio-01`**. Locally you can use **`gcloud auth application-default login`** (no key file). For Docker, mount that ADC file (see below). Optionally use service account **`sa-dn-studio@dn-studio-01.iam.gserviceaccount.com`** with a JSON key for servers/CI. The app uses Vertex in `backend/simple_llm.py` (`PROJECT_ID` / `LOCATION` — keep them aligned with **`dn-studio-01`**).

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

Set credentials for Vertex (one of):

- `gcloud auth application-default login` (no key file; good for local dev), or
- `GOOGLE_APPLICATION_CREDENTIALS` pointing at a service account JSON key (e.g. for Docker mount or servers).

## Node.js (`npm`) for DOCX export

The BPD DOCX path uses `templates/bpd_template.js` and the `docx` package.

```bash
cd templates
npm install
```

The UI runs Node when you convert `r2_populated.json` to `doctype_doc.docx`.

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
  -v "$HOME/.config/gcloud/application_default_credentials.json:/run/secrets/application_default_credentials.json:ro" `
  sabs1010/dn-studio:v4
```

**Windows PowerShell** (ADC is usually under `%APPDATA%\gcloud\`):

```powershell
docker run --rm -p 8501:8501 `
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/application_default_credentials.json `
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
  -v "$PWD/sa.json:/run/secrets/sa.json:ro" \
  sabs1010/dn-studio:v4
```

**Windows PowerShell:**

```powershell
docker run --rm -p 8501:8501 `
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/sa.json `
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
