"""One job queue for everything heavy (script, preview, final). One job at a time keeps an
18 GB Mac responsive; previews jump ahead of batch production; every job is cancellable."""
from __future__ import annotations

import json
import os
import re
import logging
import shutil
import threading
import traceback
import uuid
from pathlib import Path

import tf_article
import tf_media
import tf_qa
import tf_render
import tf_script
import tf_tts
from tf_db import ROOT, connect, get_settings, now_iso
from tf_text import narration_hash, scene_segments, sha, word_count

DATA_DIR = Path(os.getenv('TRENDFLOW_DATA_DIR') or ROOT)   # personal data lives outside the code (see tf_paths)
VOICE_DIR = DATA_DIR / 'voice_outputs'
VIDEO_DIR = DATA_DIR / 'video_outputs'
_EVENTS: dict[int, threading.Event] = {}
_LOCK = threading.RLock()
_WORKER: threading.Thread | None = None
_WAKE = threading.Event()


def _rm(path):
    """Delete if possible. On Windows a file still being streamed to the browser is locked; leave it."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logging.warning('Không xoá được %s (%s) — sẽ để lại.', path, exc)


class JobCancelled(Exception):
    pass


class JobFailed(RuntimeError):
    pass


# ------------------------------------------------------------------ queue

def enqueue(tid: int, kind: str, origin='user', params: dict | None = None) -> int:
    with connect() as db:
        active = db.execute("""SELECT id FROM automation_jobs WHERE trend_id=? AND status IN ('queued','running','cancel_requested')
                               AND kind IN ('generate','preview','final')""", (tid,)).fetchone()
        if active:
            raise JobFailed('Dự án này đang có tác vụ chạy. Đợi xong hoặc huỷ trước.')
        cur = db.execute('''INSERT INTO automation_jobs(trend_id,status,step,created_at,updated_at,origin,kind,progress,result_json)
                            VALUES(?,'queued','Đang chờ đến lượt',?,?,?,?,0,?)''',
                         (tid, now_iso(), now_iso(), origin, kind, json.dumps(params or {})))
        jid = cur.lastrowid
    with _LOCK:
        _EVENTS[jid] = threading.Event()
    ensure_worker()
    return jid


def cancel(jid: int) -> str:
    with connect() as db:
        row = db.execute('SELECT status FROM automation_jobs WHERE id=?', (jid,)).fetchone()
        if not row:
            raise JobFailed('Không tìm thấy tác vụ.')
        if row['status'] in ('done', 'error', 'cancelled'):
            return row['status']
        new = 'cancelled' if row['status'] == 'queued' else 'cancel_requested'
        db.execute('''UPDATE automation_jobs SET status=?, step=?, updated_at=?, finished_at=CASE WHEN ?='cancelled' THEN ? ELSE finished_at END
                      WHERE id=?''', (new, 'Đã huỷ' if new == 'cancelled' else 'Đang dừng…', now_iso(), new, now_iso(), jid))
    with _LOCK:
        _EVENTS.setdefault(jid, threading.Event()).set()
    return new


def cancel_trend(tid: int):
    with connect() as db:
        ids = [r['id'] for r in db.execute("SELECT id FROM automation_jobs WHERE trend_id=? AND status IN ('queued','running','cancel_requested')", (tid,))]
    for jid in ids:
        cancel(jid)
    return ids


def active_job(tid: int):
    with connect() as db:
        r = db.execute("""SELECT * FROM automation_jobs WHERE trend_id=? AND kind IN ('generate','preview','final')
                          ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
    return job_dict(r) if r else None


def job_dict(r):
    d = dict(r)
    try:
        d['result'] = json.loads(d.pop('result_json') or '{}')
    except ValueError:
        d['result'] = {}
    return d


def ensure_worker():
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_loop, daemon=True, name='TrendFlowJobs')
            _WORKER.start()
    _WAKE.set()


