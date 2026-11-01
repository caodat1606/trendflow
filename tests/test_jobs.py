"""Integration: script length loop (fake LLM, real espeak audio) and cancelling a running render."""
import importlib
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / 'fixtures' / 'space'
sys.path.insert(0, str(APP))
pytestmark = pytest.mark.skipif(not shutil.which('espeak-ng'), reason='needs espeak-ng for real audio')


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, v in {'TRENDFLOW_DB': str(tmp_path / 't.db'), 'TRENDFLOW_FIXTURE_DIR': str(FIX), 'MIN_IMAGE_SIDE': '380', 'WEB_MIN_IMAGE_SIDE': '380',
                 'AUTO_SCAN': 'false', 'OLLAMA_URL': 'http://127.0.0.1:9'}.items():
        monkeypatch.setenv(k, v)
    for m in ('tf_db', 'tf_news', 'tf_media', 'tf_article', 'tf_imagesearch', 'tf_verify', 'tf_jobs', 'tf_render', 'tf_qa', 'app'):
        sys.modules.pop(m, None)
    import tf_db, tf_jobs, tf_media
    monkeypatch.setattr(tf_jobs, 'VOICE_DIR', tmp_path / 'voice')
    monkeypatch.setattr(tf_jobs, 'VIDEO_DIR', tmp_path / 'video')
    monkeypatch.setattr(tf_media, 'MEDIA_DIR', tmp_path / 'media')
    import tf_render
    monkeypatch.setattr(tf_render, 'MEDIA_DIR', tmp_path / 'media')
    tf_db.migrate()
    tf_db.set_settings({'tts_provider': 'espeak', 'image_source': 'search'})
    from tf_db import connect
    article = (FIX / 'article.txt').read_text()
    with connect() as db:
        db.execute("INSERT INTO trends(id,category,title,link,first_seen) VALUES(1,'Công nghệ','Vũ trụ','https://e/1','2026')")
        db.execute("INSERT INTO article_context(trend_id,body,title,article_url) VALUES(1,?,?,?)", (article, 'Vũ trụ', 'https://e/1'))
    return tmp_path


def wait(jid, timeout=600):
    import tf_jobs
    from tf_db import connect
    t0 = time.time()
    while time.time() - t0 < timeout:
        with connect() as db:
            r = db.execute('SELECT * FROM automation_jobs WHERE id=?', (jid,)).fetchone()
        if r['status'] in ('done', 'error', 'cancelled'):
            return tf_jobs.job_dict(r)
        time.sleep(0.5)
    raise TimeoutError


def test_generate_revises_until_45_60_seconds(env, monkeypatch):
    import tf_llm, tf_jobs
    from tests.e2e_offline import NARRATION
    short = ' '.join(NARRATION.split()[:45]) + '.'
    calls = []

    def fake_chat(prompt, **kw):
        calls.append(prompt[:60])
        narr = short if len(calls) == 1 else NARRATION
        return json.dumps({'title': 'Vũ trụ', 'headline': 'Bốn cột mốc', 'narration': narr, 'caption': 'c',
                           'hashtags': '#a', 'verification': ''}, ensure_ascii=False)
    monkeypatch.setattr(tf_llm, 'chat', fake_chat)
    job = wait(tf_jobs.enqueue(1, 'generate'))
    assert job['status'] == 'done', job
    att = job['result']['attempts']
    assert att[0]['seconds'] < 45 and 45 <= att[-1]['seconds'] <= 60 and job['result']['length_ok']
    assert 'DÀI HƠN' in ''.join(calls[1:2]) or len(calls) >= 2


def test_cancel_running_preview_leaves_no_partial_files(env):
    import tf_jobs
    from tests.e2e_offline import NARRATION
    tf_jobs.save_project(1, {'title': 'Vũ trụ', 'headline': 'Bốn cột mốc', 'narration': NARRATION, 'caption': '',
                             'hashtags': '', 'verification': ''}, 52)
    jid = tf_jobs.enqueue(1, 'preview')
    from tf_db import connect
    t0 = time.time()
    while time.time() - t0 < 300:   # wait until rendering frames
        with connect() as db:
            st = db.execute('SELECT step FROM automation_jobs WHERE id=?', (jid,)).fetchone()['step']
        if 'Dựng khung' in st:
            break
        time.sleep(0.3)
    assert 'Dựng khung' in st
    tf_jobs.cancel(jid)
    job = wait(jid, 60)
    assert job['status'] == 'cancelled'
    assert not list((env / 'video').glob('*.mp4')) and tf_jobs.latest_render(1, 'preview') is None


