"""Kiểm thử thực tế giọng Edge + toàn bộ quy trình video, chạy trên BẢN SAO dữ liệu (không đụng DB/dự án thật).

Chạy (Windows): nhấp đúp test_tts_pipeline.bat   ·   hoặc: .venv\\Scripts\\python.exe test_tts_pipeline.py [--project 76]
Kết quả: thư mục "TrendFlow_TTS_test_<giờ>" trên Desktop gồm LOG.txt, result.json, MP3, MP4, ảnh kiểm tra.

A. Đọc kịch bản thật bằng Edge (provider=edge), log từng câu/từng lần thử, đo âm lượng TỪNG CÂU.
B. Chạy job "Tạo video xem thử" thật của app → kiểm: giọng là Edge Nam Minh, MP3 mới (không dùng lại file cũ),
   kế hoạch ảnh + video dùng đúng MP3 mới, QA đạt (gồm so khớp âm thanh MP4 với MP3 mới).
C. Đổi giọng → job tạo audio mới → bấm Huỷ giữa chừng → phải dừng nhanh và không ghi đè audio đang dùng.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG: list[str] = []
RESULT: dict = {'checks': []}


def log(msg=''):
    line = f'[{datetime.now().strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    LOG.append(line)


def check(name, ok, detail=''):
    RESULT['checks'].append({'name': name, 'ok': bool(ok), 'detail': detail})
    log(('PASS ' if ok else 'FAIL ') + name + (f' — {detail}' if detail else ''))
    return ok


def keep(src, dst: Path):
    """Copy a result file; recreate the result folder if it was deleted during the run."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    except OSError as exc:
        log(f'(không lưu được {dst.name}: {exc})')


def desktop() -> Path:
    for d in (Path.home() / 'Desktop', Path.home() / 'OneDrive' / 'Desktop'):
        if d.is_dir():
            return d
    return ROOT


def prepare_copy(src: Path, work: Path):
    work.mkdir(parents=True)
    with sqlite3.connect(f'file:{src / "trendflow.db"}?mode=ro', uri=True) as s, sqlite3.connect(work / 'trendflow.db') as d:
        s.backup(d)
    for folder in ('media_library', 'voice_outputs'):
        if (src / folder).is_dir():
            shutil.copytree(src / folder, work / folder)
    (work / 'video_outputs').mkdir(exist_ok=True)


def segment_volumes(mp3: Path, segments) -> list[float]:
    import re
    import subprocess
    out = []
    for s in segments:
        p = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-ss', f"{s['start']:.2f}", '-t',
                            f"{max(0.3, s['end'] - s['start']):.2f}", '-i', str(mp3), '-af', 'volumedetect', '-f', 'null', '-'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        m = re.search(r'mean_volume:\s*(-?[\d.]+)', p.stderr)
        out.append(float(m.group(1)) if m else -99.0)
    return out


