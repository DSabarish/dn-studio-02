# Cursor Agent Skill — Streamlit + Gemini 2.5 Flash + GCS + Cloud Run

## Purpose

Refactor, restructure, and optimize a Python/Streamlit application that uses:
- **Gemini 2.5 Flash** (via `google-genai` or Vertex AI)
- **Google Cloud Storage (GCS)** for file/data persistence
- **Google Cloud Run** for deployment
- **Streamlit** as the UI framework

The goal is clean, fast, non-redundant, well-structured code — written as if Claude coded it from scratch.

---

## Golden Rules (Never Break These)

1. **Read every file before editing a single one.**
2. **Never rename a Streamlit page file** — it changes the URL route and breaks navigation.
3. **Never remove a function without grepping for all its call sites first.**
4. **Never hardcode credentials, project IDs, bucket names, or model names** — everything goes in `config.py`.
5. **Never duplicate logic** — if a block appears twice, it becomes a function.
6. **Cache everything in Streamlit that is expensive** — `@st.cache_data` and `@st.cache_resource` are non-negotiable.

---

## Phase 0 — Read and Map the Codebase

### Get the full file tree
```
run_terminal_cmd: find . -type f -name "*.py" | sort
run_terminal_cmd: find . -type f | grep -v __pycache__ | grep -v .git | sort
```

### Find the heaviest files
```
run_terminal_cmd: find . -name "*.py" | xargs wc -l | sort -rn | head -20
```

### Read ALL of these before writing anything
- Every `.py` file
- `requirements.txt`
- `Dockerfile` (if present)
- `.env` or any secrets/config file
- `app.yaml` or `cloudbuild.yaml` (if present)

### Internally answer before proceeding
1. What does the app do? What is the user flow?
2. How is Gemini called? (direct API, LangChain, Vertex AI?)
3. How is GCS used? (read? write? both? what file types?)
4. What is slow? (any GCS reads or Gemini calls NOT cached?)
5. What is duplicated? (same GCS client setup in 3 files? same prompt built in 2 places?)
6. What is the entry point? (`app.py`, `main.py`, or a `pages/` folder?)
7. Are there any `st.session_state` uses that are inconsistent or missing init?

**Tell the user your findings before restructuring.** A plain-English summary of "here's what your app does and here's what I found" is valuable.

---

## Phase 1 — Target Project Structure

```
project/
├── app.py                   # Streamlit entry point — page config, session init, layout only
├── config.py                # ALL config: GCS, Gemini, Cloud Run, feature flags
├── requirements.txt         # Pinned versions
├── Dockerfile               # Optimised for Cloud Run
├── .env.example             # Placeholder env vars — never commit .env
├── .gitignore
│
├── pages/                   # Streamlit multipage files (if multipage app)
│   ├── 1_page_name.py
│   └── 2_page_name.py
│
├── components/              # Reusable Streamlit UI components
│   ├── __init__.py
│   ├── sidebar.py
│   ├── chat.py              # Chat UI (if applicable)
│   └── cards.py
│
├── services/                # All external service calls — Gemini and GCS only
│   ├── __init__.py
│   ├── gemini.py            # Every Gemini API interaction
│   └── gcs.py               # Every GCS read/write operation
│
├── utils/                   # Pure helper functions — no Streamlit, no API calls
│   ├── __init__.py
│   ├── formatters.py
│   ├── validators.py
│   └── parsers.py
│
└── state/                   # Session state management
    ├── __init__.py
    └── session.py           # init_session_state(), getters, setters
```

### Small app rule
If the app is under ~400 lines total, flat is fine:
```
project/
├── app.py
├── config.py
├── services.py      # gemini + gcs combined
└── utils.py
```
Do not over-engineer small apps.

---

## Phase 2 — `config.py` (Write This First)