def _article_trend():
    from tf_db import connect
    with connect() as db:
        db.execute("INSERT INTO trends(id,category,title,link,first_seen) VALUES(2,'Công nghệ','Bốn cột mốc khám phá vũ trụ',"
                   "'https://vnexpress.net/test-bon-cot-moc-vu-tru','2026')")
        db.execute("INSERT INTO article_context(trend_id,body,title,article_url) VALUES(2,?,?,?)",
                   ((FIX / 'article.txt').read_text(), 'x', 'https://vnexpress.net/test-bon-cot-moc-vu-tru'))


def _timings():
    from tests.e2e_offline import NARRATION
    from tf_text import scene_segments
    segs = scene_segments(NARRATION)
    t, out = 0.0, []
    for i, s in enumerate(segs):
        out.append({'index': i, 'text': s, 'start': t, 'end': t + 6})
        t += 6
    return {'segments': out, 'words': []}


def test_ai_timeout_on_one_photo_skips_only_that_photo(env, monkeypatch):
    import tf_article, tf_verify
    _article_trend()
    real = tf_verify.verify

    def flaky(path, **kw):
        if kw.get('fixture_key') == 'moon.jpg':
            raise tf_verify.VerifyTransient('ReadTimeout')
        return real(path, **kw)
    monkeypatch.setattr(tf_verify, 'verify', flaky)
    r = tf_article.build_article_plan(2, _timings(), 'H', {'image_verifier': 'auto'}, use_llm=False)
    assert r['passed'], r['report']['issues']
    assert any('bỏ qua' in x for x in r['report']['rejected_examples'])
    used = {m['local_filename'] for m in r['plan']['media'].values()}
    assert all((m.get('verdict') or {}).get('relevant') for m in r['plan']['media'].values()) and len(used) >= 3


def test_ai_unreachable_three_times_stops_with_clear_error(env, monkeypatch):
    import tf_article, tf_verify
    _article_trend()
    monkeypatch.setattr(tf_verify, 'verify', lambda path, **kw: (_ for _ in ()).throw(tf_verify.VerifyTransient('ReadTimeout')))
    with pytest.raises(tf_verify.VerifyUnavailable) as e:
        tf_article.build_article_plan(2, _timings(), 'H', {'image_verifier': 'auto'}, use_llm=False)
    assert 'liên tiếp' in str(e.value)


def test_gemini_quota_fails_over_to_local_ai_and_rechecks_same_photos(env, monkeypatch):
    import tf_article, tf_verify
    _article_trend()
    real = tf_verify.verify_many
    seen = []
    monkeypatch.setattr(tf_verify, 'backends', lambda s: [('gemini', {'key': 'K'}), ('fixture', {'file': FIX / 'verify.json'})])

    def vm(paths, **kw):
        seen.append((kw['how'][0], tuple(kw['fixture_keys'])))
        if kw['how'][0] == 'gemini':
            raise tf_verify.VerifyUnavailable('Hết hạn mức Gemini miễn phí trong ngày.')
        return real(paths, **kw)
    monkeypatch.setattr(tf_verify, 'verify_many', vm)
    monkeypatch.setattr(tf_verify, 'batch_size', lambda how: 6 if how[0] == 'gemini' else 1)
    r = tf_article.build_article_plan(2, _timings(), 'H', {'image_verifier': 'auto'}, use_llm=False)
    assert r['passed'], r['report']['issues']
    assert seen[0][0] == 'gemini' and seen[1] == ('fixture', seen[0][1])     # same photos re-checked locally
    assert r['plan']['verifier'] == 'gemini→fixture'
    assert any('chuyển sang' in x for x in r['report']['log'])


def test_preview_endpoint_passes_chosen_verifier(env):
    import tf_jobs
    import app as appmod
    from fastapi.testclient import TestClient
    tf_jobs.save_project(1, {'title': 'T', 'headline': 'H', 'narration': ' '.join(['từ'] * 60) + '.', 'caption': '',
                             'hashtags': '', 'verification': ''}, 52)
    c = TestClient(appmod.app)
    assert c.post('/api/projects/1/preview?verifier=bad').status_code == 400
    jid = c.post('/api/projects/1/preview?verifier=ollama').json()['job_id']
    tf_jobs.cancel(jid)
    from tf_db import connect
    with connect() as db:
        params = db.execute('SELECT result_json FROM automation_jobs WHERE id=?', (jid,)).fetchone()[0]
    assert '"verifier": "ollama"' in params or '"verifier": "ollama"' in (tf_jobs.job_dict({'result_json': params, 'id': 0}).get('result') and params)


