"""TrendFlow 3.0 — local news-video studio (FastAPI). Run: python3 -m uvicorn app:app --port 8001"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

if Path('/opt/homebrew/bin/ffmpeg').exists():  # Apple Silicon Homebrew, often missing from venv PATH
    os.environ['PATH'] = '/opt/homebrew/bin:' + os.environ.get('PATH', '')

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
import tf_paths

load_dotenv(tf_paths.ENV_FILE)          # personal keys live in the data folder, never in the repository
load_dotenv(ROOT / '.env')              # legacy location (ZIP installs)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

import tf_db
import tf_jobs
import tf_llm
import tf_media
import tf_news
import tf_studio
import tf_tts
from tf_db import connect, get_settings, now_iso, set_settings
from tf_text import narration_hash, scene_segments, word_count

VERSION = tf_paths.version()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
MIGRATION = tf_db.migrate()
if MIGRATION['backup']:
    logging.info('Đã sao lưu database trước khi nâng cấp: %s', MIGRATION['backup'])
tf_news.seed_sources()

from contextlib import asynccontextmanager


def _quiet_disconnects(loop, context):
    # Windows: a browser that stops/seeks a video drops the socket -> harmless WinError 10054 tracebacks.
    if isinstance(context.get('exception'), (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(_app):
    import asyncio
    asyncio.get_running_loop().set_exception_handler(_quiet_disconnects)
    if os.getenv('AUTO_SCAN', 'true').lower() in ('true', '1', 'yes'):
        threading.Thread(target=tf_news.scanner_loop, daemon=True, name='TrendFlowSchedule').start()
    tf_jobs.ensure_worker()
    yield


app = FastAPI(title='TrendFlow', version=VERSION, lifespan=lifespan)
app.include_router(tf_news.router)


# ------------------------------------------------------------------ pages

PAGES = ('/', '/discover', '/scripts', '/pending', '/completed', '/settings')


def _index():
    return HTMLResponse((ROOT / 'index.html').read_text(encoding='utf-8'), headers={'Cache-Control': 'no-store'})


for _p in PAGES:
    app.add_api_route(_p, _index, methods=['GET'], response_class=HTMLResponse, include_in_schema=False)


@app.get('/scripts/{tid}', response_class=HTMLResponse, include_in_schema=False)
def _script_page(tid: int):
    return _index()


# ------------------------------------------------------------------ helpers

def _require_project(tid):
    p = tf_jobs.project_row(tid)
    if not p:
        raise HTTPException(404, 'Tin này chưa có kịch bản.')
    return p


def _require_draft(tid):
    st = tf_jobs.stage_of(tid)
    if st not in (None, 'draft'):
        raise HTTPException(409, 'Kịch bản đang ở Pending/Thành phẩm. Chuyển về bản nháp để chỉnh sửa.')


def _enqueue(tid, kind, params=None):
    try:
        jid = tf_jobs.enqueue(tid, kind, params=params)
    except tf_jobs.JobFailed as exc:
        raise HTTPException(409, str(exc))
    return {'ok': True, 'job_id': jid}


def gates(tid) -> dict:
    """Everything required before approval. Each item: ok + message."""
    p = tf_jobs.project_row(tid)
    vt = tf_jobs.voice_track(tid)
    sp = tf_jobs.scene_plan(tid)
    pr = tf_jobs.latest_render(tid, 'preview')
    chk = tf_jobs.script_check(tid)
    nh = narration_hash(p['narration']) if p else None
    items = []

    def add(key, ok, msg):
        items.append({'key': key, 'ok': bool(ok), 'message': msg})

    fresh_voice, why_voice = tf_jobs.voice_is_current(vt, p, get_settings())
    add('voice', fresh_voice, f"Giọng đọc {vt['duration']:.1f}s · {tf_tts.PROVIDER_LABELS.get(vt['provider'], vt['provider'])}"
        if fresh_voice else why_voice)
    add('duration', fresh_voice and 45 <= vt['duration'] <= 60,
        f"Thời lượng {vt['duration']:.1f}s (cần 45–60s)" if fresh_voice else 'Chưa đo thời lượng')
    fresh_check = bool(chk and chk['narration_hash'] == nh)
    add('facts', fresh_check and chk['ok'],
        ('Số liệu khớp bài nguồn' if chk['ok'] else 'Số liệu không có trong nguồn: ' + ', '.join(chk['unsupported_numbers']))
        if fresh_check else 'Chưa kiểm tra dữ kiện')
    settings = get_settings()
    fresh_plan = bool(fresh_voice and tf_jobs.plan_is_current(sp, vt, p, settings))
    manual = tf_jobs.plan_source(settings) == 'manual'
    if not fresh_plan:
        photo_msg = ('Ảnh đã chọn thay đổi hoặc chưa tạo video xem thử từ ảnh đã chọn' if manual
                     else 'Chưa chọn ảnh cho lời thoại hiện tại')
    elif not sp['passed']:
        photo_msg = 'Ảnh chưa đạt: ' + ' '.join(sp['report']['issues'])
    elif manual:
        photo_msg = (f"{sp['report']['distinct_photos']} ảnh tự chọn · "
                     f"{sp['report']['sentence_matched']}/{sp['report']['segments']} đoạn có ảnh")
    elif sp['plan'].get('image_source') == 'article':
        photo_msg = (f"{sp['report']['distinct_photos']} ảnh đã được AI xác nhận (bài gốc: {sp['report'].get('verified_a', 0)}, web: "
                     f"{sp['report'].get('verified_b', 0)}) · {sp['report']['sentence_matched']}/{sp['report']['segments']} câu có ảnh đúng chủ thể")
    else:
        photo_msg = (f"{sp['report']['distinct_photos']} ảnh thật, "
                     f"{sp['report']['sentence_matched']}/{sp['report']['segments']} câu có ảnh riêng")
    add('photos', fresh_plan and sp['passed'], photo_msg)
    fresh_render = bool(pr and fresh_plan and pr['plan_hash'] == sp['plan_hash'])
    add('preview', fresh_render and pr['passed'], ('Video xem thử đã qua QA' if pr['passed'] else 'QA video xem thử chưa đạt')
        if fresh_render else 'Chưa có video xem thử cho nội dung hiện tại')
    return {'ok': all(i['ok'] for i in items), 'items': items,
            'warnings': (['Tên riêng chưa thấy trong nguồn: ' + ', '.join(chk['unsupported_names'])]
                         if fresh_check and chk.get('unsupported_names') else [])
                        + (sp['report'].get('warnings', []) if fresh_plan else [])}


def project_payload(tid):
    p = tf_jobs.project_row(tid)
    trend = tf_jobs.trend_row(tid)
    with connect() as db:
        w = db.execute('SELECT * FROM workflow_items WHERE trend_id=?', (tid,)).fetchone()
    out = {'trend': {k: trend.get(k) for k in ('id', 'title', 'title_vi', 'summary_vi', 'summary', 'link', 'category',
                                              'published', 'source_name', 'market')},
           'project': None, 'stage': w['stage'] if w else None, 'job': tf_jobs.active_job(tid)}
    if not p:
        return out
    vt = tf_jobs.voice_track(tid)
    sp = tf_jobs.scene_plan(tid)
    pr = tf_jobs.latest_render(tid, 'preview')
    fr = tf_jobs.latest_render(tid, 'final')
    nh = narration_hash(p['narration'])
    segs = scene_segments(p['narration'])
    sentences = [{'index': i, 'text': s} for i, s in enumerate(segs)]
    if sp and sp['narration_hash'] == nh and sp['plan'].get('image_source') != 'manual':
        for seg in sp['plan']['segments']:
            if seg['index'] < len(sentences):
                sentences[seg['index']].update({'start': seg['start'], 'end': seg['end'], 'entities': seg['entities'],
                                                'shots': [dict(sh, thumb=f"/api/media/{sh['media_id']}/image",
                                                               credit=sp['plan']['media'][str(sh['media_id'])].get('credit'),
                                                               page_url=sp['plan']['media'][str(sh['media_id'])].get('page_url'))
                                                          for sh in seg['shots']]})
    out['project'] = {'title': p['title'], 'headline': p['hook'], 'narration': p['narration'], 'caption': p['caption'],
                      'hashtags': p['hashtags'], 'verification': p['verification'], 'updated_at': p['updated_at'],
                      'words': word_count(p['narration']), 'sentences': sentences}
    settings = get_settings()
    voice_ok, voice_why = tf_jobs.voice_is_current(vt, p, settings)
    # Old audio/video is never presented as the current result: stale items lose their URL and say why.
    out['voice'] = {'duration': vt['duration'], 'provider': vt['provider'],
                    'provider_label': tf_tts.PROVIDER_LABELS.get(vt['provider']), 'fresh': voice_ok, 'stale_reason': voice_why,
                    'fallback_reason': vt['timings'].get('fallback_reason'),
                    'url': f'/api/projects/{tid}/audio' if voice_ok else None} if vt else None
    out['plan'] = {'report': sp['report'], 'passed': bool(sp['passed']), 'fresh': sp['narration_hash'] == nh} if sp else None
    plan_current = bool(voice_ok and tf_jobs.plan_is_current(sp, vt, p, settings))
    out['preview_stale'] = None
    if pr and (out['stage'] in (None, 'draft')) and not (plan_current and pr['plan_hash'] == sp['plan_hash']):
        out['preview_stale'] = {'reason': voice_why or 'Nội dung/ảnh đã đổi sau video xem thử này', 'created_at': pr['created_at']}
        pr = None
    for key, r in (('preview', pr), ('final', fr)):
        out[key] = {'url': f'/api/projects/{tid}/video/{key}', 'download': f'/api/projects/{tid}/video/{key}?download=1',
                    'sheet': f'/api/projects/{tid}/sheet/{key}', 'duration': r['duration'], 'passed': bool(r['passed']),
                    'qa': {k: r['qa'].get(k) for k in ('checks', 'segments', 'passed')}, 'created_at': r['created_at'],
                    'fresh': bool(sp and r['plan_hash'] == sp['plan_hash'])} if r else None
    out['gates'] = gates(tid)
    import tf_verify
    out['verifiers'] = tf_verify.available(settings)
    out['verifier_default'] = settings.get('image_verifier', 'auto')
    out['legacy_video'] = _legacy_video(tid)
    out['visual_mode'] = tf_jobs.plan_source(settings) == 'manual' and 'manual' or 'auto'
    out['studio'] = None
    if out['visual_mode'] == 'manual':
        sel, tl, approx = tf_jobs.studio_state(tid, p, settings)
        out['studio'] = tf_studio.payload(sel, tl, approx)
        out['studio']['sources'] = tf_studio.sources(settings)
        out['studio']['voice_ready'] = not approx
    return out


def _legacy_video(tid):
    with connect() as db:
        r = db.execute('SELECT filename, duration_seconds FROM video_assets WHERE trend_id=?', (tid,)).fetchone()
    if r and re.fullmatch(r'[0-9]+_[0-9a-f]{14}\.mp4', r['filename']) and (tf_jobs.VIDEO_DIR / r['filename']).is_file():
        return {'url': f'/api/projects/{tid}/video/legacy', 'duration': r['duration_seconds']}
    return None


# ------------------------------------------------------------------ projects

class GenerateBody(BaseModel):
    target_seconds: int | None = None
    angle: str = ''


class SaveBody(BaseModel):
    title: str
    headline: str = ''
    narration: str
    caption: str = ''
    hashtags: str = ''
    verification: str = ''


@app.get('/api/projects')
def list_projects(stage: str = 'draft'):
    if stage not in ('draft', 'pending', 'done'):
        raise HTTPException(400, 'Giai đoạn không hợp lệ')
    with connect() as db:
        rows = [dict(r) for r in db.execute(
            '''SELECT w.trend_id, w.stage, w.position, w.updated_at, w.approved_hash, p.title, p.narration, t.category, t.link
               FROM workflow_items w JOIN script_projects p ON p.trend_id=w.trend_id JOIN trends t ON t.id=w.trend_id
               WHERE w.stage=? ORDER BY CASE WHEN ?='pending' THEN w.position END ASC, w.updated_at DESC LIMIT 300''',
            (stage, stage))]
    for r in rows:
        tid = r['trend_id']
        r['words'] = word_count(r.pop('narration'))
        r['job'] = tf_jobs.active_job(tid)
        if stage == 'pending':
            r['approved_valid'] = bool(r['approved_hash']) and r['approved_hash'] == tf_jobs.approval_hash(tid)
        if stage == 'done':
            fr = tf_jobs.latest_render(tid, 'final')
            r['final'] = {'duration': fr['duration'], 'width': fr['width'], 'height': fr['height'],
                          'download': f'/api/projects/{tid}/video/final?download=1', 'credits': f'/api/projects/{tid}/credits',
                          'sheet': f'/api/projects/{tid}/sheet/final'} if fr else None
            r['legacy_video'] = _legacy_video(tid)
        r.pop('approved_hash', None)
    return {'items': rows}


@app.get('/api/projects/{tid}')
def get_project(tid: int):
    try:
        return project_payload(tid)
    except tf_jobs.JobFailed as exc:
        raise HTTPException(404, str(exc))


@app.post('/api/projects/{tid}/generate')
def generate(tid: int, body: GenerateBody):
    tf_jobs.trend_row(tid)
    _require_draft(tid)
    if body.target_seconds is not None and not 45 <= body.target_seconds <= 60:
        raise HTTPException(400, 'Mục tiêu phải trong 45–60 giây.')
    return _enqueue(tid, 'generate', {'target_seconds': body.target_seconds, 'angle': body.angle[:300]})


@app.put('/api/projects/{tid}')
def save(tid: int, body: SaveBody):
    _require_project(tid)
    _require_draft(tid)
    if not body.title.strip() or word_count(body.narration) < 20:
        raise HTTPException(400, 'Cần tiêu đề và lời thoại.')
    if len(body.narration) > 4000:
        raise HTTPException(400, 'Lời thoại quá dài (tối đa 4.000 ký tự).')
    job = tf_jobs.active_job(tid)
    if job and job['status'] in ('queued', 'running', 'cancel_requested'):
        raise HTTPException(409, 'Đang có tác vụ chạy cho kịch bản này. Đợi xong hoặc huỷ trước khi lưu.')
    data = {'title': body.title.strip()[:250], 'headline': body.headline.strip()[:120], 'narration': body.narration.strip(),
            'caption': body.caption[:600], 'hashtags': body.hashtags[:300], 'verification': body.verification[:1500]}
    tf_jobs.save_project(tid, data, get_settings()['target_seconds'])
    with connect() as db:
        db.execute('UPDATE workflow_items SET updated_at=? WHERE trend_id=?', (now_iso(), tid))
    return project_payload(tid)


@app.post('/api/projects/{tid}/preview')
def preview(tid: int, refresh_media: bool = False, verifier: str = ''):
    if verifier and verifier not in ('auto', 'gemini', 'ollama'):
        raise HTTPException(400, 'AI kiểm tra ảnh không hợp lệ')
    _require_project(tid)
    if tf_jobs.stage_of(tid) != 'draft':
        raise HTTPException(409, 'Chỉ tạo video xem thử cho bản nháp.')
    return _enqueue(tid, 'preview', {'refresh_media': refresh_media, 'verifier': verifier})


@app.post('/api/projects/{tid}/voice-sample')
def voice_sample(tid: int):
    p = _require_project(tid)
    first = (scene_segments(p['narration']) or [''])[0]
    s = get_settings()
    dest = tf_jobs.VOICE_DIR / f'sample_{tid}.mp3'
    try:
        r = tf_tts.sample(first, dest, s['voice_id'], s['tts_provider'], azure=tf_tts.azure_config(s),
                          allow_fallback=bool(s.get('allow_fallback_voice')), gemini=tf_tts.gemini_config(s))
    except tf_tts.TTSFailure as exc:
        raise HTTPException(502, str(exc))
    return {'url': f'/api/projects/{tid}/audio?sample=1&v={uuid.uuid4().hex[:6]}', 'provider': r['provider_label'],
            'fallback_reason': r.get('fallback_reason')}


@app.post('/api/projects/{tid}/approve')
def approve(tid: int):
    _require_project(tid)
    if tf_jobs.stage_of(tid) != 'draft':
        raise HTTPException(409, 'Chỉ duyệt được bản nháp.')
    g = gates(tid)
    if not g['ok']:
        raise HTTPException(409, 'Chưa đủ điều kiện duyệt: ' + '; '.join(i['message'] for i in g['items'] if not i['ok']))
    h = tf_jobs.approval_hash(tid)
    with connect() as db:
        n = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM workflow_items WHERE stage='pending'").fetchone()[0]
        db.execute("UPDATE workflow_items SET stage='pending', approved_hash=?, approved_at=?, position=?, updated_at=? WHERE trend_id=?",
                   (h, now_iso(), n, now_iso(), tid))
    return {'ok': True}


@app.post('/api/projects/{tid}/to-draft')
def to_draft(tid: int):
    _require_project(tid)
    tf_jobs.cancel_trend(tid)
    with connect() as db:
        db.execute("UPDATE workflow_items SET stage='draft', approved_hash=NULL, position=0, updated_at=? WHERE trend_id=?",
                   (now_iso(), tid))
    return {'ok': True}


@app.delete('/api/projects/{tid}')
def delete_project(tid: int):
    _require_project(tid)
    job = tf_jobs.active_job(tid)
    if job and job['status'] in ('queued', 'running', 'cancel_requested'):
        tf_jobs.cancel_trend(tid)
        if job['status'] != 'queued':
            raise HTTPException(409, 'Đang dừng tác vụ của dự án này. Thử xoá lại sau vài giây.')
    tf_jobs.delete_project_files(tid)
    with connect() as db:
        for table in ('workflow_items', 'script_projects', 'voice_tracks', 'scene_plans', 'renders', 'script_checks'):
            db.execute(f'DELETE FROM {table} WHERE trend_id=?', (tid,))
    tf_studio.delete(tid)
    return {'ok': True, 'source_article_preserved': True}


# ------------------------------------------------------------------ files

def _file(path: Path, media_type, download_name=None):
    if not path.is_file():
        raise HTTPException(404, 'Không tìm thấy tệp.')
    return FileResponse(path, media_type=media_type, filename=download_name, headers={'Cache-Control': 'no-store'})


@app.api_route('/api/projects/{tid}/video/{kind}', methods=['GET', 'HEAD'])
def video_file(tid: int, kind: str, download: bool = False):
    if kind == 'legacy':
        lv = _legacy_video(tid)
        if not lv:
            raise HTTPException(404, 'Không có video cũ')
        with connect() as db:
            fn = db.execute('SELECT filename FROM video_assets WHERE trend_id=?', (tid,)).fetchone()['filename']
        return _file(tf_jobs.VIDEO_DIR / fn, 'video/mp4', f'TrendFlow_{tid}_old.mp4' if download else None)
    if kind not in ('preview', 'final'):
        raise HTTPException(404, 'Không hợp lệ')
    r = tf_jobs.latest_render(tid, kind)
    if not r:
        raise HTTPException(404, 'Chưa có video')
    return _file(tf_jobs.VIDEO_DIR / Path(r['filename']).name, 'video/mp4', f'TrendFlow_{tid}_{kind}.mp4' if download else None)


@app.api_route('/api/projects/{tid}/sheet/{kind}', methods=['GET', 'HEAD'])
def sheet_file(tid: int, kind: str):
    r = tf_jobs.latest_render(tid, kind) if kind in ('preview', 'final') else None
    if not r or not r.get('sheet_filename'):
        raise HTTPException(404, 'Chưa có ảnh kiểm tra')
    return _file(tf_jobs.VIDEO_DIR / Path(r['sheet_filename']).name, 'image/png')


@app.api_route('/api/projects/{tid}/credits', methods=['GET', 'HEAD'])
def credits_file(tid: int):
    r = tf_jobs.latest_render(tid, 'final')
    if not r:
        raise HTTPException(404, 'Chưa có bản cuối')
    return _file((tf_jobs.VIDEO_DIR / Path(r['filename']).name).with_suffix('.credits.txt'), 'text/plain; charset=utf-8',
                 f'TrendFlow_{tid}_nguon_va_caption.txt')


@app.api_route('/api/projects/{tid}/audio', methods=['GET', 'HEAD'])
def audio_file(tid: int, sample: bool = False):
    if sample:
        return _file(tf_jobs.VOICE_DIR / f'sample_{tid}.mp3', 'audio/mpeg')
    vt = tf_jobs.voice_track(tid)
    if not vt:
        raise HTTPException(404, 'Chưa có giọng đọc')
    return _file(vt['path'], 'audio/mpeg')


@app.api_route('/api/media/{media_id}/image', methods=['GET', 'HEAD'])
def media_image(media_id: int):
    with connect() as db:
        r = db.execute('SELECT local_filename FROM media_cache WHERE id=?', (media_id,)).fetchone()
    if not r or not r['local_filename']:
        raise HTTPException(404, 'Không có ảnh')
    return _file(tf_media.MEDIA_DIR / Path(r['local_filename']).name, 'image/jpeg')


# ------------------------------------------------------------------ Visual Studio (manual photo selection)

class StudioSegBody(BaseModel):
    keywords: str | None = None
    picks: list[dict] | None = None


class StudioAddBody(BaseModel):
    candidate: dict
    credit: str = ''


class StudioUploadBody(BaseModel):
    data_url: str
    filename: str = ''
    credit: str = ''


class StudioResegBody(BaseModel):
    count: int | None = None


def _studio(tid):
    p = _require_project(tid)
    _require_draft(tid)
    settings = get_settings()
    sel, tl, approx = tf_jobs.studio_state(tid, p, settings)
    return p, settings, sel, [t['text'] for t in tl]


def _studio_out(tid):
    p = tf_jobs.project_row(tid)
    settings = get_settings()
    sel, tl, approx = tf_jobs.studio_state(tid, p, settings)
    out = tf_studio.payload(sel, tl, approx)
    out['sources'] = tf_studio.sources(settings)
    out['voice_ready'] = not approx
    return out


def _studio_call(fn):
    try:
        return fn()
    except tf_studio.StudioError as exc:
        raise HTTPException(400, str(exc))


@app.get('/api/projects/{tid}/studio')
def studio_get(tid: int):
    _require_project(tid)
    return _studio_out(tid)


@app.post('/api/projects/{tid}/studio/resegment')
def studio_resegment(tid: int, body: StudioResegBody):
    p, settings, sel, sents = _studio(tid)
    if body.count is not None and not 1 <= body.count <= tf_studio.MAX_SEGMENTS:
        raise HTTPException(400, f'Số đoạn từ 1 đến {tf_studio.MAX_SEGMENTS}')
    tf_jobs.studio_state(tid, p, settings, count=body.count, force=True)
    return _studio_out(tid)


@app.put('/api/projects/{tid}/studio/segments/{seg_id}')
def studio_segment(tid: int, seg_id: str, body: StudioSegBody):
    p, settings, sel, sents = _studio(tid)
    _studio_call(lambda: tf_studio.update_segment(tid, sel, sents, seg_id, body.keywords, body.picks))
    return _studio_out(tid)


@app.post('/api/projects/{tid}/studio/segments/{seg_id}/merge')
def studio_merge(tid: int, seg_id: str):
    p, settings, sel, sents = _studio(tid)
    _studio_call(lambda: tf_studio.merge(tid, sel, sents, seg_id))
    return _studio_out(tid)


@app.post('/api/projects/{tid}/studio/segments/{seg_id}/split')
def studio_split(tid: int, seg_id: str):
    p, settings, sel, sents = _studio(tid)
    _studio_call(lambda: tf_studio.split(tid, sel, sents, seg_id))
    return _studio_out(tid)


@app.post('/api/projects/{tid}/studio/segments/{seg_id}/add')
def studio_add(tid: int, seg_id: str, body: StudioAddBody):
    p, settings, sel, sents = _studio(tid)
    row = _studio_call(lambda: tf_studio.fetch_candidate(body.candidate))
    _studio_call(lambda: tf_studio.add_pick(tid, sel, sents, seg_id, row, body.credit))
    return _studio_out(tid)


@app.post('/api/projects/{tid}/studio/segments/{seg_id}/upload')
def studio_upload(tid: int, seg_id: str, body: StudioUploadBody):
    p, settings, sel, sents = _studio(tid)
    row = _studio_call(lambda: tf_studio.store_upload(body.data_url, body.filename, body.credit))
    _studio_call(lambda: tf_studio.add_pick(tid, sel, sents, seg_id, row, body.credit))
    return _studio_out(tid)


@app.post('/api/projects/{tid}/studio/suggest')
def studio_suggest(tid: int):
    p, settings, sel, sents = _studio(tid)
    trend = tf_jobs.trend_row(tid)
    kws = tf_studio.suggest_keywords([s.get('text', '') for s in sel['segments']], trend.get('title_vi') or trend['title'])
    for seg, kw in zip(sel['segments'], kws):
        seg['keywords'] = kw
    tf_studio.save(tid, sel['narration_hash'], sel['segments'])
    return _studio_out(tid)


@app.post('/api/projects/{tid}/studio/voice')
def studio_voice(tid: int):
    _require_project(tid)
    _require_draft(tid)
    return _enqueue(tid, 'voice')


@app.get('/api/projects/{tid}/studio/search')
def studio_search(tid: int, source: str = 'article', q: str = ''):
    _require_project(tid)
    items = _studio_call(lambda: tf_studio.search(tid, source, q, get_settings()))
    out = []
    for c in items[:40]:
        if not c.get('url'):
            continue
        thumb = c.get('thumb_src') or c['url']
        out.append({**{k: c.get(k) for k in ('url', 'page_url', 'provider', 'title', 'description', 'creator', 'license',
                                              'license_url', 'rights_class', 'width', 'height', 'primary')},
                    'thumb': '/api/studio/thumb?' + __import__('urllib.parse').parse.urlencode({'u': thumb, 'r': c.get('page_url') or ''})})
    return {'items': out, 'source': source}


@app.get('/api/studio/thumb')
def studio_thumb(u: str, r: str = ''):
    from fastapi.responses import Response
    try:
        data = tf_studio.thumbnail(u, r)
    except Exception:
        raise HTTPException(404, 'Không tải được ảnh xem trước')
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'max-age=3600'})


# ------------------------------------------------------------------ jobs & pending

@app.get('/api/jobs')
def jobs(limit: int = 30):
    with connect() as db:
        rows = [tf_jobs.job_dict(r) for r in db.execute(
            '''SELECT j.*, COALESCE(p.title, t.title) AS title FROM automation_jobs j JOIN trends t ON t.id=j.trend_id
               LEFT JOIN script_projects p ON p.trend_id=j.trend_id WHERE j.kind IN ('generate','preview','final')
               ORDER BY j.id DESC LIMIT ?''', (max(1, min(100, limit)),))]
    return {'items': rows}


@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: int):
    try:
        return {'ok': True, 'status': tf_jobs.cancel(jid)}
    except tf_jobs.JobFailed as exc:
        raise HTTPException(404, str(exc))


class OrderBody(BaseModel):
    ids: list[int]


@app.put('/api/pending/order')
def pending_order(body: OrderBody):
    with connect() as db:
        existing = {r[0] for r in db.execute("SELECT trend_id FROM workflow_items WHERE stage='pending'")}
        if set(body.ids) != existing or len(body.ids) != len(existing):
            raise HTTPException(409, 'Danh sách Pending đã thay đổi. Tải lại trang.')
        for i, tid in enumerate(body.ids, 1):
            db.execute("UPDATE workflow_items SET position=? WHERE trend_id=? AND stage='pending'", (i, tid))
    return {'ok': True}


@app.post('/api/pending/start')
def pending_start():
    queued, skipped = [], []
    with connect() as db:
        ids = [r[0] for r in db.execute("SELECT trend_id FROM workflow_items WHERE stage='pending' ORDER BY position")]
    for tid in ids:
        with connect() as db:
            w = db.execute('SELECT approved_hash FROM workflow_items WHERE trend_id=?', (tid,)).fetchone()
        if not w['approved_hash'] or w['approved_hash'] != tf_jobs.approval_hash(tid):
            skipped.append({'trend_id': tid, 'reason': 'Nội dung đổi sau khi duyệt'})
            continue
        try:
            queued.append(tf_jobs.enqueue(tid, 'final', origin='batch'))
        except tf_jobs.JobFailed as exc:
            skipped.append({'trend_id': tid, 'reason': str(exc)})
    return {'queued': len(queued), 'skipped': skipped}


@app.post('/api/pending/cancel-all')
def pending_cancel_all():
    with connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM automation_jobs WHERE kind='final' AND status IN ('queued','running','cancel_requested')")]
    for jid in ids:
        tf_jobs.cancel(jid)
    return {'ok': True, 'cancelled': len(ids)}


@app.delete('/api/pending/{tid}')
def pending_remove(tid: int):
    if tf_jobs.stage_of(tid) != 'pending':
        raise HTTPException(404, 'Không nằm trong Pending.')
    return to_draft(tid)


@app.get('/api/overview')
def overview():
    with connect() as db:
        c = {r['stage']: r['n'] for r in db.execute('SELECT stage, COUNT(*) n FROM workflow_items GROUP BY stage')}
        running = db.execute("SELECT COUNT(*) FROM automation_jobs WHERE status IN ('queued','running','cancel_requested') AND kind IN ('generate','preview','final','voice')").fetchone()[0]
    return {'drafts': c.get('draft', 0), 'pending': c.get('pending', 0), 'done': c.get('done', 0), 'jobs': running, 'version': VERSION}


# ------------------------------------------------------------------ settings & health

class SettingsBody(BaseModel):
    visual_mode: str | None = None
    pexels_key: str | None = None
    image_source: str | None = None
    azure_key: str | None = None
    gemini_key: str | None = None
    image_verifier: str | None = None
    serpapi_key: str | None = None
    gemini_voice: str | None = None
    gemini_model: str | None = None
    azure_region: str | None = None
    allow_fallback_voice: bool | None = None
    rights_policy: str | None = None
    voice_id: str | None = None
    tts_provider: str | None = None
    final_quality: str | None = None
    target_seconds: int | None = None


def _masked(s):
    s = dict(s)
    for name in ('azure_key', 'gemini_key', 'serpapi_key', 'pexels_key'):
        k = s.get(name) or ''
        s[name] = ('••••' + k[-4:]) if k else ''
    return s


@app.post('/api/voice-test')
def voice_test():
    s = get_settings()
    dest = tf_jobs.VOICE_DIR / 'voice_test.mp3'
    tf_jobs.VOICE_DIR.mkdir(exist_ok=True)
    try:
        r = tf_tts.sample('Xin chào, đây là giọng đọc tin tức của TrendFlow. Chúc bạn một ngày tốt lành.', dest,
                          s['voice_id'], s['tts_provider'], azure=tf_tts.azure_config(s),
                          allow_fallback=bool(s.get('allow_fallback_voice')), gemini=tf_tts.gemini_config(s))
    except tf_tts.TTSFailure as exc:
        raise HTTPException(502, str(exc))
    return {'url': f'/api/voice-test/audio?v={uuid.uuid4().hex[:6]}', 'provider': r['provider_label'],
            'fallback_reason': r.get('fallback_reason')}


@app.api_route('/api/voice-test/audio', methods=['GET', 'HEAD'])
def voice_test_audio():
    return _file(tf_jobs.VOICE_DIR / 'voice_test.mp3', 'audio/mpeg')


@app.get('/api/settings')
def settings_get():
    return {'settings': _masked(get_settings()), 'voices': tf_tts.VOICES, 'gemini_voices': tf_tts.GEMINI_VOICES, 'providers': tf_tts.PROVIDER_LABELS,
            'policies': {'strict': 'Chỉ ảnh CC0 / Public domain / CC BY', 'open': 'Thêm CC BY-SA (khuyến nghị — nhiều ảnh nhất trên Wikimedia)',
                         'editorial': 'Thêm ảnh của chính bài báo gốc (bản quyền tòa soạn — bạn tự chịu trách nhiệm khi đăng)'}}


@app.put('/api/settings')
def settings_put(body: SettingsBody):
    v = {k: x for k, x in body.model_dump().items() if x is not None}
    if 'image_source' in v and v['image_source'] not in ('article', 'search'):
        raise HTTPException(400, 'Nguồn ảnh không hợp lệ')
    if 'rights_policy' in v and v['rights_policy'] not in tf_media.POLICY_CLASSES:
        raise HTTPException(400, 'Chính sách ảnh không hợp lệ')
    if 'voice_id' in v and v['voice_id'] not in tf_tts.VOICES:
        raise HTTPException(400, 'Giọng không hợp lệ')
    if 'tts_provider' in v and v['tts_provider'] not in ('auto', 'edge', 'azure', 'gemini', 'gtts', 'macos'):
        raise HTTPException(400, 'Nhà cung cấp giọng không hợp lệ')
    if 'final_quality' in v and v['final_quality'] not in ('720p', '1080p'):
        raise HTTPException(400, 'Chất lượng không hợp lệ')
    if 'target_seconds' in v and not 45 <= v['target_seconds'] <= 60:
        raise HTTPException(400, 'Mục tiêu 45–60 giây')
    if 'image_verifier' in v and v['image_verifier'] not in ('auto', 'gemini', 'ollama'):
        raise HTTPException(400, 'AI kiểm tra ảnh không hợp lệ')
    if 'visual_mode' in v and v['visual_mode'] not in ('manual', 'auto'):
        raise HTTPException(400, 'Chế độ ảnh không hợp lệ')
    for name in ('azure_key', 'gemini_key', 'serpapi_key', 'pexels_key'):
        if (v.get(name) or '').startswith('••••'):
            v.pop(name)   # masked value sent back unchanged
    if 'gemini_voice' in v and v['gemini_voice'] not in tf_tts.GEMINI_VOICES:
        raise HTTPException(400, 'Giọng Gemini không hợp lệ')
    if 'gemini_model' in v and v['gemini_model'] and not re.fullmatch(r'[a-z0-9.\-]{5,60}', v['gemini_model']):
        raise HTTPException(400, 'Tên model Gemini không hợp lệ')
    if 'azure_region' in v and not re.fullmatch(r'[a-z0-9]{3,30}', v['azure_region'] or ''):
        raise HTTPException(400, 'Region Azure không hợp lệ (ví dụ: southeastasia)')
    return {'settings': _masked(set_settings(v))}


@app.get('/api/ping')
def ping():
    """Cheap liveness check used by the `trendflow` command (no Ollama/network calls)."""
    return {'app': 'TrendFlow', 'version': VERSION, 'pid': os.getpid(), 'data_dir': str(tf_paths.DATA_DIR)}


@app.get('/api/health')
def health():
    st = tf_llm.status()
    return {'version': VERSION, 'ollama': st, 'ffmpeg': bool(shutil.which('ffmpeg')), 'macos_voice': tf_tts.macos_voice_available(),
            'database': str(tf_db.DB_PATH.name), 'migration': MIGRATION}


@app.post('/api/diagnostics/network')
def network_check():
    import requests
    targets = {'VnExpress RSS': 'https://vnexpress.net/rss/tin-moi-nhat.rss', 'BBC RSS': 'https://feeds.bbci.co.uk/news/world/rss.xml',
               'Wikipedia': 'https://vi.wikipedia.org/w/api.php?action=query&format=json&meta=siteinfo',
               'Wikimedia Commons': 'https://commons.wikimedia.org/w/api.php?action=query&format=json&meta=siteinfo',
               'Openverse': 'https://api.openverse.org/v1/images/?q=hanoi&page_size=1',
               'Edge TTS': 'https://speech.platform.bing.com/', 'Google gTTS': 'https://translate.google.com/'}
    out = {}
    for name, url in targets.items():
        try:
            r = requests.get(url, timeout=8, headers={'User-Agent': tf_media.USER_AGENT})
            out[name] = {'ok': r.status_code < 500, 'status': r.status_code}
        except requests.RequestException as exc:
            out[name] = {'ok': False, 'status': type(exc).__name__}
    return out