```python
# config.py
"""
Centralised configuration for the application.
All values loaded from environment variables.
Defaults are for local dev only — set real values in Cloud Run env vars.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Google Cloud ──────────────────────────────────────────────────────────
    GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "")
    GCP_REGION: str = os.getenv("GCP_REGION", "us-central1")

    # ── GCS ───────────────────────────────────────────────────────────────────
    GCS_BUCKET_NAME: str = os.getenv("GCS_BUCKET_NAME", "")
    GCS_UPLOAD_PREFIX: str = os.getenv("GCS_UPLOAD_PREFIX", "uploads/")
    GCS_OUTPUT_PREFIX: str = os.getenv("GCS_OUTPUT_PREFIX", "outputs/")

    # ── Gemini ────────────────────────────────────────────────────────────────
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MAX_TOKENS: int = int(os.getenv("GEMINI_MAX_TOKENS", "8192"))
    GEMINI_TEMPERATURE: float = float(os.getenv("GEMINI_TEMPERATURE", "0.7"))

    # ── Streamlit App ─────────────────────────────────────────────────────────
    APP_TITLE: str = os.getenv("APP_TITLE", "My App")
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # ── Derived ───────────────────────────────────────────────────────────────
    IS_PRODUCTION: bool = APP_ENV == "production"


config = Config()
```

### Rules for config.py
- Every bucket name, model name, project ID, prefix, timeout → goes here
- No logic, no imports except `os` and `dotenv`
- Cloud Run env vars override everything at runtime — no code changes needed between environments

---

## Phase 3 — `services/gemini.py`

All Gemini interaction lives here. Nothing else imports `google.generativeai` directly.

```python
# services/gemini.py
"""
Gemini 2.5 Flash service layer.
All prompt construction and API calls are centralised here.
"""

import logging
from typing import Optional, Generator
import google.generativeai as genai
import streamlit as st

from config import config

logger = logging.getLogger(__name__)


@st.cache_resource
def get_gemini_client() -> genai.GenerativeModel:
    """
    Initialise and cache the Gemini client for the app lifetime.
    st.cache_resource ensures one client is shared across all sessions.
    """
    genai.configure(api_key=config.GEMINI_API_KEY)
    return genai.GenerativeModel(
        model_name=config.GEMINI_MODEL,
        generation_config=genai.GenerationConfig(
            max_output_tokens=config.GEMINI_MAX_TOKENS,
            temperature=config.GEMINI_TEMPERATURE,
        ),
    )


def generate_text(prompt: str, system_prompt: Optional[str] = None) -> str:
    """
    Send a prompt to Gemini and return the text response.

    Args:
        prompt: The user prompt.
        system_prompt: Optional system-level instruction prepended to the prompt.

    Returns:
        Model's text response.

    Raises:
        RuntimeError: If the API call fails.
    """
    client = get_gemini_client()
    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

    try:
        response = client.generate_content(full_prompt)
        return response.text
    except Exception as e:
        logger.error("Gemini API call failed: %s", e, exc_info=True)
        raise RuntimeError(f"Gemini request failed: {e}") from e


def stream_text(prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
    """
    Stream a Gemini response token by token for real-time display.

    Args:
        prompt: The user prompt.
        system_prompt: Optional system instruction.

    Yields:
        Text chunks as they arrive from the model.
    """
    client = get_gemini_client()
    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

    try:
        for chunk in client.generate_content(full_prompt, stream=True):
            if chunk.text:
                yield chunk.text
    except Exception as e:
        logger.error("Gemini stream failed: %s", e, exc_info=True)
        raise RuntimeError(f"Gemini stream failed: {e}") from e


def generate_from_file(file_bytes: bytes, mime_type: str, prompt: str) -> str:
    """
    Send a file with a prompt to Gemini (image, PDF, etc.).

    Args:
        file_bytes: Raw bytes of the file.
        mime_type: MIME type, e.g. 'image/jpeg', 'application/pdf'.
        prompt: Instruction to accompany the file.

    Returns:
        Model's text response.
    """
    client = get_gemini_client()
    part = {"mime_type": mime_type, "data": file_bytes}

    try:
        response = client.generate_content([part, prompt])
        return response.text
    except Exception as e:
        logger.error("Gemini file request failed: %s", e, exc_info=True)
        raise RuntimeError(f"Gemini file request failed: {e}") from e
```

