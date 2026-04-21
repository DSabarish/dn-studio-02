# Base image: Debian Bookworm + Python 3.12 (slim = smaller, no extra dev tools).
FROM python:3.12-slim-bookworm

# PYTHONUNBUFFERED: print logs immediately (no stdout buffering) — better for `docker logs`.
# PIP_NO_CACHE_DIR: do not keep pip download cache in the image layer (smaller image).
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Refresh apt index, install OS packages, then delete apt lists to shrink the layer.
# - ffmpeg: extract audio from video for Faster Whisper transcription.
# - curl: optional HTTP tooling / some deps.
# - ca-certificates: HTTPS (e.g. pip, npm, APIs).
# - nodejs + npm: run templates/bpd_template.js for JSON → DOCX in the app.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Copy the `uv` binary from Astral’s official image so we can install Python deps quickly.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# All following commands run from /app (app root inside the container).
WORKDIR /app

# Copy only dependency manifests first (better layer cache: code changes won’t reinstall Python deps).
COPY requirements.txt pyproject.toml uv.lock* ./
# Install Python packages system-wide into this image (Streamlit, google-genai, faster-whisper, etc.).
RUN uv pip install --system --no-cache -r requirements.txt

# Copy the rest of the repo (respects .dockerignore).
COPY . .
# Install Node dependency `docx` for the BPD DOCX template; clear npm cache to save space.
RUN cd templates && npm install && npm cache clean --force

# Document that the process listens on 8501 (informational for `docker run -p`).
EXPOSE 8501

# Default process: start Streamlit UI (app2.py — full pipeline).
# - --server.port=8501: listen port (match EXPOSE / `docker run -p`).
# - --server.address=0.0.0.0: accept connections from outside the container (not only localhost).
# - --server.headless=true: no browser auto-open; correct for containers.
CMD ["streamlit", "run", "app2.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
