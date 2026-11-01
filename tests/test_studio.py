"""Visual Studio: segmentation, photo picking (search / upload), plan → real render → QA, approval freshness."""
import base64
import io
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

APP = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / 'fixtures' / 'space'
sys.path.insert(0, str(APP))
from tests.test_jobs import env, wait  # noqa: E402,F401  (shared fixture: temp DB, fixtures, espeak voice)


def test_auto_groups_are_contiguous_balanced_and_5_to_10():
    import tf_studio
    for durs in ([4.5] * 12, [2, 9, 3, 3, 8, 2, 2, 6, 5, 4, 3], [6] * 30):
        g = tf_studio.auto_groups(durs)
        assert 5 <= len(g) <= 10
        assert g[0][0] == 0 and g[-1][1] == len(durs) - 1
        assert all(b[0] == a[1] + 1 for a, b in zip(g, g[1:]))
    assert len(tf_studio.auto_groups([5, 5, 5])) == 3          # never more segments than sentences
    assert len(tf_studio.auto_groups([4.5] * 12, count=7)) == 7


@pytest.fixture()
def api(env):
    import app as appmod
    import tf_jobs
    from fastapi.testclient import TestClient
    import tf_db
    from tests.e2e_offline import NARRATION
    tf_db.set_settings({'visual_mode': 'manual'})
    tf_jobs.save_project(1, {'title': 'Vũ trụ', 'headline': 'Bốn cột mốc', 'narration': NARRATION, 'caption': '',
                             'hashtags': '', 'verification': ''}, 52)
    return TestClient(appmod.app)


def _png(w=800, h=1000, color=(200, 40, 40)):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def test_studio_segments_search_pick_upload_merge_split(api):
    d = api.get('/api/projects/1').json()
    assert d['visual_mode'] == 'manual'
    st = d['studio']
    assert 5 <= len(st['segments']) <= 10 and st['approx'] and not st['complete']
    assert not any(i['ok'] for i in d['gates']['items'] if i['key'] == 'photos')
    s0 = st['segments'][0]
    assert s0['keywords']                                          # heuristic keywords without any AI

    r = api.get('/api/projects/1/studio/search', params={'source': 'commons', 'q': 'Hubble'}).json()
    hubble = next(c for c in r['items'] if 'Deep Field' in c['title'])
    assert api.get(hubble['thumb']).headers['content-type'] == 'image/jpeg'
    st = api.post(f"/api/projects/1/studio/segments/{s0['id']}/add", json={'candidate': hubble}).json()
    pick = st['segments'][0]['picks'][0]
    assert pick['credit_text'].startswith('Ảnh:') and api.get(pick['thumb']).status_code == 200

    st = api.post(f"/api/projects/1/studio/segments/{s0['id']}/upload",
                  json={'data_url': _png(), 'filename': 'mine.png', 'credit': 'Harvey'}).json()
    assert [p['credit_text'] for p in st['segments'][0]['picks']][1] == 'Ảnh: Harvey'
    assert api.post(f"/api/projects/1/studio/segments/{s0['id']}/upload",
                    json={'data_url': _png(100, 100), 'filename': 'tiny.png'}).status_code == 400   # too small

    # focus + reorder persist
    picks = st['segments'][0]['picks']
    body = {'picks': [dict(picks[1], focus=[0.2, 0.3]), picks[0]]}
    st = api.put(f"/api/projects/1/studio/segments/{s0['id']}", json=body).json()
    assert st['segments'][0]['picks'][0]['focus'] == [0.2, 0.3] and st['segments'][0]['picks'][1]['media_id'] == picks[0]['media_id']

    n = len(st['segments'])
    st = api.post(f"/api/projects/1/studio/segments/{s0['id']}/merge").json()
    assert len(st['segments']) == n - 1 and len(st['segments'][0]['picks']) == 2
    st = api.post(f"/api/projects/1/studio/segments/{s0['id']}/split").json()
    assert len(st['segments']) == n
    assert st['segments'][0]['last'] + 1 == st['segments'][1]['first']

    # bad source / missing key are clear errors, not crashes
    assert 'Pexels' in api.get('/api/projects/1/studio/search', params={'source': 'pexels', 'q': 'moon'}).json()['detail']


def _fill_all(api):
    st = api.get('/api/projects/1/studio').json()
    pool = []
    for q in ('Hubble', 'Moon', 'Falcon', 'Eileen Collins', 'cat', 'camera'):
        pool += api.get('/api/projects/1/studio/search', params={'source': 'openverse', 'q': q}).json()['items']
    k = 0
    for seg in st['segments']:
        for _ in range(len(pool)):
            r = api.post(f"/api/projects/1/studio/segments/{seg['id']}/add", json={'candidate': pool[k % len(pool)]})
            k += 1
            if r.status_code == 200:
                st = r.json()
                break
            assert 'Ảnh nhỏ' in r.json()['detail']          # only the too-small photo may be refused
    assert st['complete']
    return st


@pytest.mark.skipif(not shutil.which('espeak-ng') or not shutil.which('ffmpeg'), reason='needs espeak-ng + ffmpeg')
def test_preview_renders_exactly_the_picked_photos_and_goes_stale_on_change(api):
    import tf_jobs
    job = wait(api.post('/api/projects/1/studio/voice').json()['job_id'])
    assert job['status'] == 'done', job
    st = api.get('/api/projects/1/studio').json()
    assert not st['approx'] and st['voice_ready']
    st = _fill_all(api)
    job = wait(api.post('/api/projects/1/preview').json()['job_id'])
    assert job['status'] == 'done', job
    sp = tf_jobs.scene_plan(1)
    assert sp['plan']['image_source'] == 'manual'
    picked = [[p['media_id'] for p in s['picks']] for s in st['segments']]
    assert [[sh['media_id'] for sh in s['shots']] for s in sp['plan']['segments']] == picked
    pr = tf_jobs.latest_render(1, 'preview')
    qa = {c['name']: c for c in pr['qa']['checks']}
    assert qa['photo_matches_sentence']['ok'], qa['photo_matches_sentence']
    assert qa['audio_is_current_voice']['ok']
    d = api.get('/api/projects/1').json()
    photos = next(i for i in d['gates']['items'] if i['key'] == 'photos')
    assert photos['ok'] and 'tự chọn' in photos['message'] and d['preview']

    # changing a pick after the preview: preview hidden, approval blocked until a new preview
    seg = st['segments'][1]
    api.put(f"/api/projects/1/studio/segments/{seg['id']}", json={'picks': [dict(seg['picks'][0], focus=[0.9, 0.1])]})
    d = api.get('/api/projects/1').json()
    assert not next(i for i in d['gates']['items'] if i['key'] == 'photos')['ok']
    assert d['preview'] is None and d['preview_stale']


def test_incomplete_selection_fails_preview_with_clear_message(api, monkeypatch):
    import tf_jobs, tf_tts
    if not shutil.which('espeak-ng'):
        pytest.skip('needs espeak-ng')
    job = wait(api.post('/api/projects/1/preview').json()['job_id'])
    assert job['status'] == 'error' and 'Đoạn chưa chọn ảnh' in job['error']


def test_auto_mode_still_available(api):
    assert api.put('/api/settings', json={'visual_mode': 'auto'}).status_code == 200
    d = api.get('/api/projects/1').json()
    assert d['visual_mode'] == 'auto' and d['studio'] is None
    assert api.put('/api/settings', json={'visual_mode': 'x'}).status_code == 400