---

## Phase 4 — `services/gcs.py`

All GCS interaction lives here. No other file touches `google.cloud.storage`.

```python
# services/gcs.py
"""
Google Cloud Storage service layer.
All bucket reads and writes are centralised here.
"""

import logging
from typing import Optional
import streamlit as st
from google.cloud import storage

from config import config

logger = logging.getLogger(__name__)


@st.cache_resource
def get_gcs_client() -> storage.Client:
    """
    Initialise and cache the GCS client for the app lifetime.
    On Cloud Run, uses the attached service account automatically.
    """
    return storage.Client(project=config.GCP_PROJECT_ID)


def upload_bytes(
    data: bytes,
    destination_path: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload bytes to GCS and return the full GCS URI.

    Args:
        data: Raw bytes to upload.
        destination_path: Path within the bucket, e.g. 'uploads/file.pdf'.
        content_type: MIME type of the data.

    Returns:
        GCS URI: gs://bucket/path
    """
    client = get_gcs_client()
    bucket = client.bucket(config.GCS_BUCKET_NAME)
    blob = bucket.blob(destination_path)
    blob.upload_from_string(data, content_type=content_type)
    uri = f"gs://{config.GCS_BUCKET_NAME}/{destination_path}"
    logger.info("Uploaded to %s", uri)
    return uri


def download_bytes(source_path: str) -> bytes:
    """
    Download a file from GCS and return raw bytes.

    Args:
        source_path: Path within the bucket, e.g. 'outputs/result.json'.

    Returns:
        Raw bytes of the file.

    Raises:
        FileNotFoundError: If the blob does not exist.
    """
    client = get_gcs_client()
    bucket = client.bucket(config.GCS_BUCKET_NAME)
    blob = bucket.blob(source_path)

    if not blob.exists():
        raise FileNotFoundError(
            f"GCS object not found: gs://{config.GCS_BUCKET_NAME}/{source_path}"
        )

    return blob.download_as_bytes()


@st.cache_data(ttl=300)
def list_blobs(prefix: str) -> list[str]:
    """
    List object names under a prefix. Cached for 5 minutes.

    Args:
        prefix: GCS prefix to filter by, e.g. 'uploads/'.

    Returns:
        List of blob name strings.
    """
    client = get_gcs_client()
    blobs = client.list_blobs(config.GCS_BUCKET_NAME, prefix=prefix)
    return [blob.name for blob in blobs]
```

---

## Phase 5 — Streamlit Performance Rules

These are **non-negotiable**. Apply to every Streamlit file.

### 5.1 Cache API clients with `@st.cache_resource`

Use for: GCS client, Gemini client, DB connections — anything initialised once and shared.

```python
# WRONG — creates a new client on every rerun
def get_client():
    return storage.Client()

# RIGHT
@st.cache_resource
def get_client():
    return storage.Client()
```

### 5.2 Cache data fetches with `@st.cache_data`

Use for: GCS reads, API responses, data transforms.

```python
# WRONG — hits GCS on every rerun
def load_data(path: str) -> bytes:
    return download_bytes(path)

# RIGHT
@st.cache_data(ttl=600)
def load_data(path: str) -> bytes:
    return download_bytes(path)
```

### 5.3 Initialise session state once, at the top of every page

```python
# state/session.py
import streamlit as st

def init_session_state() -> None:
    """Initialise all session state keys with defaults. Safe to call on every rerun."""
    defaults: dict = {
        "messages": [],
        "uploaded_file_uri": None,
        "current_result": None,
        "is_processing": False,
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default
```

Call `init_session_state()` as the **first line** of `app.py` and every page file.

### 5.4 Gate expensive calls behind user actions

