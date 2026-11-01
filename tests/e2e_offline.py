"""Offline end-to-end test through the real HTTP API.

What is REAL here: FastAPI app, SQLite, job queue, TTS audio (espeak-ng, a real Vietnamese
speech synthesiser — robotic, used only because Edge/gTTS are unreachable in CI), photo
selection/scoring/licence filtering, Ken Burns renderer, FFmpeg encode, output QA.
What is SIMULATED: news article (fixture text), photo search results (local real photos
with honest metadata incl. distractors), LLM (unavailable -> heuristic entity fallback).

Usage:  python tests/e2e_offline.py [workdir]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
FIX = HERE / 'fixtures' / 'space'

NARRATION_GATE_FAIL = (
    'Từ kính Hubble đến tên lửa Falcon 9, đây là bốn cột mốc giúp con người nhìn xa hơn vào vũ trụ. '
    'Kính viễn vọng Hubble được tàu con thoi Discovery đưa lên quỹ đạo năm 1990 và đã hoạt động hơn ba thập kỷ. '
    'Năm 1995, ảnh Hubble Deep Field được ghép từ 342 lần phơi sáng và cho thấy khoảng 3.000 thiên hà. '
    'Cũng năm 1995, phi hành gia Eileen Collins trở thành người phụ nữ đầu tiên điều khiển tàu con thoi của NASA. '
    'Năm 1999, Eileen Collins là nữ chỉ huy đầu tiên của một chuyến bay tàu con thoi. '
    'Mặt Trăng cách Trái Đất trung bình khoảng 384.400 km. '
    'Năm 1969, sứ mệnh Apollo 11 lần đầu đưa con người đặt chân lên Mặt Trăng. '
    'Năm 2015, tên lửa Falcon 9 của SpaceX phóng vệ tinh DSCOVR. '
    'DSCOVR theo dõi gió Mặt Trời và chụp ảnh toàn bộ mặt được chiếu sáng của Trái Đất từ khoảng cách khoảng 1,5 triệu km.')
# Same story without the Discovery-launch sentence (the offline photo set has no photo of it).
NARRATION = NARRATION_GATE_FAIL.replace(
    'Kính viễn vọng Hubble được tàu con thoi Discovery đưa lên quỹ đạo năm 1990 và đã hoạt động hơn ba thập kỷ. ', '')


def main(workdir: Path):
    workdir.mkdir(parents=True, exist_ok=True)
    for f in ('app.py', 'index.html') + tuple(p.name for p in APP.glob('tf_*.py')):
        shutil.copy(APP / f, workdir / f)
    os.environ.update({'TRENDFLOW_DB': str(workdir / 'trendflow.db'), 'TRENDFLOW_FIXTURE_DIR': str(FIX),
                       'MIN_IMAGE_SIDE': '380', 'WEB_MIN_IMAGE_SIDE': '380', 'AUTO_SCAN': 'false', 'OLLAMA_URL': 'http://127.0.0.1:9'})
    sys.path.insert(0, str(workdir))
    os.chdir(workdir)
    from fastapi.testclient import TestClient
    import app as appmod
    import tf_db
    from tf_db import connect, now_iso
    client = TestClient(appmod.app)
    tf_db.set_settings({'image_source': 'search', 'tts_provider': 'espeak', 'final_quality': os.getenv('E2E_FINAL_QUALITY', '1080p')})
    with connect() as db:
        db.execute("INSERT INTO sources(id,category,name,url,language,market) VALUES(900,'Công nghệ','Fixture','fixture://src','vi','VN')")
        db.execute('''INSERT INTO trends(id,source_id,category,title,summary,link,published,first_seen)
                      VALUES(9001,900,'Công nghệ','Bốn cột mốc khám phá vũ trụ (dữ liệu thử)','Tổng hợp','https://example.org/test-article',?,?)''',
                   (now_iso(), now_iso()))
        db.execute('INSERT INTO article_context(trend_id,fetched_at,article_url,title,body) VALUES(9001,?,?,?,?)',
                   (now_iso(), 'https://example.org/test-article', 'Bốn cột mốc khám phá vũ trụ', (FIX / 'article.txt').read_text()))
    import tf_jobs
    results = {}

    def save(narr):
        tf_jobs.save_project(9001, {'title': 'Bốn cột mốc khám phá vũ trụ', 'headline': 'Bốn cột mốc nhìn xa vào vũ trụ',
                                    'narration': narr, 'caption': 'Dữ liệu kiểm thử.', 'hashtags': '#test',
                                    'verification': ''}, 52)

    def wait(tid, timeout=900):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = client.get(f'/api/projects/{tid}').json()['job']
            if j and j['status'] in ('done', 'error', 'cancelled'):
                return j
            time.sleep(1)
        raise TimeoutError

    # ---- Scenario A: not enough distinct real photos -> must NOT render, must explain.
    save(NARRATION_GATE_FAIL)
    assert client.post('/api/projects/9001/preview').status_code == 200
    job = wait(9001)
    proj = client.get('/api/projects/9001').json()
    results['A_job'] = {k: job[k] for k in ('status', 'error')}
    results['A_no_video'] = proj['preview'] is None
    results['A_approve_refused'] = client.post('/api/projects/9001/approve').status_code == 409
    # ---- Scenario B: balanced story -> full pipeline.
    r = client.put('/api/projects/9001', json={'title': 'Bốn cột mốc khám phá vũ trụ', 'headline': 'Bốn cột mốc nhìn xa vào vũ trụ',
                                               'narration': NARRATION, 'caption': 'Dữ liệu kiểm thử.', 'hashtags': '#test'})
    results['B_save_status'] = r.status_code
    # Approval must be refused before a preview for the new text exists.
    r = client.post('/api/projects/9001/approve')
    results['approve_before_preview_refused'] = r.status_code == 409
    t0 = time.time()
    assert client.post('/api/projects/9001/preview').status_code == 200
    job = wait(9001)
    results['preview_job'] = {k: job[k] for k in ('status', 'step', 'error')}
    results['preview_seconds'] = round(time.time() - t0, 1)
    proj = client.get('/api/projects/9001').json()
    results['gates'] = proj['gates']
    results['plan_report'] = {k: proj['plan']['report'][k] for k in ('passed', 'issues', 'warnings', 'distinct_photos',
                                                                       'sentence_matched', 'segments', 'max_photo_share',
                                                                       'rejected_examples', 'candidates')} if proj['plan'] else None
    results['sentences'] = [{'i': s['index'] + 1, 'text': s['text'][:70], 'start': s.get('start'), 'end': s.get('end'),
                             'shots': [(sh['media_id'], sh['match'], sh['hits']) for sh in s.get('shots', [])]}
                            for s in proj['project']['sentences']]
    with connect() as db:
        results['media'] = {r['id']: r['title'] for r in db.execute('SELECT id,title FROM media_cache')}
    results['preview_qa'] = proj['preview']['qa'] if proj['preview'] else None
    r = client.post('/api/projects/9001/approve')
    results['approve'] = (r.status_code, r.json())
    # Editing after approval is blocked.
    r = client.put('/api/projects/9001', json={'title': 'x', 'narration': NARRATION})
    results['edit_after_approve_blocked'] = r.status_code == 409
    r = client.post('/api/pending/start')
    results['batch_start'] = r.json()
    t0 = time.time()
    job = wait(9001, 1800)
    results['final_job'] = {k: job[k] for k in ('status', 'step', 'error')}
    results['final_seconds'] = round(time.time() - t0, 1)
    proj = client.get('/api/projects/9001').json()
    results['stage_after'] = proj['stage']
    results['final_qa'] = proj['final']['qa'] if proj['final'] else None
    # ---- Scenario C: photos ONLY from the original article + another outlet's article on the same event.
    tf_db.set_settings({'image_source': 'article'})
    with connect() as db:
        for tid, link, title, src in ((9003, 'https://vnexpress.net/test-bon-cot-moc-vu-tru', 'Bốn cột mốc khám phá vũ trụ', 900),
                                      (9004, 'https://tuoitre.vn/test-cot-moc-vu-tru', 'Bốn cột mốc khám phá vũ trụ của nhân loại', 901),
                                      (9005, 'https://thanhnien.vn/test-netflix-pubg', 'Netflix làm phim PUBG', 902)):
            db.execute("INSERT OR IGNORE INTO sources(id,category,name,url,language,market) VALUES(?,?,?,?,?,?)",
                       (src, 'Công nghệ', f'src{src}', f'fixture://src{src}', 'vi', 'VN'))
            db.execute('INSERT INTO trends(id,source_id,category,title,summary,link,published,first_seen) VALUES(?,?,?,?,?,?,?,?)',
                       (tid, src, 'Công nghệ', title, 'x', link, now_iso(), now_iso()))
        db.execute('INSERT INTO article_context(trend_id,fetched_at,article_url,title,body) VALUES(9003,?,?,?,?)',
                   (now_iso(), 'https://vnexpress.net/test-bon-cot-moc-vu-tru', 'Bốn cột mốc', (FIX / 'article.txt').read_text()))
    tf_jobs.save_project(9003, {'title': 'Bốn cột mốc (ảnh bài báo)', 'headline': 'Bốn cột mốc nhìn xa vào vũ trụ',
                                'narration': NARRATION, 'caption': '', 'hashtags': '', 'verification': ''}, 52)
    assert client.post('/api/projects/9003/preview').status_code == 200
    job = wait(9003)
    pc = client.get('/api/projects/9003').json()
    results['C_job'] = {k: job[k] for k in ('status', 'step', 'error')}
    results['C_sources'] = [(x['outlet'], x['url']) for x in pc['plan']['report'].get('sources', [])]
    results['C_sentences'] = [(s['index'] + 1, s['text'][:45], [(sh['credit'], sh['match']) for sh in s.get('shots', [])])
                              for s in pc['project']['sentences']]
    with connect() as db:
        plan = json.loads(db.execute('SELECT plan_json FROM scene_plans WHERE trend_id=9003').fetchone()[0])
    results['C_media'] = {k: (m['provider'], m['local_filename']) for k, m in plan['media'].items()}
    results['C_qa'] = pc['preview']['qa'] if pc['preview'] else None
    results['C_rejected'] = pc['plan']['report'].get('rejected_examples')
    results['C_counts'] = {k: pc['plan']['report'].get(k) for k in ('verified_a', 'verified_b', 'candidates', 'issues', 'warnings')}
    results['C_gates'] = pc['gates']
    pr3 = tf_jobs.latest_render(9003, 'preview')
    results['C_files'] = {'preview': str(tf_jobs.VIDEO_DIR / pr3['filename']) if pr3 else None,
                          'sheet': str(tf_jobs.VIDEO_DIR / pr3['sheet_filename']) if pr3 else None}
    fr = tf_jobs.latest_render(9001, 'final')
    pr = tf_jobs.latest_render(9001, 'preview')
    results['files'] = {'final': str(tf_jobs.VIDEO_DIR / fr['filename']) if fr else None,
                        'final_sheet': str(tf_jobs.VIDEO_DIR / fr['sheet_filename']) if fr else None,
                        'preview': str(tf_jobs.VIDEO_DIR / pr['filename']) if pr else None,
                        'credits': str((tf_jobs.VIDEO_DIR / fr['filename']).with_suffix('.credits.txt')) if fr else None}
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return results


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix='tf_e2e_')))
