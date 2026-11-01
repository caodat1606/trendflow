"""SQLite access and NON-destructive migrations for TrendFlow 3.0.

Rules:
- Never DROP, DELETE or rewrite user rows during migration. Only CREATE IF NOT EXISTS
  and ADD COLUMN.
- Before the first 3.x migration of an existing database a consistent copy is made
  with the SQLite backup API into ./backups/.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('TRENDFLOW_DB') or (Path(os.getenv('TRENDFLOW_DATA_DIR') or ROOT) / 'trendflow.db'))
SCHEMA_VERSION = 4
_SETTINGS_LOCK = threading.Lock()

DEFAULT_SETTINGS = {
    # strict: CC0/PD/CC BY · open: + CC BY-SA · editorial: + ảnh của chính bài báo gốc
    # article: ảnh của chính bài gốc + bài báo khác cùng sự kiện (mặc định) · search: ảnh tư liệu Wikimedia/Openverse
    # manual: the editor picks photos per segment in Visual Studio (default from 3.6) · auto: AI picks (image_source)
    'visual_mode': 'manual',
    'pexels_key': '',                # optional: Pexels API key for Visual Studio photo search
    'image_source': 'article',
    'rights_policy': 'open',
    'voice_id': 'vi-VN-NamMinhNeural',
    'azure_key': '',                 # Azure Speech (same Nam Minh voice, official API); stored only in local DB
    'azure_region': 'southeastasia',
    'allow_fallback_voice': False,
    'image_verifier': 'auto',        # auto: Gemini if keyed, else Ollama vision model (qwen2.5vl…)
    'serpapi_key': '',               # optional: Google Images results via serpapi.com (else Bing Images)
    'gemini_key': '',                # Google AI Studio API key (Gemini TTS); stored only in local DB
    'gemini_voice': 'Charon',
    'gemini_model': '',              # empty = try current TTS models automatically   # never silently ship gTTS/robot voices
    'tts_provider': 'auto',
    'final_quality': '1080p',
    'preview_quality': '720p',
    'target_seconds': 52,
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path | None = None):
    db = sqlite3.connect(str(path or DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('PRAGMA journal_mode=WAL')
    return db


def add_columns(db, table, columns):
    existing = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
    for name, definition in columns.items():
        if name not in existing:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')


def backup_database(reason: str, path: Path | None = None) -> Path | None:
    src_path = Path(path or DB_PATH)
    if not src_path.is_file():
        return None
    folder = src_path.parent / 'backups'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    target = folder / f'trendflow_{reason}_{stamp}.db'
    with sqlite3.connect(str(src_path)) as src, sqlite3.connect(str(target)) as dst:
        src.backup(dst)
    with sqlite3.connect(str(target)) as check:
        if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Bản sao lưu database bị lỗi; dừng nâng cấp để bảo vệ dữ liệu.')
    return target


LEGACY_SCHEMA = '''
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS trends (
    id INTEGER PRIMARY KEY, source_id INTEGER, category TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT, link TEXT NOT NULL UNIQUE,
    published TEXT, first_seen TEXT NOT NULL, score INTEGER DEFAULT 0,
    status TEXT DEFAULT 'Mới', script TEXT);
CREATE TABLE IF NOT EXISTS google_trends (
    keyword TEXT PRIMARY KEY, norm_keyword TEXT NOT NULL, traffic TEXT, published TEXT,
    fetched_at TEXT NOT NULL, category TEXT DEFAULT 'Khác');
CREATE TABLE IF NOT EXISTS international_trends (
    geo TEXT NOT NULL, keyword TEXT NOT NULL, norm_keyword TEXT NOT NULL, traffic TEXT,
    published TEXT, fetched_at TEXT NOT NULL, category TEXT DEFAULT 'Khác',
    PRIMARY KEY (geo, keyword));
CREATE TABLE IF NOT EXISTS scan_history (
    id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, inserted INTEGER DEFAULT 0,
    source_ok INTEGER DEFAULT 0, source_error INTEGER DEFAULT 0, google_ok INTEGER DEFAULT 0,
    errors_json TEXT);
CREATE TABLE IF NOT EXISTS script_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trend_id INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '', hook TEXT NOT NULL DEFAULT '', narration TEXT NOT NULL DEFAULT '',
    scenes_json TEXT NOT NULL DEFAULT '[]', caption TEXT NOT NULL DEFAULT '', hashtags TEXT NOT NULL DEFAULT '',
    verification TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Bản nháp',
    duration_target INTEGER NOT NULL DEFAULT 30, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS video_assets (
    trend_id INTEGER PRIMARY KEY, filename TEXT NOT NULL, script_hash TEXT NOT NULL,
    voice_filename TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    duration_seconds REAL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS article_context (
    trend_id INTEGER PRIMARY KEY, fetched_at TEXT, article_url TEXT, title TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS automation_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trend_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', step TEXT NOT NULL DEFAULT 'Đang chờ',
    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
    origin TEXT NOT NULL DEFAULT 'manual', local_day TEXT);
CREATE TABLE IF NOT EXISTS workflow_items (
    trend_id INTEGER PRIMARY KEY, stage TEXT NOT NULL DEFAULT 'draft',
    position INTEGER NOT NULL DEFAULT 0, approved_hash TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_trends_category ON trends(category);
CREATE INDEX IF NOT EXISTS idx_trends_source ON trends(source_id);
CREATE INDEX IF NOT EXISTS idx_google_norm ON google_trends(norm_keyword);
CREATE INDEX IF NOT EXISTS idx_workflow_stage_position ON workflow_items(stage,position);
'''

V3_SCHEMA = '''
CREATE TABLE IF NOT EXISTS tf_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voice_tracks (
    trend_id INTEGER PRIMARY KEY, filename TEXT NOT NULL, narration_hash TEXT NOT NULL,
    provider TEXT NOT NULL, voice_id TEXT NOT NULL, duration REAL NOT NULL,
    timings_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scene_plans (
    trend_id INTEGER PRIMARY KEY, plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL,
    narration_hash TEXT NOT NULL, voice_filename TEXT NOT NULL, passed INTEGER NOT NULL,
    report_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS media_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE, page_url TEXT,
    provider TEXT, title TEXT, description TEXT, creator TEXT, license TEXT, license_url TEXT,
    rights_class TEXT, width INTEGER, height INTEGER, taken TEXT, local_filename TEXT,
    dhash TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS renders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trend_id INTEGER NOT NULL, kind TEXT NOT NULL,
    filename TEXT NOT NULL, plan_hash TEXT NOT NULL, duration REAL, width INTEGER, height INTEGER,
    passed INTEGER NOT NULL DEFAULT 0, qa_json TEXT, sheet_filename TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_renders_trend ON renders(trend_id, kind, id);
CREATE TABLE IF NOT EXISTS visual_selections (
    trend_id INTEGER PRIMARY KEY, narration_hash TEXT NOT NULL, segments_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS script_checks (
    trend_id INTEGER PRIMARY KEY, narration_hash TEXT NOT NULL, report_json TEXT NOT NULL,
    created_at TEXT NOT NULL);
'''


def schema_version(db) -> int:
    try:
        row = db.execute("SELECT value FROM tf_meta WHERE key='schema_version'").fetchone()
        return int(row['value']) if row else 0
    except sqlite3.OperationalError:
        return 0


def migrate(path: Path | None = None) -> dict:
    """Create/upgrade schema. Returns {'backup': path or None, 'from': v, 'to': v}."""
    path = Path(path or DB_PATH)
    existed = path.is_file() and path.stat().st_size > 0
    backup = None
    with connect(path) as db:
        version = schema_version(db)
        has_data = existed and db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
    if has_data and version < SCHEMA_VERSION:
        backup = backup_database(f'pre_v{SCHEMA_VERSION}', path)
    with connect(path) as db:
        db.executescript(LEGACY_SCHEMA)
        db.executescript(V3_SCHEMA)
        add_columns(db, 'sources', {'language': "TEXT DEFAULT 'en'", 'market': "TEXT DEFAULT 'INTL'"})
        add_columns(db, 'trends', {'title_vi': 'TEXT', 'summary_vi': 'TEXT', 'vn_score': 'INTEGER',
                                   'score_reason': 'TEXT'})
        add_columns(db, 'automation_jobs', {'local_day': 'TEXT', 'kind': "TEXT DEFAULT 'legacy'",
                                            'progress': 'REAL DEFAULT 0', 'result_json': 'TEXT'})
        add_columns(db, 'workflow_items', {'approved_at': 'TEXT'})
        # Interrupted work from a previous run cannot continue (threads are gone).
        db.execute("""UPDATE automation_jobs SET status='cancelled', step='Dừng do khởi động lại máy chủ',
                      finished_at=? WHERE status IN ('queued','running','cancel_requested')""", (now_iso(),))
        db.execute("INSERT OR REPLACE INTO tf_meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    return {'backup': str(backup) if backup else None, 'from': version, 'to': SCHEMA_VERSION}


def get_settings() -> dict:
    with connect() as db:
        rows = {r['key']: r['value'] for r in db.execute('SELECT key,value FROM app_settings')}
    out = dict(DEFAULT_SETTINGS)
    for k, v in rows.items():
        if k in out:
            try:
                out[k] = json.loads(v)
            except ValueError:
                out[k] = v
    return out


def set_settings(values: dict) -> dict:
    with _SETTINGS_LOCK, connect() as db:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                db.execute('INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)',
                           (k, json.dumps(v, ensure_ascii=False)))
    return get_settings()
