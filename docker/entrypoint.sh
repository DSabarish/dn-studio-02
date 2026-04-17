#!/bin/sh
set -e
# Optional first argument: path to a GCP service account JSON file inside the container.
# Prefer bind-mounting the key and passing that path, e.g.:
#   docker run ... -v /host/key.json:/run/secrets/sa.json:ro IMAGE /run/secrets/sa.json
# Or set GOOGLE_APPLICATION_CREDENTIALS in the environment and omit the argument.
if [ -n "${GOOGLE_APPLICATION_CREDENTIALS}" ]; then
  :
elif [ -n "$1" ] && [ -f "$1" ]; then
  export GOOGLE_APPLICATION_CREDENTIALS="$1"
  shift
fi

exec streamlit run app.py \
  --server.port="${PORT:-8501}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  "$@"