```python
# WRONG — runs on every rerun including every keystroke
result = generate_text(st.text_input("Question"))

# RIGHT — only runs when the user clicks Submit
question = st.text_input("Question")
if st.button("Submit"):
    with st.spinner("Thinking..."):
        result = generate_text(question)
    st.session_state.current_result = result
```

### 5.5 Stream Gemini responses for perceived speed

```python
# In the UI:
with st.chat_message("assistant"):
    st.write_stream(stream_text(user_prompt))
```

### 5.6 Use `st.spinner` for every long operation

```python
with st.spinner("Uploading to GCS..."):
    uri = upload_bytes(data, path)

with st.spinner("Generating response..."):
    result = generate_text(prompt)
```

---

## Phase 6 — `app.py` (Entry Point)

Thin. Page config + session init + layout only. Target: under 60 lines.

```python
# app.py
"""
Streamlit application entry point.
Handles page configuration, session state initialisation, and top-level layout.
"""

import logging
import streamlit as st

from config import config
from state.session import init_session_state
from components.sidebar import render_sidebar

# ── Must be the absolute first Streamlit call ─────────────────────────────────
st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── Session state ─────────────────────────────────────────────────────────────
init_session_state()

# ── Sidebar ───────────────────────────────────────────────────────────────────
render_sidebar()

# ── Main content ──────────────────────────────────────────────────────────────
st.title(config.APP_TITLE)
# For multipage apps, Streamlit routes via pages/ automatically.
# Add single-page content here if not using pages/.
```

**`app.py` must never contain:** Gemini calls, GCS operations, business logic, inline route logic.

---

## Phase 7 — UI Design Standards

### 7.1 Use columns — never raw vertical stacking for forms

```python
col1, col2 = st.columns(2)
with col1:
    name = st.text_input("Name")
    phone = st.text_input("Phone")
with col2:
    email = st.text_input("Email")
    role = st.selectbox("Role", ["User", "Admin"])
```

### 7.2 Use `st.expander` to hide advanced/secondary controls

```python
with st.expander("Advanced Settings", expanded=False):
    temperature = st.slider("Temperature", 0.0, 1.0, config.GEMINI_TEMPERATURE)
    max_tokens = st.number_input("Max tokens", value=config.GEMINI_MAX_TOKENS)
```

### 7.3 Use `st.metric` for KPIs

```python
col1, col2, col3 = st.columns(3)
col1.metric("Files Processed", 42)
col2.metric("Tokens Used", "1.2M")
col3.metric("Avg Response", "1.4s")
```

### 7.4 Consistent status messages — never bare `st.write` for feedback

```python
st.error("Upload failed — please check your file format.")
st.success("File processed successfully.")
st.warning("Approaching token limit.")
st.info("Processing in background...")
```

### 7.5 Single CSS block in `app.py` only

```python
st.markdown("""
<style>
    .main .block-container { padding-top: 2rem; max-width: 900px; }
    .stButton > button { border-radius: 8px; font-weight: 600; }
    .stTextInput > div > div > input { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)
```

One block. No scattered inline styles across files.

---

## Phase 8 — Dockerfile for Cloud Run

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copy requirements first to exploit layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false"]
```

### Cloud Run environment variables (set in Cloud Run console — never hardcode)
```
GCP_PROJECT_ID=your-project-id
GCS_BUCKET_NAME=your-bucket-name
GEMINI_API_KEY=your-api-key
GEMINI_MODEL=gemini-2.5-flash
APP_ENV=production
DEBUG=false
```

---

## Phase 9 — Code-Level Refactor Rules

### Dead code — always grep before removing
```
grep_search: function_name
```
Delete only if zero call sites found (excluding the definition itself).

### Imports — order in every file
```python
# 1. Standard library
import os
import logging
from typing import Optional, List

# 2. Third-party
import streamlit as st
import google.generativeai as genai

# 3. Local
from config import config
from services.gcs import download_bytes
```

### Error handling — always specific, never silent
```python
# BAD
try:
    result = call_api()
except:
    pass

# GOOD
try:
    result = call_api()
