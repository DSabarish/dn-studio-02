# DN Studio — Streamlit app + faster-whisper + Vertex (Gemini) + Node (docx template)
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8501

# ffmpeg: audio extract for Whisper; nodejs/npm: templates/bpd_template.js (docx)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY requirements.txt pyproject.toml uv.lock* ./
RUN uv pip install --system --no-cache -r requirements.txt

COPY . .

RUN cd templates && npm install && npm cache clean --force

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/entrypoint.sh"]
CMD []