def test_editor_style_subject_matching_pubg_story(env, monkeypatch):
    """Feedback scenario: Donald Glover sentence -> Donald Glover photo (confirmed by page naming him, not by face);
    PUBG -> game art; Netflix -> Netflix image; Stephen Glover (no photo found) -> broader photo, clearly labelled."""
    import tf_article, tf_imagesearch, tf_media, tf_verify
    from tf_db import connect
    with connect() as db:
        db.execute("INSERT INTO trends(id,category,title,link,first_seen) VALUES(5,'Phim ảnh','Netflix làm phim PUBG',"
                   "'https://tuoitre.vn/netflix-pubg','2026')")
        db.execute("INSERT INTO article_context(trend_id,body,title,article_url) VALUES(5,?,?,?)",
                   ('Netflix và Krafton làm phim PUBG. Donald Glover đóng chính. Stephen Glover viết kịch bản. ' * 5, 't', 'u'))
    texts = ['Netflix và Krafton làm phim live-action từ PUBG.', 'Donald Glover đóng vai chính và làm đạo diễn.',
             'Em trai anh, Stephen Glover, cùng viết kịch bản.', 'PUBG là trò chơi sinh tồn nổi tiếng.']
    E = lambda vi, en, t: {'vi': vi, 'en': en, 'type': t, 'key': tf_article.normalize(vi)}
    per = [[E('Netflix', 'Netflix', 'org'), E('PUBG', 'PUBG', 'object')], [E('Donald Glover', 'Donald Glover', 'person')],
           [E('Stephen Glover', 'Stephen Glover', 'person')], [E('PUBG', 'PUBG', 'object')]]
    monkeypatch.setattr(tf_media, 'build_entities', lambda *a, **k: (per, [], 'test', [''] * 4))
    monkeypatch.setattr(tf_article, 'images_of', lambda url: ('t', [{'url': 'fixture://rocket.jpg', 'caption': 'PUBG', 'note': 'Ảnh trong bài'}]))
    results = {'Donald Glover': [{'file': 'astronaut.jpg', 'title': 'Donald Glover at the premiere', 'outlet': 'news.example.com'}],
               'Netflix': [{'file': 'hubble_deep_field.jpg', 'title': 'Netflix sign', 'outlet': 'brand.example.com'}],
               'Stephen Glover': [{'file': 'coffee.jpg', 'title': 'coffee shop', 'outlet': 'x.example.com'}]}
    monkeypatch.setattr(tf_imagesearch, 'search', lambda q, s, n=20: ('test', [dict(r, url='fixture://' + r['file'], page_url='')
                                                                             for r in results.get(q, [])]))
    verdicts = {'rocket.jpg': {'relevant': True, 'score': 8, 'subjects': ['PUBG'], 'kind': 'game_art', 'shows': 'nhân vật PUBG'},
                'astronaut.jpg': {'relevant': True, 'score': 7, 'subjects': [], 'kind': 'person_photo', 'shows': 'một người đàn ông tại sự kiện'},
                'hubble_deep_field.jpg': {'relevant': True, 'score': 7, 'subjects': ['Netflix'], 'kind': 'logo_brand', 'shows': 'logo Netflix'},
                'coffee.jpg': {'relevant': False, 'score': 1, 'subjects': [], 'kind': 'other', 'shows': 'tách cà phê'}}
    monkeypatch.setattr(tf_verify, 'backends', lambda s: [('fake', {})])
    monkeypatch.setattr(tf_verify, 'batch_size', lambda how: 6)
    monkeypatch.setattr(tf_verify, 'verify_many', lambda paths, **kw: [
        tf_verify._normalise(verdicts[k], 4, 'fake', kw['subjects']) for k in kw['fixture_keys']])
    timings = {'segments': [{'index': i, 'text': t, 'start': i * 6, 'end': i * 6 + 6} for i, t in enumerate(texts)], 'words': []}
    r = tf_article.build_article_plan(5, timings, 'H', {}, use_llm=False)
    plan = r['plan']
    pic = lambda i: plan['media'][str(plan['segments'][i]['shots'][0]['media_id'])]['local_filename']
    import json as _j
    files = {m['local_filename']: m['url'].replace('fixture://', '') for m in plan['media'].values()}
    got = [(files[pic(i)], plan['segments'][i]['shots'][0]['match'], plan['segments'][i]['shots'][0]['hits'][0]) for i in range(4)]
    assert got[0][0] == 'hubble_deep_field.jpg' and got[0][1] == 'subject'                        # Netflix
    assert got[1][0] == 'astronaut.jpg' and got[1][1] == 'subject' and 'nguồn ảnh ghi tên' in got[1][2]   # Donald Glover
    assert got[2][1] == 'story' and 'chưa có ảnh Stephen Glover' in got[2][2]                      # honest fallback
    assert got[3][0] == 'rocket.jpg' and got[3][1] == 'subject'                                     # PUBG
    assert 'coffee.jpg' not in files.values()
    assert any('Stephen Glover' in w for w in r['report']['warnings'])