except ValueError as e:
    logger.warning("Bad input: %s", e)
    st.error(f"Invalid input: {e}")
except Exception as e:
    logger.error("Unexpected error: %s", e, exc_info=True)
    st.error("Something went wrong. Please try again.")
```

### Guard clauses over nested if-else
```python
# BAD
def handle_upload(file):
    if file:
        if file.size > 0:
            if file.type in ALLOWED_TYPES:
                process(file)

# GOOD
def handle_upload(file):
    if not file:
        return
    if file.size == 0:
        st.warning("File is empty.")
        return
    if file.type not in ALLOWED_TYPES:
        st.error(f"Unsupported type: {file.type}")
        return
    process(file)
```

### Type hints on every function
```python
def process_upload(file_bytes: bytes, filename: str) -> str:
    ...
```

### Docstrings on every public function
```python
def upload_to_gcs(data: bytes, path: str) -> str:
    """
    Upload bytes to GCS.

    Args:
        data: File bytes to upload.
        path: Destination path within the bucket.

    Returns:
        Full GCS URI (gs://bucket/path).
    """
```

### Replace all print() with logging
```python
import logging
logger = logging.getLogger(__name__)
logger.info("Uploaded file: %s", filename)
logger.error("GCS error: %s", e, exc_info=True)
```

---

## Phase 10 — Final Checklist

### Structure
- [ ] `app.py` is thin — page config, session init, layout only
- [ ] `config.py` exists — every hardcoded value moved there
- [ ] All Gemini calls are in `services/gemini.py` only
- [ ] All GCS calls are in `services/gcs.py` only
- [ ] Session state initialised in one place via `init_session_state()`
- [ ] Every package directory has `__init__.py`

### Performance
- [ ] GCS client wrapped in `@st.cache_resource`
- [ ] Gemini client wrapped in `@st.cache_resource`
- [ ] All data fetches use `@st.cache_data` with a TTL
- [ ] Expensive operations only triggered by user actions (buttons), not top-level
- [ ] `st.spinner` used for every long-running operation
- [ ] Gemini responses streamed with `st.write_stream` where UX allows

### Code quality
- [ ] No dead/unused functions
- [ ] No duplicate logic
- [ ] No hardcoded bucket names, model names, project IDs, API keys
- [ ] No `print()` — replaced with `logger.*`
- [ ] No bare `except:` — specific exception types used
- [ ] Imports clean, sorted, unused ones removed
- [ ] Type hints on all public functions
- [ ] Docstrings on all public functions

### Deployment
- [ ] No secrets in any `.py` file
- [ ] `.env.example` created with all required keys
- [ ] `.gitignore` excludes `.env` and `*.json` key files
- [ ] `Dockerfile` uses `$PORT` for Cloud Run compatibility

### Sanity check
```
run_terminal_cmd: python -c "import app; print('Import OK')"
run_terminal_cmd: streamlit run app.py --server.headless true
```

---

## What NOT to Do

- **Never create a GCS client outside `services/gcs.py`**
- **Never call `genai` outside `services/gemini.py`**
- **Never use `st.experimental_rerun()`** — use `st.rerun()` (current API)
- **Never put `st.set_page_config()` anywhere except the very first call in `app.py`**
- **Never put `@st.cache_resource` on a function that takes mutable/unhashable args** — use `@st.cache_data` or convert args to strings/tuples
- **Never over-engineer** — if the app is under 400 lines, flat structure is fine
- **Never delete uncertain code** — comment it out with `# REMOVED (verify):` and flag it to the user

---

## Communicating Results to the User

When done, provide:

1. **What you found** — plain English: what the app does, what was wrong with the original code
2. **What changed** — old structure vs new structure, what moved where
3. **What is now faster** — specifically which cache decorators were added and why
4. **What they need to do** — copy `.env.example` → `.env`, fill in values, `pip install -r requirements.txt`, set Cloud Run env vars
5. **Flags** — anything commented out, anything dynamically called that was left in, anything that still needs attention