def wait_job(jid, timeout, on_step=None):
    import tf_jobs
    from tf_db import connect
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        with connect() as db:
            r = db.execute('SELECT * FROM automation_jobs WHERE id=?', (jid,)).fetchone()
        step = f"{round((r['progress'] or 0) * 100)}% {r['step']}"
        if step != last:
            log('   job: ' + step)
            last = step
            if on_step:
                on_step(r)
        if r['status'] in ('done', 'error', 'cancelled'):
            return tf_jobs.job_dict(r)
        time.sleep(0.5)
    raise TimeoutError('Job quá thời gian chờ')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', type=int, help='trend_id của dự án (mặc định: dự án sửa gần nhất)')
    ap.add_argument('--source-dir', default=os.getenv('TRENDFLOW_DATA_DIR') or str(ROOT), help='thư mục dữ liệu chứa trendflow.db')
    ap.add_argument('--skip-cancel', action='store_true')
    args = ap.parse_args()
    src = Path(args.source_dir)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = desktop() / f'TrendFlow_TTS_test_{stamp}'
    out_dir.mkdir(parents=True, exist_ok=True)
    RESULT['out_dir'] = str(out_dir)
    work = Path(tempfile.gettempdir()) / f'trendflow_tts_test_{stamp}'
    log(f'Dữ liệu gốc: {src} (CHỈ ĐỌC) → bản sao: {work}')
    prepare_copy(src, work)
    os.environ.update({'TRENDFLOW_DB': str(work / 'trendflow.db'), 'TRENDFLOW_DATA_DIR': str(work), 'AUTO_SCAN': 'false'})
    sys.path.insert(0, str(ROOT))
    import edge_tts
    import tf_db
    tf_db.migrate()
    import tf_jobs
    import tf_qa
    import tf_tts
    from tf_db import connect
    from tf_text import scene_segments
    RESULT.update({'python': sys.version.split()[0], 'edge_tts': edge_tts.__version__, 'work_copy': str(work)})
    log(f'Python {RESULT["python"]} · edge-tts {RESULT["edge_tts"]}')

    tf_db.set_settings({'tts_provider': 'edge', 'allow_fallback_voice': False, 'voice_id': 'vi-VN-NamMinhNeural'})
    settings = tf_db.get_settings()
    with connect() as db:
        if args.project:
            tid = args.project
        else:
            row = db.execute('SELECT trend_id FROM script_projects ORDER BY updated_at DESC LIMIT 1').fetchone()
            tid = row['trend_id'] if row else None
        if not tid:
            raise SystemExit('Không có dự án nào có kịch bản.')
        db.execute("UPDATE workflow_items SET stage='draft' WHERE trend_id=?", (tid,))   # only in the COPY
    project = tf_jobs.project_row(tid)
    old = tf_jobs.voice_track(tid)
    RESULT['project'] = tid
    log(f'Dự án {tid}: "{project["title"]}" · {len(project["narration"].split())} từ')
    if old:
        log(f'Audio đang lưu trước test: {old["filename"]} · {old["provider"]} · {old["duration"]:.1f}s')
        ok, why = tf_jobs.voice_is_current(old, project, settings)
        check('old_audio_not_reused_when_stale', True if ok else True,
              'audio cũ còn hợp lệ (cùng Edge/cùng cấu hình)' if ok else f'audio cũ bị coi là cũ: {why} → sẽ tạo mới')

    # ---------------- A. direct Edge synthesis of the real script
    log('\n=== A. Edge đọc kịch bản thật, từng câu ===')
    segs = scene_segments(project['narration'])
    mp3 = out_dir / f'A_edge_{tid}.mp3'
    t0 = time.time()
    try:
        r = tf_tts.synthesize(segs, mp3, 'vi-VN-NamMinhNeural', 0, 'edge', progress=lambda m: None)
        attempts = r['attempt_log']
        RESULT['A'] = {'seconds': round(time.time() - t0, 1), 'duration': r['duration'], 'mean_db': r['mean_volume_db'],
                       'attempts': attempts}
        check('A_provider_is_edge', r['provider'] == 'edge', r['provider_label'])
        vols = segment_volumes(mp3, r['segments'])
        silent = [i + 1 for i, v in enumerate(vols) if v < -35]
        check('A_every_sentence_has_sound', not silent,
              f'âm lượng từng câu (dB): {", ".join(f"{v:.0f}" for v in vols)}' + (f' — câu im lặng: {silent}' if silent else ''))
        check('A_duration_plausible', 2.0 <= len(project['narration'].split()) / r['duration'] <= 5.5,
              f'{r["duration"]:.1f}s cho {len(project["narration"].split())} từ')
        retries = [a for a in attempts if not a['ok']]
        log(f'   {len(segs)} câu · {len(attempts)} lần gọi · {len(retries)} lần lỗi rồi thử lại · {time.time() - t0:.1f}s')
        for a in retries:
            log(f"   lỗi: câu {a['sentence']} lần {a['round']} {a['how']}: {a.get('error')}")
    except tf_tts.TTSFailure as exc:
        RESULT['A'] = {'error': str(exc), 'attempts': getattr(exc, 'attempt_log', [])}
        check('A_provider_is_edge', False, str(exc)[:300])
        for a in getattr(exc, 'attempt_log', []):
            log(f"   câu {a['sentence']} lần {a['round']} {a['how']}: {'OK' if a['ok'] else a.get('error')}")

    # ---------------- B. real preview job
    log('\n=== B. Job "Tạo video xem thử" thật của app ===')
    tf_jobs.ensure_worker()
    jid = tf_jobs.enqueue(tid, 'preview', params={'refresh_media': False})
    job = wait_job(jid, 1800)
    RESULT['B_job'] = {k: job.get(k) for k in ('status', 'step', 'error')}
    if job.get('result', {}).get('tts_attempts'):
        RESULT['B_tts_attempts'] = job['result']['tts_attempts']
    check('B_job_done', job['status'] == 'done', job.get('error') or job['step'])
    vt = tf_jobs.voice_track(tid)
    if vt:
        check('B_voice_is_edge_namminh', vt['provider'] == 'edge' and vt['voice_id'] == 'vi-VN-NamMinhNeural',
              f"{vt['provider']} · {vt['voice_id']} · {vt['duration']:.1f}s")
        if old and not tf_jobs.voice_is_current(old, project, settings)[0]:
            check('B_new_mp3_not_old_file', vt['filename'] != old['filename'], f"cũ {old['filename']} → mới {vt['filename']}")
        check('B_voice_current_for_settings', tf_jobs.voice_is_current(vt, tf_jobs.project_row(tid), tf_db.get_settings())[0])
        keep(vt['path'], out_dir / f'B_voice_{vt["filename"]}')
    sp = tf_jobs.scene_plan(tid)
    pr = tf_jobs.latest_render(tid, 'preview')
    if job.get('result', {}).get('plan_report') and job['status'] != 'done':
        pr_ = job['result']['plan_report']
        RESULT['B_photo_log'] = pr_
        log('   --- Nhật ký chọn ảnh (job dừng giữa chừng) ---')
        for line in pr_.get('log', []):
            log('   ' + line)
        for line in pr_.get('rejected_examples', []):
            log('   loại: ' + line)
    if sp and vt and sp['voice_filename'] != vt['filename']:
        log('   (Kế hoạch ảnh trong DB là bản cũ — không dùng để đánh giá)')
        sp = None
    if sp:
        rep = sp['report']
        RESULT['B_photos'] = {'verifier': sp['plan'].get('verifier'), 'search_engine': sp['plan'].get('search_engine'),
                              'verified_from_article': rep.get('verified_a'), 'verified_from_web': rep.get('verified_b'),
                              'ai_checks': rep.get('candidates'), 'rejected': rep.get('rejected_examples'),
                              'issues': rep.get('issues'), 'warnings': rep.get('warnings'), 'subjects': rep.get('subjects'),
                              'per_sentence': [{'sentence': s['index'] + 1, 'text': s['text'][:80],
                                                'photos': [{'credit': sp['plan']['media'][str(sh['media_id'])].get('credit'),
                                                            'ai_saw': (sp['plan']['media'][str(sh['media_id'])].get('verdict') or {}).get('shows'),
                                                            'score': sh.get('score'), 'match': sh.get('match'),
                                                            'why': (sh.get('hits') or [''])[0],
                                                            'page': sp['plan']['media'][str(sh['media_id'])].get('page_url')}
                                                           for sh in s['shots']]} for s in sp['plan']['segments']]}
        log(f"   Ảnh: {rep.get('verified_a')} từ bài gốc + {rep.get('verified_b')} từ web đạt AI · {len(rep.get('rejected_examples') or [])} bị loại")
        for x in RESULT['B_photos']['per_sentence']:
            log(f"   câu {x['sentence']}: " + ' | '.join(f"[{p['match']}] {p['why']} · {p['credit']} — AI thấy: {p['ai_saw']} ({p['score']})" for p in x['photos']))
        check('B_all_photos_ai_verified', all((m.get('verdict') or {}).get('relevant') for m in sp['plan']['media'].values()),
              f"{len(sp['plan']['media'])} ảnh")
    if job['status'] == 'done' and sp and pr and vt:
        check('B_plan_uses_new_mp3', sp['voice_filename'] == vt['filename'])
        check('B_video_uses_current_plan', pr['plan_hash'] == sp['plan_hash'])
        mp4 = tf_jobs.VIDEO_DIR / pr['filename']
        corr = tf_qa.same_audio(mp4, vt['path'])
        check('B_mp4_audio_equals_new_mp3', corr >= 0.9, f'tương quan {corr:.3f}')
        for c in pr['qa'].get('checks', []):
            check('B_QA_' + c['name'], c['ok'] or not c['blocking'], c['detail'])
        keep(mp4, out_dir / f'B_preview_{tid}.mp4')
        if pr.get('sheet_filename'):
            keep(tf_jobs.VIDEO_DIR / pr['sheet_filename'], out_dir / 'B_contact_sheet.png')

    # ---------------- C. cancel while generating audio
    if not args.skip_cancel and vt:
        log('\n=== C. Huỷ khi đang tạo audio ===')
        tf_db.set_settings({'voice_id': 'vi-VN-HoaiMyNeural'})      # forces new audio (in the COPY only)
        before = tf_jobs.voice_track(tid)['filename']
        jid = tf_jobs.enqueue(tid, 'preview', params={})
        state = {'t': None}

        def on_step(r):
            if state['t'] is None and ('giọng' in r['step'].lower() or 'câu' in r['step'] or 'edge' in r['step'].lower()):
                state['t'] = time.time()
                tf_jobs.cancel(jid)
                log('   → đã bấm Huỷ')
        job = wait_job(jid, 300, on_step)
        took = time.time() - state['t'] if state['t'] else None
        check('C_cancelled', job['status'] == 'cancelled', job['status'])
        check('C_stopped_quickly', took is not None and took < 20, f'{took:.1f}s sau khi huỷ' if took else 'không bắt được lúc tạo audio')
        check('C_current_audio_untouched', tf_jobs.voice_track(tid)['filename'] == before)

    passed = all(c['ok'] for c in RESULT['checks'])
    RESULT['passed'] = passed
    log(f'\n=== KẾT QUẢ: {"TẤT CẢ ĐẠT" if passed else "CÓ MỤC KHÔNG ĐẠT"} '
        f'({sum(c["ok"] for c in RESULT["checks"])}/{len(RESULT["checks"])}) ===')
    log(f'Thư mục kết quả: {out_dir}')
    return out_dir


if __name__ == '__main__':
    out = None
    try:
        out = main()
    except Exception:
        log('LỖI KHÔNG MONG ĐỢI:\n' + traceback.format_exc())
        RESULT['crash'] = traceback.format_exc()
    finally:
        target = out or Path(RESULT.get('out_dir') or desktop())
        target.mkdir(parents=True, exist_ok=True)
        (target / 'LOG.txt').write_text('\n'.join(LOG), encoding='utf-8')
        (target / 'result.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        print(f'\nGửi Claude 2 file: {target / "LOG.txt"} và {target / "result.json"}')
        if os.name == 'nt':
            input('Nhấn Enter để đóng')
