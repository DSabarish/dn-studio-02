from __future__ import annotations

import logging

import streamlit as st
from dotenv import load_dotenv

from config import config
from ui.doc_view import render_doc_view
from ui.gap_view import render_gap_view

RUN_DIR = config.run_dir
RUNS_BUCKET = config.RUNS_BUCKET
DEFAULT_BPD_H1_HEADERS = "\n".join(
    [
        "Business Process Overview",
        "Business Process Design",
        "Business Process Flows",
        "Business Process Controls",
        "Business Process Impacts",
    ]
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("dn_studio.app")
load_dotenv()

st.set_page_config(page_title=config.APP_TITLE, layout="wide")
st.title(config.APP_TITLE)
st.markdown(
    """
    <style>
      .main .block-container { max-width: 1100px; padding-top: 1.2rem; padding-bottom: 2.0rem; }
      div[data-testid="stExpander"] { border-radius: 10px; }
      div.stButton > button { border-radius: 10px; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.caption(
    "Add meeting media or transcript JSON, optional context documents, and BPD settings. "
    "One button runs: transcripts → context.md → schema prompt → r1_schema.json → populate prompt → r2_populated.json → DOCX (if Node is available)."
)

view_mode = st.radio(
    "View",
    options=["Doc", "GAP Analysis"],
    horizontal=True,
    key="app_view_mode",
)

if view_mode == "GAP Analysis":
    render_gap_view(run_dir=RUN_DIR, runs_bucket=RUNS_BUCKET)
else:
    render_doc_view(
        run_dir=RUN_DIR,
        runs_bucket=RUNS_BUCKET,
        default_bpd_h1_headers=DEFAULT_BPD_H1_HEADERS,
    )