def _next_job():
    with connect() as db:
        row = db.execute("""SELECT * FROM automation_jobs WHERE status='queued' AND kind IN ('generate','preview')
                            ORDER BY id LIMIT 1""").fetchone()
        if row:
            return row
        return db.execute("""SELECT j.* FROM automation_jobs j LEFT JOIN workflow_items w ON w.trend_id=j.trend_id
                             WHERE j.status='queued' AND j.kind='final' ORDER BY COALESCE(w.position, 1e9), j.id LIMIT 1""").fetchone()


def _loop():
    while True:
        row = _next_job()
        if not row:
            _WAKE.clear()
            _WAKE.wait(timeout=5)
            continue
        with connect() as db:
            claimed = db.execute("UPDATE automation_jobs SET status='running', step='Bắt đầu…', updated_at=? WHERE id=? AND status='queued'",
                                 (now_iso(), row['id'])).rowcount
        if claimed:
            _run(dict(row))


def _run(job):
    jid, tid, kind = job['id'], job['trend_id'], job['kind']
    with _LOCK:
        event = _EVENTS.setdefault(jid, threading.Event())
    try:
        params = json.loads(job.get('result_json') or '{}')
    except ValueError:
        params = {}

    def checkpoint():
        if event.is_set():
            raise JobCancelled()
        with connect() as db:
            st = db.execute('SELECT status FROM automation_jobs WHERE id=?', (jid,)).fetchone()
        if not st or st['status'] in ('cancel_requested', 'cancelled'):
            raise JobCancelled()

    last = {'p': 0.0}

    def report(step, p=None):
        checkpoint()
        if p is not None:
            last['p'] = p
        with connect() as db:
            db.execute("UPDATE automation_jobs SET step=?, progress=?, updated_at=? WHERE id=? AND status='running'",
                       (step[:300], round(last['p'], 3), now_iso(), jid))

    ctx = Ctx(tid, report, checkpoint)
    try:
        result = {'generate': run_generate, 'preview': run_preview, 'final': run_final}[kind](ctx, params)
        with connect() as db:
            db.execute("""UPDATE automation_jobs SET status='done', step=?, progress=1, result_json=?, error=NULL,
                          updated_at=?, finished_at=? WHERE id=? AND status='running'""",
                       (result.get('message', 'Hoàn tất'), json.dumps(result, ensure_ascii=False), now_iso(), now_iso(), jid))
    except JobCancelled:
        with connect() as db:
            db.execute("UPDATE automation_jobs SET status='cancelled', step='Đã huỷ theo yêu cầu', updated_at=?, finished_at=? WHERE id=?",
                       (now_iso(), now_iso(), jid))
    except Exception as exc:
        logging.error('Job %s (%s, trend %s) failed:\n%s', jid, kind, tid, traceback.format_exc())
        detail = getattr(exc, 'payload', None)
        with connect() as db:
            db.execute("""UPDATE automation_jobs SET status='error', step=?, error=?, result_json=?, updated_at=?, finished_at=?
                          WHERE id=?""", (('Lỗi: ' + str(exc))[:300], str(exc)[:1500],
                                          json.dumps(detail or {}, ensure_ascii=False), now_iso(), now_iso(), jid))
    finally:
        with _LOCK:
            _EVENTS.pop(jid, None)


class Ctx:
    def __init__(self, tid, report, checkpoint):
        self.tid, self.report, self.checkpoint = tid, report, checkpoint

    def cancel(self):
        try:
            self.checkpoint()
        except JobCancelled:
            raise


# ------------------------------------------------------------------ data helpers

def trend_row(tid):
    with connect() as db:
        r = db.execute('''SELECT t.*, s.name AS source_name, s.market FROM trends t LEFT JOIN sources s ON s.id=t.source_id
                          WHERE t.id=?''', (tid,)).fetchone()
    if not r:
        raise JobFailed('Không tìm thấy tin.')
    return dict(r)


def project_row(tid):
    with connect() as db:
        r = db.execute('SELECT * FROM script_projects WHERE trend_id=?', (tid,)).fetchone()
    return dict(r) if r else None


