"""Centralized configuration for DN Studio."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration loaded from environment variables."""

    APP_TITLE: str = os.getenv("APP_TITLE", "DN Studio")
    RUNS_BUCKET: str = os.getenv("RUNS_BUCKET", "dn-studio-runs-01")
    RUN_DIR_NAME: str = os.getenv("RUN_DIR_NAME", "run")

    GCP_PROJECT_ID: str = os.getenv("PROJECT_ID", "dn-studio-01")
    GCP_LOCATION: str = os.getenv("LOCATION", "us-central1")

    GEMINI_PROJECT_ID: str = os.getenv("GEMINI_PROJECT_ID", "dn-studio-01")
    GEMINI_LOCATION: str = os.getenv("GEMINI_LOCATION", "asia-south1")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    ASSEMBLYAI_PARALLELISM: int = int(os.getenv("ASSEMBLYAI_PARALLELISM", "2"))
    LIVE_LOG_MAX_LINES: int = int(os.getenv("LIVE_LOG_MAX_LINES", "300"))

    @property
    def app_root(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def run_dir(self) -> Path:
        return self.app_root / self.RUN_DIR_NAME


config = Config()

