from __future__ import annotations

from functools import lru_cache

from google.cloud import storage


@lru_cache(maxsize=1)
def get_gcs_client() -> storage.Client:
    """Return a cached GCS client for this process."""
    return storage.Client()

