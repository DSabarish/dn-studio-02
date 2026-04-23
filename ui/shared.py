from __future__ import annotations

import streamlit as st

from backend.artifacts import list_run_folders_in_gcs


def meeting_date_key(idx: int) -> str:
    return f"app_meeting_date_{idx}"


@st.cache_data(ttl=60)
def cached_list_run_folders_in_gcs(bucket_name: str) -> list[str]:
    return list_run_folders_in_gcs(bucket_name)

