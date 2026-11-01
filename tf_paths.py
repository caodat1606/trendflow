"""Where TrendFlow keeps code vs. personal data.

Code (this Git repository) can be updated freely. Personal data — database, .env with keys, media, voice, video,
backups, logs — lives in DATA_DIR, OUTSIDE the repository, so `git pull`/`trendflow update` never touches it.

DATA_DIR = $TRENDFLOW_DATA_DIR if set (the `trendflow` command always sets it), otherwise the code folder
(backward compatible with ZIP installs and the test-suite).
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv('TRENDFLOW_DATA_DIR') or ROOT)
DB_PATH = Path(os.getenv('TRENDFLOW_DB') or DATA_DIR / 'trendflow.db')
ENV_FILE = DATA_DIR / '.env'
MEDIA_DIR = DATA_DIR / 'media_library'
VOICE_DIR = DATA_DIR / 'voice_outputs'
VIDEO_DIR = DATA_DIR / 'video_outputs'


def version() -> str:
    try:
        return (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    except OSError:
        return '0.0.0'