def stage_of(tid):
    with connect() as db:
        r = db.execute('SELECT stage FROM workflow_items WHERE trend_id=?', (tid,)).fetchone()
    return r['stage'] if r else ('draft' if project_row(tid) else None)


def voice_track(tid):
    with connect() as db:
        r = db.execute('SELECT * FROM voice_tracks WHERE trend_id=?', (tid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d['timings'] = json.loads(d.pop('timings_json'))
    d['path'] = VOICE_DIR / d['filename']
    return d


def scene_plan(tid):
    with connect() as db:
        r = db.execute('SELECT * FROM scene_plans WHERE trend_id=?', (tid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d['plan'] = json.loads(d.pop('plan_json'))
    d['report'] = json.loads(d.pop('report_json'))
    return d


def latest_render(tid, kind):
    with connect() as db:
        r = db.execute('SELECT * FROM renders WHERE trend_id=? AND kind=? ORDER BY id DESC LIMIT 1', (tid, kind)).fetchone()
    if not r:
        return None
    d = dict(r)
    d['qa'] = json.loads(d.pop('qa_json') or '{}')
    return d


def script_check(tid):
    with connect() as db:
        r = db.execute('SELECT * FROM script_checks WHERE trend_id=?', (tid,)).fetchone()
    return {'narration_hash': r['narration_hash'], **json.loads(r['report_json'])} if r else None


def calibrated_wps():
    with connect() as db:
        rows = db.execute('SELECT timings_json, duration FROM voice_tracks ORDER BY created_at DESC LIMIT 12').fetchall()
    rates = []
    for r in rows:
        try:
            t = json.loads(r['timings_json'])
            if t.get('provider') == 'edge' and r['duration'] > 20:
                rates.append(len(t['words']) / r['duration'])
        except (ValueError, KeyError, TypeError):
            pass
    return max(2.6, min(4.5, sum(rates) / len(rates))) if rates else tf_script.DEFAULT_WPS


def approval_hash(tid):
    p = project_row(tid)
    sp = scene_plan(tid)
    if not p or not sp:
        return None
    return sha(json.dumps([p['title'], p['hook'], p['narration'], p['caption'], p['hashtags'], sp['plan_hash']], ensure_ascii=False))


def save_project(tid, data, target_seconds):
    segs = scene_segments(data['narration'])
    scenes = [{'voice': s, 'visual': '', 'on_screen': ''} for s in segs]
    now = now_iso()
    with connect() as db:
        db.execute('''INSERT INTO script_projects(trend_id,title,hook,narration,scenes_json,caption,hashtags,verification,status,
                      duration_target,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'Bản nháp',?,?,?)
                      ON CONFLICT(trend_id) DO UPDATE SET title=excluded.title,hook=excluded.hook,narration=excluded.narration,
                      scenes_json=excluded.scenes_json,caption=excluded.caption,hashtags=excluded.hashtags,
                      verification=excluded.verification,duration_target=excluded.duration_target,updated_at=excluded.updated_at''',
                   (tid, data['title'], data.get('headline', ''), data['narration'], json.dumps(scenes, ensure_ascii=False),
                    data.get('caption', ''), data.get('hashtags', ''), data.get('verification', ''), target_seconds, now, now))
        db.execute("INSERT OR IGNORE INTO workflow_items(trend_id,stage,position,created_at,updated_at) VALUES(?,'draft',0,?,?)", (tid, now, now))


def synth_voice(ctx, tid, narration, settings, label='', p_range=None):
    segs = scene_segments(narration)
    VOICE_DIR.mkdir(exist_ok=True)
    filename = f'{tid}_{uuid.uuid4().hex[:12]}.mp3'
    path = VOICE_DIR / filename
    ctx.report(f'Đang tạo giọng đọc{label}…')
    def tts_progress(m):
        frac = re.search(r'câu (\d+)/(\d+)', m)
        if p_range and frac:
            a, b = p_range
            ctx.report(m, a + (b - a) * int(frac.group(1)) / int(frac.group(2)))
        else:
            ctx.report(m)
    try:
        result = tf_tts.synthesize(segs, path, settings['voice_id'], 0, settings['tts_provider'], cancel=ctx.checkpoint,
                                   progress=tts_progress, azure=tf_tts.azure_config(settings),
                                   allow_fallback=bool(settings.get('allow_fallback_voice')),
                                   gemini=tf_tts.gemini_config(settings))
    except tf_tts.TTSFailure as exc:
        _rm(path)
        exc.payload = {'tts_attempts': getattr(exc, 'attempt_log', [])}
        raise
    result['config'] = tf_tts.config_signature(settings)
    if result['provider'] not in tf_tts.expected_providers(settings):   # defensive: never store a silent substitute
        _rm(path)
        raise JobFailed(f"Giọng tạo ra là {result['provider']}, không phải dịch vụ đã chọn.")
    old = voice_track(tid)
    with connect() as db:
        db.execute('''INSERT INTO voice_tracks(trend_id,filename,narration_hash,provider,voice_id,duration,timings_json,created_at)
                      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(trend_id) DO UPDATE SET filename=excluded.filename,
                      narration_hash=excluded.narration_hash,provider=excluded.provider,voice_id=excluded.voice_id,
                      duration=excluded.duration,timings_json=excluded.timings_json,created_at=excluded.created_at''',
                   (tid, filename, narration_hash(narration), result['provider'], settings['voice_id'], result['duration'],
                    tf_tts.timings_json(result), now_iso()))
    if old and old['filename'] != filename:
        # Keep the file if a finished/approved render still uses it.
        with connect() as db:
            in_use = db.execute("SELECT 1 FROM scene_plans WHERE trend_id=? AND voice_filename=?", (tid, old['filename'])).fetchone()
        if not in_use:
            _rm(old['path'])
    return voice_track(tid)


def voice_is_current(vt, project, settings) -> tuple[bool, str]:
    """Stored audio may be reused only if text, voice, provider and TTS settings all still match.
    (Before 3.2.2 only text+voice were compared, so an old gTTS file kept being reused after Edge worked.)"""
    if not vt or not project:
        return False, 'Chưa có giọng đọc'
    if vt['narration_hash'] != narration_hash(project['narration']):
        return False, 'Lời thoại đã đổi sau khi tạo giọng'
    if vt['voice_id'] != settings['voice_id']:
        return False, 'Đã đổi giọng đọc trong Cài đặt'
    if vt['provider'] not in tf_tts.expected_providers(settings):
        return False, f"Audio cũ là {tf_tts.PROVIDER_LABELS.get(vt['provider'], vt['provider'])} — không đúng dịch vụ đang chọn"
    if vt['timings'].get('config') != tf_tts.config_signature(settings):
        return False, 'Cài đặt giọng đã đổi (hoặc audio tạo từ bản cũ)'
    if not vt['path'].is_file():
        return False, 'Thiếu file MP3'
    return True, ''


def current_voice(ctx, tid, project, settings):
    vt = voice_track(tid)
    ok, why = voice_is_current(vt, project, settings)
    if ok:
        return vt
    if vt:
        ctx.report(f'Tạo lại giọng đọc: {why}.')
    return synth_voice(ctx, tid, project['narration'], settings, p_range=(0.03, 0.11))


# ------------------------------------------------------------------ pipelines

def run_generate(ctx, params):
    tid = ctx.tid
    if stage_of(tid) not in (None, 'draft'):
        raise JobFailed('Dự án đang ở Pending/Thành phẩm. Chuyển về bản nháp trước khi viết lại.')
    settings = get_settings()
    target = int(params.get('target_seconds') or settings['target_seconds'])
    trend = trend_row(tid)
    ctx.report('Đang đọc bài báo gốc…', 0.05)
    article = tf_media.article_text(tid)
    if len(article['text']) < 400:
        raise JobFailed('Không đọc được đủ nội dung bài gốc (chỉ có tóm tắt RSS). TrendFlow không bịa thêm dữ kiện; hãy chọn tin khác.')
    ctx.report('AI đang viết kịch bản tiếng Việt…', 0.15)
    data = tf_script.generate(trend, article, target, calibrated_wps(), cancel=ctx.checkpoint, angle=params.get('angle', ''))
    attempts = []
    for attempt in range(4):
        save_project(tid, data, target)
        ctx.report(f'Đo độ dài bằng giọng đọc thật (lần {attempt + 1})…', 0.3 + 0.17 * attempt)
        vt = synth_voice(ctx, tid, data['narration'], settings, f' (đo độ dài, lần {attempt + 1})',
                         p_range=(0.3 + 0.17 * attempt, 0.42 + 0.17 * attempt))
        d = vt['duration']
        attempts.append({'words': word_count(data['narration']), 'seconds': d, 'provider': vt['provider']})
        if 45 <= d <= 60 or attempt == 3:
            break
        ctx.report(f'Lời đọc dài {d:.1f}s — AI chỉnh độ dài cho đạt 45–60s…', 0.3 + 0.15 * attempt)
        data = tf_script.revise_length(data, article, d, target, cancel=ctx.checkpoint)
    check = tf_script.fact_check(data['narration'], article['text'] + ' ' + trend.get('title', ''))
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO script_checks(trend_id,narration_hash,report_json,created_at) VALUES(?,?,?,?)',
                   (tid, narration_hash(data['narration']), json.dumps(check, ensure_ascii=False), now_iso()))
    vt = voice_track(tid)
    ok = 45 <= vt['duration'] <= 60
    return {'message': f"Kịch bản {word_count(data['narration'])} từ, đọc {vt['duration']:.1f}s" + ('' if ok else ' — chưa đạt 45–60s, cần biên tập thêm'),
            'attempts': attempts, 'duration': vt['duration'], 'length_ok': ok, 'provider': vt['provider']}


def run_preview(ctx, params):
    tid = ctx.tid
    if stage_of(tid) != 'draft':
        raise JobFailed('Chỉ tạo bản xem thử cho bản nháp.')
    settings = get_settings()
    if params.get('verifier') in ('auto', 'gemini', 'ollama'):
        settings = dict(settings, image_verifier=params['verifier'])   # chosen for this run in the editor
    project = project_row(tid)
    if not project or word_count(project['narration']) < 40:
        raise JobFailed('Kịch bản chưa có lời thoại đủ dài.')
    trend = trend_row(tid)
    ctx.report('Chuẩn bị giọng đọc…', 0.03)
    vt = current_voice(ctx, tid, project, settings)
    nh = narration_hash(project['narration'])
    # Fact check (deterministic, cheap) is refreshed for the current text.
    article = tf_media.article_text(tid)
    check = tf_script.fact_check(project['narration'], article['text'] + ' ' + trend.get('title', ''))
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO script_checks(trend_id,narration_hash,report_json,created_at) VALUES(?,?,?,?)',
                   (tid, nh, json.dumps(check, ensure_ascii=False), now_iso()))
    sp = scene_plan(tid)
    reuse = (sp and not params.get('refresh_media') and sp['narration_hash'] == nh and sp['voice_filename'] == vt['filename']
             and sp['passed'] and sp['plan'].get('headline') == project['hook']
             and sp['plan'].get('image_source', 'search') == settings['image_source']
             and (settings['image_source'] == 'article' or sp['plan'].get('policy') == settings['rights_policy'])
             and all((tf_media.MEDIA_DIR / m['local_filename']).is_file() for m in sp['plan']['media'].values()))
    if reuse:
        plan, report = sp['plan'], sp['report']
        ctx.report('Dùng lại kế hoạch ảnh đã kiểm tra cho lời thoại này.', 0.3)
    else:
        if settings['image_source'] == 'article':
            ctx.report('Đang lấy ảnh từ bài gốc và báo cùng sự kiện…', 0.12)
            built = tf_article.build_article_plan(tid, vt['timings'], project['hook'], settings, cancel=ctx.checkpoint,
                                                  progress=lambda m, p=None: ctx.report(m, None if p is None else 0.12 + 0.23 * p))
        else:
            ctx.report('Đang tìm ảnh tư liệu cho từng câu…', 0.12)
            built = tf_media.build_plan(tid, vt['timings'], project['hook'], settings, cancel=ctx.checkpoint,
                                        progress=lambda m: ctx.report(m))
        plan, report = built['plan'], built['report']
        ph = tf_media.plan_hash(plan, nh, vt['filename'])
        with connect() as db:
            db.execute('''INSERT OR REPLACE INTO scene_plans(trend_id,plan_json,plan_hash,narration_hash,voice_filename,passed,report_json,created_at)
                          VALUES(?,?,?,?,?,?,?,?)''', (tid, json.dumps(plan, ensure_ascii=False), ph, nh, vt['filename'],
                                                      int(report['passed']), json.dumps(report, ensure_ascii=False), now_iso()))
        sp = scene_plan(tid)
    if not report['passed']:
        err = JobFailed('KHÔNG ĐẠT ảnh thật — không dựng video. ' + ' '.join(report['issues']))
        err.payload = {'plan_report': report}
        raise err
    ctx.report('Đang dựng video xem thử…', 0.35)
    VIDEO_DIR.mkdir(exist_ok=True)
    out = VIDEO_DIR / f'{tid}_preview_{uuid.uuid4().hex[:12]}.mp4'
    manifest = tf_render.render(plan, vt['timings'], {'category': trend.get('category')}, vt['path'], out,
                                quality=settings['preview_quality'], preview=True, cancel=ctx.checkpoint,
                                progress=lambda p, m: ctx.report(m, 0.35 + 0.5 * p))
    ctx.report('Đang kiểm tra video đầu ra (khung hình, âm thanh)…', 0.9)
    sheet = out.with_suffix('.sheet.png')
    qa = tf_qa.run(out, plan, vt['timings'], manifest, final=False, sheet_path=sheet, voice_path=vt['path'])
    qa['manifest'] = manifest
    _store_render(tid, 'preview', out, sp['plan_hash'], manifest, qa, sheet)
    dur_ok = 45 <= vt['duration'] <= 60
    return {'message': f"Video xem thử {manifest['duration']:.1f}s · {report['distinct_photos']} ảnh thật · QA "
                       + ('đạt' if qa['passed'] else 'CHƯA ĐẠT') + ('' if dur_ok else ' · thời lượng ngoài 45–60s'),
            'qa_passed': qa['passed'], 'duration_ok': dur_ok, 'plan_report': report}


def run_final(ctx, params):
    tid = ctx.tid
    with connect() as db:
        w = db.execute('SELECT * FROM workflow_items WHERE trend_id=?', (tid,)).fetchone()
    if not w or w['stage'] != 'pending':
        raise JobCancelled()
    if not w['approved_hash'] or w['approved_hash'] != approval_hash(tid):
        raise JobFailed('Nội dung đã thay đổi sau khi duyệt. Chuyển về bản nháp, Test lại rồi duyệt lại.')
    settings = get_settings()
    sp = scene_plan(tid)
    vt = voice_track(tid)
    if not vt or vt['filename'] != sp['voice_filename'] or not vt['path'].is_file():
        raise JobFailed('Thiếu file giọng đọc của bản đã duyệt. Chuyển về bản nháp và Test lại.')
    trend = trend_row(tid)
    project = project_row(tid)
    ctx.report('Đang dựng bản hoàn chỉnh từ đúng kế hoạch đã duyệt…', 0.05)
    out = VIDEO_DIR / f'{tid}_final_{uuid.uuid4().hex[:12]}.mp4'
    manifest = tf_render.render(sp['plan'], vt['timings'], {'category': trend.get('category')}, vt['path'], out,
                                quality=settings['final_quality'], preview=False, cancel=ctx.checkpoint,
                                progress=lambda p, m: ctx.report(m, 0.05 + 0.8 * p))
    ctx.report('Kiểm tra chất lượng bản cuối…', 0.9)
    sheet = out.with_suffix('.sheet.png')
    qa = tf_qa.run(out, sp['plan'], vt['timings'], manifest, final=True, sheet_path=sheet, voice_path=vt['path'])
    qa['manifest'] = manifest
    if not qa['passed']:
        failed = [c['name'] + ': ' + c['detail'] for c in qa['checks'] if c['blocking'] and not c['ok']]
        _rm(out)
        _rm(sheet)
        raise JobFailed('QA bản cuối không đạt: ' + '; '.join(failed))
    _store_render(tid, 'final', out, sp['plan_hash'], manifest, qa, sheet)
    out.with_suffix('.credits.txt').write_text(credits_text(trend, project, sp['plan']), encoding='utf-8')
    with connect() as db:
        db.execute("UPDATE workflow_items SET stage='done', updated_at=? WHERE trend_id=? AND stage='pending' AND approved_hash=?",
                   (now_iso(), tid, w['approved_hash']))
    return {'message': f"Hoàn tất MP4 {manifest['width']}×{manifest['height']}, {manifest['duration']:.1f}s, QA đạt"}


def _store_render(tid, kind, out, plan_hash, manifest, qa, sheet):
    with connect() as db:
        old = [dict(r) for r in db.execute('SELECT filename, sheet_filename FROM renders WHERE trend_id=? AND kind=?', (tid, kind))]
        db.execute('''INSERT INTO renders(trend_id,kind,filename,plan_hash,duration,width,height,passed,qa_json,sheet_filename,created_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (tid, kind, out.name, plan_hash, manifest['duration'], manifest['width'],
                                                          manifest['height'], int(qa['passed']), json.dumps(qa, ensure_ascii=False),
                                                          sheet.name, now_iso()))
        db.execute('DELETE FROM renders WHERE trend_id=? AND kind=? AND filename!=?', (tid, kind, out.name))
    for o in old:
        if o['filename'] != out.name:
            _rm((VIDEO_DIR / o['filename']))
            _rm((VIDEO_DIR / o['filename']).with_suffix('.credits.txt'))
            if o['sheet_filename']:
                _rm((VIDEO_DIR / o['sheet_filename']))


def credits_text(trend, project, plan):
    lines = [project['title'], '', 'NGUỒN TIN: ' + (trend.get('link') or ''), '',
             'ẢNH (tự động chọn, có giấy phép/nguồn kèm theo):']
    for s in plan['segments']:
        for sh in s['shots']:
            m = plan['media'][str(sh['media_id'])]
            lines.append(f"- Câu {s['index'] + 1}: {m.get('title') or ''} — {m.get('creator') or 'không rõ tác giả'} — "
                         f"{m.get('license') or ''} — {m.get('page_url') or m.get('url')}")
    lines += ['', 'Lưu ý: CC BY / CC BY-SA yêu cầu ghi công tác giả khi đăng; ảnh "Bài báo gốc" thuộc bản quyền tòa soạn.',
              '', 'CAPTION:', project.get('caption') or '', project.get('hashtags') or '']
    return '\n'.join(lines)


def delete_project_files(tid):
    with connect() as db:
        files = [r['filename'] for r in db.execute('SELECT filename FROM renders WHERE trend_id=?', (tid,))]
        sheets = [r['sheet_filename'] for r in db.execute('SELECT sheet_filename FROM renders WHERE trend_id=?', (tid,)) if r['sheet_filename']]
        voice = db.execute('SELECT filename FROM voice_tracks WHERE trend_id=?', (tid,)).fetchone()
    for f in files:
        _rm((VIDEO_DIR / f))
        _rm((VIDEO_DIR / f).with_suffix('.credits.txt'))
    for f in sheets:
        _rm((VIDEO_DIR / f))
    if voice:
        _rm((VOICE_DIR / voice['filename']))
