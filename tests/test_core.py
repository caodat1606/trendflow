"""Unit + integration tests. Run: python -m pytest -q tests  (needs ffmpeg; espeak-ng optional)."""
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

import tf_text  # noqa: E402
import tf_media  # noqa: E402
import tf_script  # noqa: E402
import tf_tts  # noqa: E402
import tf_render  # noqa: E402
import tf_llm  # noqa: E402

HAS_ESPEAK = bool(shutil.which('espeak-ng'))


# ---------------------------------------------------------------- text
def test_sentences_keep_every_word_and_respect_numbers():
    t = 'Giá vàng tăng 3,5% lên 1.200 USD. TP. HCM có mưa lớn! Ông Nguyễn Văn A nói: "Rất tốt." Kết thúc'
    s = tf_text.split_sentences(t)
    assert ' '.join(s).split() == t.split()
    assert s[0].endswith('USD.') and s[1].startswith('TP. HCM')
    segs = tf_text.scene_segments(t, min_words=3)
    assert ' '.join(segs).split() == t.split()


def test_long_sentence_split_at_comma():
    words = ('một ' * 20).strip() + ', ' + ('hai ' * 20).strip() + '.'
    segs = tf_text.scene_segments(words, max_words=30)
    assert len(segs) == 2 and segs[0].endswith(',')


def test_proper_names_skip_sentence_case_keep_acronyms():
    t = 'Kính viễn vọng Hubble bay. DSCOVR theo dõi gió. Giá dầu tăng ở Việt Nam.'
    names = tf_text.proper_names(t)
    assert 'Hubble' in names and 'DSCOVR' in names and 'Việt Nam' in names
    assert 'Kính' not in names and 'Giá' not in names


def test_numbers_normalised():
    assert tf_text.numbers_in('1.200 người, 3,5 triệu, năm 2026') == {'1200', '3.5', '2026'}


# ---------------------------------------------------------------- licences / scoring
@pytest.mark.parametrize('lic,cls', [('CC BY-SA 4.0', 'cc-by-sa'), ('CC BY 2.0', 'cc-by'), ('CC0', 'cc0'),
                                     ('Public domain', 'pd'), ('CC BY-NC 2.0', 'unknown'), ('CC BY-ND 4.0', 'unknown'),
                                     ('', 'unknown'), ('Fair use', 'unknown')])
def test_licence_classes(lic, cls):
    assert tf_media.classify_licence(lic) == cls


def test_policy():
    assert tf_media.allowed('cc-by-sa', 'open') and not tf_media.allowed('cc-by-sa', 'strict')
    assert not tf_media.allowed('publisher', 'open') and tf_media.allowed('publisher', 'editorial')
    assert not tf_media.allowed('unknown', 'editorial')


def _e(vi, en=None, typ='person'):
    return {'vi': vi, 'en': en or vi, 'type': typ, 'key': tf_text.normalize(vi)}


def test_score_matches_entity_and_rejects_logos():
    ents = [_e('Eileen Collins')]
    good = {'url': 'https://x/a.jpg', 'title': 'Eileen Collins portrait', 'description': 'NASA astronaut'}
    logo = {'url': 'https://x/b.jpg', 'title': 'Eileen Collins logo', 'description': ''}
    other = {'url': 'https://x/c.jpg', 'title': 'A cat', 'description': ''}
    assert tf_media.score(good, ents)[0] >= tf_media.SENTENCE_THRESHOLD
    assert tf_media.score(logo, ents)[0] < 0
    assert tf_media.score(other, ents)[0] == 0


def test_wikipedia_lead_image_counts_as_strong_match():
    ents = [_e('Mặt Trăng', 'Moon', 'place')]
    c = {'url': 'https://x/m.jpg', 'title': 'File 1', 'description': '', 'lead_of': ['mat trang'], 'wiki_page': 'Mặt Trăng'}
    assert tf_media.score(c, ents)[0] >= 6


def test_gate_rejects_slideshow():
    plan = {'segments': [{'index': i, 'shots': [{'media_id': 1, 'start': i * 5, 'end': i * 5 + 5, 'match': 'sentence'}]}
                         for i in range(10)], 'media': {}}
    r = tf_media.evaluate(plan, 50)
    assert not r['passed'] and any('khác nhau' in x for x in r['issues'])
    plan['segments'][3]['shots'] = []
    assert any('chưa có ảnh' in x for x in tf_media.evaluate(plan, 50)['issues'])


def test_gate_passes_balanced():
    plan = {'segments': [{'index': i, 'shots': [{'media_id': i % 5, 'start': i * 5, 'end': i * 5 + 5, 'match': 'sentence'}]}
                         for i in range(10)], 'media': {}}
    assert tf_media.evaluate(plan, 50)['passed']


def test_entity_grounding_drops_hallucinations():
    ground = tf_text.normalize('Hải Phòng mời đoàn phim quốc tế đến Tokyo')
    assert tf_media._entity({'vi': 'Hải Phòng', 'en': 'Haiphong', 'type': 'place'}, ground)
    assert tf_media._entity({'vi': 'Hà Nội', 'en': 'Hanoi', 'type': 'place'}, ground) is None


def test_safe_url_blocks_private_hosts():
    assert not tf_media.safe_url('http://127.0.0.1/x') and not tf_media.safe_url('http://localhost/x')
    assert not tf_media.safe_url('file:///etc/passwd') and not tf_media.safe_url('http://10.0.0.1/')


# ---------------------------------------------------------------- facts / llm
def test_fact_check_flags_invented_numbers():
    src = 'Năm 2026, 1.200 người tham dự liên hoan phim Tokyo.'
    assert tf_script.fact_check('Năm 2026 có 1.200 người đến Tokyo.', src)['ok']
    bad = tf_script.fact_check('Năm 2026 có 5.000 người đến Tokyo.', src)
    assert not bad['ok'] and '5000' in bad['unsupported_numbers']


def test_parse_json_variants():
    assert tf_llm.parse_json('```json\n{"a":1}\n```') == {'a': 1}
    assert tf_llm.parse_json('chú thích {"a": [1,2]} xong') == {'a': [1, 2]}
    with pytest.raises(tf_llm.LLMError):
        tf_llm.parse_json('không có json')


def test_context_window_grows_with_prompt(monkeypatch):
    sent = {}

    class R:
        status_code = 200

        def raise_for_status(self): pass

        def iter_lines(self):
            yield json.dumps({'message': {'content': '<think>x</think>ok'}, 'done': True}).encode()

        def close(self): pass

    def fake_post(url, json=None, **kw):
        sent.update(json)
        return R()
    monkeypatch.setattr(tf_llm.requests, 'post', fake_post)
    assert tf_llm.chat('x' * 20000, max_tokens=1600) == 'ok'
    assert sent['options']['num_ctx'] >= 8192 and sent['think'] is False


# ---------------------------------------------------------------- TTS
def test_align_marks_handles_grouped_words():
    segs = ['Xin chào Việt Nam.', 'Hôm nay trời đẹp.']
    marks = [{'start': 0.0, 'end': 0.4, 'text': 'Xin'}, {'start': 0.4, 'end': 0.8, 'text': 'chào'},
             {'start': 0.8, 'end': 1.6, 'text': 'Việt Nam'}, {'start': 2.0, 'end': 2.4, 'text': 'Hôm'},
             {'start': 2.4, 'end': 2.8, 'text': 'nay'}, {'start': 2.8, 'end': 3.2, 'text': 'trời'},
             {'start': 3.2, 'end': 3.6, 'text': 'đẹp'}]
    starts, words, cov = tf_tts.align_marks(segs, marks, 4.0)
    assert cov > 0.95 and starts[0] == 0.0 and abs(starts[1] - 2.0) < 0.01
    assert len(words) == 8 and words[3]['text'] == 'Nam.'


def _mp3(path, seconds=3.0):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=300:duration={seconds}',
                    '-c:a', 'libmp3lame', str(path)], check=True)


def test_edge_in_process_backup_when_cli_unavailable(monkeypatch, tmp_path):
    """If the edge-tts CLI cannot run, the same voice is requested in-process (sentence by sentence)."""
    audio = tmp_path / 'a.mp3'
    _mp3(audio, 2.0)

    class FakeCommunicate:
        def __init__(self, text, voice, rate, **kw):
            self.text = text

        async def stream(self):
            yield {'type': 'audio', 'data': audio.read_bytes()}
    import edge_tts
    monkeypatch.setattr(edge_tts, 'Communicate', FakeCommunicate)
    monkeypatch.setattr(tf_tts, 'EDGE_CLI_MODULE', 'module_that_does_not_exist')
    monkeypatch.setattr(tf_tts, 'EDGE_RETRY_DELAYS', (0,))
    r = tf_tts.synthesize(['Xin chào các bạn.', 'Đây là bản tin thử.'], tmp_path / 'v.mp3', provider='edge')
    assert r['provider'] == 'edge' and r['mode'] == 'per_segment'
    assert r['segments'][1]['start'] > r['segments'][0]['start']


@pytest.mark.skipif(not HAS_ESPEAK, reason='espeak-ng not installed')
def test_fallback_chain_and_per_sentence_timings(monkeypatch, tmp_path):
    monkeypatch.setattr(tf_tts, '_edge_stream', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('403 blocked')))
    monkeypatch.setattr(tf_tts, '_gtts', lambda text, out: tf_tts._espeak(text, out))
    monkeypatch.setattr(tf_tts, '_edge_cli', lambda *a: (_ for _ in ()).throw(RuntimeError('cli blocked')))
    monkeypatch.setattr(tf_tts, 'EDGE_RETRY_DELAYS', (0,))
    r = tf_tts.synthesize(['Câu thứ nhất khá ngắn.', 'Câu thứ hai dài hơn một chút nữa.'], tmp_path / 'v.mp3',
                          allow_fallback=True)   # only when the user explicitly allows robotic fallbacks
    assert r['provider'] == 'gtts' and 'edge' in r['fallback_reason'] and r['mode'] == 'per_segment'
    s = r['segments']
    assert s[0]['start'] == 0 and s[0]['end'] < s[1]['start'] + 0.01 and s[1]['end'] == pytest.approx(r['duration'], abs=0.01)


# ---------------------------------------------------------------- render helpers
def test_shots_merge_consecutive_same_photo():
    plan = {'segments': [{'index': 0, 'shots': [{'media_id': 1, 'start': 0, 'end': 5}]},
                         {'index': 1, 'shots': [{'media_id': 1, 'start': 5, 'end': 9}]},
                         {'index': 2, 'shots': [{'media_id': 2, 'start': 9, 'end': 14}]}]}
    shots = tf_render.flatten_shots(plan, 14.2)
    assert len(shots) == 2 and shots[0]['segs'] == [0, 1] and shots[1]['end'] == 14.2


def test_caption_groups_follow_word_times():
    timings = {'segments': [{'index': 0, 'start': 0, 'end': 4}],
               'words': [{'seg': 0, 'text': w, 'start': i * 0.5, 'end': i * 0.5 + 0.4} for i, w in
                         enumerate('Một hai ba, bốn năm sáu bảy tám.'.split())]}
    g = tf_render.caption_groups(timings)
    assert g[0]['start'] == 0 and g[1]['start'] == 1.5 and g[-1]['end'] == 4


def test_render_refuses_plan_without_photos():
    with pytest.raises(tf_render.RenderError):
        tf_render.flatten_shots({'segments': [{'index': 0, 'shots': []}]}, 10)


# ---------------------------------------------------------------- database migration (2.7 -> 3.0)
LEGACY_27 = '''
CREATE TABLE sources (id INTEGER PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE, active INTEGER DEFAULT 1, language TEXT, market TEXT);
CREATE TABLE trends (id INTEGER PRIMARY KEY, source_id INTEGER, category TEXT NOT NULL, title TEXT NOT NULL, summary TEXT, link TEXT NOT NULL UNIQUE, published TEXT, first_seen TEXT NOT NULL, score INTEGER DEFAULT 0, status TEXT DEFAULT 'Mới', script TEXT, title_vi TEXT, summary_vi TEXT, vn_score INTEGER, score_reason TEXT);
CREATE TABLE script_projects (id INTEGER PRIMARY KEY AUTOINCREMENT, trend_id INTEGER NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '', hook TEXT NOT NULL DEFAULT '', narration TEXT NOT NULL DEFAULT '', scenes_json TEXT NOT NULL DEFAULT '[]', caption TEXT NOT NULL DEFAULT '', hashtags TEXT NOT NULL DEFAULT '', verification TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Bản nháp', duration_target INTEGER NOT NULL DEFAULT 30, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE workflow_items(trend_id INTEGER PRIMARY KEY, stage TEXT NOT NULL DEFAULT 'draft', position INTEGER NOT NULL DEFAULT 0, approved_hash TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE automation_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, trend_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued', step TEXT NOT NULL DEFAULT 'x', error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT, origin TEXT NOT NULL DEFAULT 'manual', local_day TEXT);
CREATE TABLE media_assets (id INTEGER PRIMARY KEY AUTOINCREMENT, trend_id INTEGER NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL);
INSERT INTO sources VALUES (1,'Tin thế giới','VnE','https://vnexpress.net/rss/the-gioi.rss',0,'vi','VN');
INSERT INTO sources VALUES (2,'Công nghệ','My own feed','https://example.com/rss',1,'en','INTL');
INSERT INTO trends(id,source_id,category,title,link,first_seen,status,title_vi) VALUES (385,1,'Phim ảnh','Vietnam Night','https://vnexpress.net/a','2026-01-01','Đã duyệt','Đêm Việt Nam');
INSERT INTO script_projects(trend_id,title,narration,created_at,updated_at) VALUES (385,'Tiêu đề','Lời thoại cũ của người dùng','2026','2026');
INSERT INTO workflow_items VALUES (385,'pending',1,'oldhash','2026','2026');
INSERT INTO media_assets(trend_id,kind,url) VALUES (385,'image','https://x/y.jpg');
'''


def dump(path, tables):
    with sqlite3.connect(path) as db:
        return {t: db.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in tables}


def test_migration_is_non_destructive(tmp_path, monkeypatch):
    path = tmp_path / 'trendflow.db'
    with sqlite3.connect(path) as db:
        db.executescript(LEGACY_27)
    before = dump(path, ['trends', 'script_projects', 'workflow_items', 'media_assets'])
    import tf_db
    r = tf_db.migrate(path)
    assert r['backup'] and Path(r['backup']).is_file() and r['from'] == 0
    after = dump(path, ['trends', 'script_projects', 'workflow_items', 'media_assets'])
    for t in before:
        assert [row[:len(before[t][0])] for row in after[t]] == before[t], t
    with sqlite3.connect(path) as db:
        src = db.execute('SELECT active, market FROM sources WHERE id IN (1,2) ORDER BY id').fetchall()
        assert src == [(0, 'VN'), (1, 'INTL')]  # user's disabled feed stays disabled
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='scene_plans'").fetchone()[0] == 1
    assert dump(Path(r['backup']), ['script_projects'])['script_projects'] == before['script_projects']
    assert tf_db.migrate(path)['backup'] is None  # second start: no new backup


# ---------------------------------------------------------------- API workflow
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TRENDFLOW_DB', str(tmp_path / 't.db'))
    monkeypatch.setenv('AUTO_SCAN', 'false')
    monkeypatch.setenv('OLLAMA_URL', 'http://127.0.0.1:9')
    for m in ('tf_db', 'tf_news', 'tf_media', 'tf_article', 'tf_imagesearch', 'tf_verify', 'tf_jobs', 'app'):
        sys.modules.pop(m, None)
    import tf_db
    importlib.reload(tf_db)
    import app as appmod
    from fastapi.testclient import TestClient
    from tf_db import connect
    with connect() as db:
        for tid in (1, 2, 3):
            db.execute("INSERT INTO trends(id,category,title,link,first_seen) VALUES(?,?,?,?,?)",
                       (tid, 'Công nghệ', f'Tin {tid}', f'https://e/{tid}', '2026'))
            db.execute("INSERT INTO script_projects(trend_id,title,hook,narration,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (tid, f'Dự án {tid}', 'h', ' '.join(['từ'] * 60) + '.', '2026', '2026'))
            db.execute("INSERT INTO workflow_items(trend_id,stage,position,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (tid, 'pending' if tid < 3 else 'draft', tid, '2026', '2026'))
    return TestClient(appmod.app)


def test_approve_requires_gates(client):
    r = client.post('/api/projects/3/approve')
    assert r.status_code == 409 and 'Chưa đủ điều kiện' in r.json()['detail']


def test_reorder_validates_and_persists(client):
    assert client.put('/api/pending/order', json={'ids': [1]}).status_code == 409
    assert client.put('/api/pending/order', json={'ids': [2, 1]}).status_code == 200
    assert [x['trend_id'] for x in client.get('/api/projects?stage=pending').json()['items']] == [2, 1]


def test_remove_pending_returns_to_draft_and_delete(client):
    assert client.delete('/api/pending/1').status_code == 200
    assert {x['trend_id'] for x in client.get('/api/projects?stage=draft').json()['items']} == {1, 3}
    assert client.delete('/api/projects/3').status_code == 200
    assert client.get('/api/projects/3').json()['project'] is None
    from tf_db import connect
    with connect() as db:
        assert db.execute('SELECT COUNT(*) FROM trends WHERE id=3').fetchone()[0] == 1  # article kept


def test_edit_blocked_outside_draft(client):
    r = client.put('/api/projects/1', json={'title': 'x', 'narration': ' '.join(['a'] * 30)})
    assert r.status_code == 409


def test_batch_skips_unapproved_content_and_cancel_queued(client):
    r = client.post('/api/pending/start').json()
    assert r['queued'] == 0 and len(r['skipped']) == 2  # approved_hash missing -> never produced
    import tf_jobs
    jid = tf_jobs.enqueue(3, 'preview')
    assert tf_jobs.cancel(jid) in ('cancelled', 'cancel_requested')


def test_spa_routes_serve_index(client):
    for p in ('/discover', '/scripts', '/scripts/1', '/pending', '/completed', '/settings'):
        assert client.get(p).status_code == 200


# ---------------------------------------------------------------- v3.0.1 regressions (video 76)
def test_secondary_name_alone_is_not_enough():
    ents = [_e('PUBG', 'PUBG', 'name'), _e('Guinness', 'Guinness World Records', 'org')]
    wally = {'url': 'https://x/w.jpg', 'title': 'Guinness world record Wally Dublin', 'description': ''}
    pubg = {'url': 'https://x/p.jpg', 'title': 'PUBG Global Invitational stage', 'description': ''}
    assert tf_media.score(wally, ents)[0] < tf_media.SENTENCE_THRESHOLD
    assert tf_media.score(pubg, ents)[0] >= tf_media.SENTENCE_THRESHOLD


def test_illustration_score_and_label():
    c = {'title': 'Esports tournament arena in Seoul', 'description': 'crowd watching a game'}
    assert tf_media.illustration_score(c, 'esports tournament arena') == 4
    assert tf_media.illustration_score({'title': 'Cat', 'description': ''}, 'esports tournament arena') == 0
    row = {'id': 1, 'url': 'u', 'provider': 'Wikimedia Commons', 'creator': 'A', 'license': 'CC BY 4.0', 'rights_class': 'cc-by'}
    assert tf_media._media_entry(row, {}, illustration=True)['credit'].startswith('Ảnh minh hoạ')


def test_speakable_dates_and_percent():
    assert tf_tts.speakable('công bố ngày 30-10 tại Amsterdam') == 'công bố ngày 30 tháng 10 tại Amsterdam'
    assert tf_tts.speakable('tăng 15%') == 'tăng 15 phần trăm'


@pytest.mark.skipif(not HAS_ESPEAK, reason='espeak-ng not installed')
def test_padded_fallback_audio_is_trimmed(monkeypatch, tmp_path):
    """gTTS adds ~0.4 s silence on both ends; 2.x left 0.7–1.2 s gaps between sentences (video 76)."""
    def padded(text, out):
        raw = out.with_suffix('.raw.wav')
        tf_tts._espeak(text, raw)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(raw), '-af', 'adelay=500:all=1,apad=pad_dur=0.5',
                        '-c:a', 'libmp3lame', str(out)], check=True)
    monkeypatch.setattr(tf_tts, '_gtts', padded)
    r = tf_tts.synthesize(['Câu thứ nhất của bản tin.', 'Câu thứ hai tiếp theo.', 'Câu thứ ba kết thúc.'],
                          tmp_path / 'v.mp3', provider='gtts')
    import tf_qa
    gaps = [g for g in tf_qa.audio_stats(tmp_path / 'v.mp3')['silences']]
    assert tf_qa.pause_total(tmp_path / 'v.mp3') < 1.2, gaps


# ---------------------------------------------------------------- 3.1: article photos only
def test_related_articles_same_event_other_outlet_only(client):
    import tf_article
    from tf_db import connect
    rows = [(11, 'Netflix làm phim từ game PUBG, Donald Glover đóng chính', 'https://vnexpress.net/a'),
            (12, 'Donald Glover đóng chính phim PUBG của Netflix và Krafton', 'https://tuoitre.vn/b'),
            (13, 'Netflix làm phim PUBG, Donald Glover đóng chính (bài 2)', 'https://vnexpress.net/c'),
            (14, 'BLACKPINK tổ chức concert ở Hà Nội', 'https://thanhnien.vn/d')]
    with connect() as db:
        for tid, title, link in rows:
            db.execute("INSERT INTO trends(id,category,title,link,first_seen) VALUES(?,?,?,?,?)",
                       (tid, 'Phim ảnh', title, link, '2026-10-31T10:00:00+00:00'))
    rel = tf_article.related_articles(11)
    assert [r['trend_id'] for r in rel] == [12]   # same outlet (13) and unrelated story (14) excluded


def test_article_credit_comes_from_article_not_cache():
    import tf_article
    media = {}
    c = {'page_url': 'https://tuoitre.vn/b', 'provider': 'Tuổi Trẻ', 'title': 't', 'creator': 'Tuổi Trẻ',
         'license': 'Ảnh của Tuổi Trẻ', 'rights_class': 'publisher', 'description': 'chú thích'}
    row = {'id': 5, 'url': 'u', 'local_filename': 'f.jpg', 'provider': 'Wikimedia Commons', 'rights_class': 'cc-by'}
    tf_article._shot(c, row, 'caption', 5, [], media)
    assert media['5']['credit'] == 'Ảnh: Tuổi Trẻ' and media['5']['provider'] == 'Tuổi Trẻ'


def test_article_gate_refuses_no_photos_and_single_photo():
    import tf_article
    empty = {'media': {}, 'segments': [{'index': 0, 'shots': []}]}
    assert not tf_article.evaluate(empty, 50)['passed']
    one = {'media': {'1': {}}, 'segments': [{'index': i, 'shots': [{'media_id': 1, 'start': i * 10, 'end': i * 10 + 10, 'match': 'article'}]} for i in range(5)]}
    assert not tf_article.evaluate(one, 50)['passed']


def test_proper_names_split_at_comma():
    assert tf_text.proper_names('Netflix làm phim từ game PUBG, Donald Glover đóng chính.') == ['PUBG', 'Donald Glover'] or \
        set(tf_text.proper_names('Netflix làm phim từ game PUBG, Donald Glover đóng chính.')) >= {'PUBG', 'Donald Glover'}


# ---------------------------------------------------------------- 3.1.1
def test_parse_bing_rss_extracts_real_article_url():
    import tf_article
    xml = b'''<?xml version="1.0"?><rss><channel><item><title>Netflix lam phim PUBG voi Donald Glover</title>
    <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;tid=1&amp;url=https%3a%2f%2fvnexpress.net%2fnetflix-pubg-123.html&amp;c=1</link>
    <pubDate>Fri, 31 Oct 2026 08:00:00 GMT</pubDate></item></channel></rss>'''
    r = tf_article.parse_bing_rss(xml)
    assert r[0]['url'] == 'https://vnexpress.net/netflix-pubg-123.html' and r[0]['outlet'] == 'VnExpress'


def test_same_event_requires_shared_names():
    import tf_article
    assert tf_article.same_event('Netflix làm phim tựa game PUBG, Donald Glover đóng chính',
                                 'Donald Glover to star in Netflix PUBG movie')
    assert not tf_article.same_event('Netflix làm phim tựa game PUBG, Donald Glover đóng chính',
                                     'BLACKPINK tổ chức concert ở Hà Nội')


def test_head_requests_allowed_for_media(client):
    assert client.head('/api/projects/1/video/preview').status_code in (404,)  # route accepts HEAD (no video yet)


# ---------------------------------------------------------------- 3.2: voice + foreign-subject photos
def test_edge_uses_cli_subprocess_and_no_gtts_by_default(monkeypatch, tmp_path):
    """Edge is called like its CLI (separate process). Without permission, never fall back to gTTS."""
    fake = tmp_path / 'fake_edge_cli.py'
    fake.write_text('import sys,subprocess\nargs=sys.argv\nout=args[args.index("--write-media")+1]\n'
                    'subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","sine=frequency=220:duration=1.5","-c:a","libmp3lame",out],check=True)\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setattr(tf_tts, 'EDGE_CLI_MODULE', 'fake_edge_cli')
    monkeypatch.setattr(tf_tts, 'EDGE_RETRY_DELAYS', (0,))
    r = tf_tts.synthesize(['Câu một.', 'Câu hai.'], tmp_path / 'v.mp3', provider='auto')
    assert r['provider'] == 'edge' and r['mode'] == 'per_segment'
    monkeypatch.setattr(tf_tts, 'EDGE_CLI_MODULE', 'module_that_does_not_exist')
    monkeypatch.setattr(tf_tts, '_edge_stream', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('NoAudioReceived')))
    called = []
    monkeypatch.setattr(tf_tts, '_gtts', lambda *a: called.append(1))
    with pytest.raises(tf_tts.TTSFailure) as e:
        tf_tts.synthesize(['Câu một.'], tmp_path / 'w.mp3', provider='auto')
    assert not called and 'Azure' in str(e.value)


def test_azure_engine_order_and_request(monkeypatch, tmp_path):
    assert tf_tts.engine_order('auto', {'key': 'k', 'region': 'southeastasia'}) == ['edge', 'azure']
    assert tf_tts.engine_order('auto') == ['edge']
    sent = {}
    audio = tmp_path / 'a.mp3'
    _mp3(audio, 1.5)

    class R:
        status_code = 200
        content = audio.read_bytes()
        text = ''
    import requests
    monkeypatch.setattr(requests, 'post', lambda url, data=None, headers=None, timeout=None: sent.update(url=url, data=data, h=headers) or R())
    tf_tts._azure('Xin chào & tạm biệt', tmp_path / 'o.mp3', 'vi-VN-NamMinhNeural', 0, {'key': 'K', 'region': 'southeastasia'})
    assert sent['url'].startswith('https://southeastasia.tts.speech.microsoft.com')
    assert b'vi-VN-NamMinhNeural' in sent['data'] and b'&amp;' in sent['data'] and sent['h']['Ocp-Apim-Subscription-Key'] == 'K'


def test_gemini_tts_request_and_wav(monkeypatch, tmp_path):
    import base64, wave, requests
    pcm = (b'\x00\x10' * 24000)  # 1 s of 24 kHz 16-bit audio
    calls = []

    class R:
        def __init__(self, code, payload=None, text=''):
            self.status_code, self._p, self.text = code, payload, text

        def json(self):
            return self._p

    def fake_post(url, params=None, json=None, timeout=None):
        calls.append(url)
        if 'gemini-3.1' in url:
            return R(404, text='not found')       # unknown model -> next one
        return R(200, {'candidates': [{'content': {'parts': [{'inlineData': {
            'mimeType': 'audio/L16;codec=pcm;rate=24000', 'data': base64.b64encode(pcm).decode()}}]}}]})
    monkeypatch.setattr(requests, 'post', fake_post)
    tf_tts._GEMINI_MODEL_OK.clear()
    out = tmp_path / 'g.wav'
    tf_tts._gemini('Xin chào.', out, {'key': 'K', 'voice': 'Charon', 'model': ''})
    with wave.open(str(out)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 24000
    assert 'gemini-2.5-flash-preview-tts' in calls[-1] and len(calls) == 2
    # Gemini is a different voice: never used by 'auto', only when chosen explicitly.
    assert tf_tts.engine_order('auto', None, False, {'key': 'K'}) == ['edge']
    assert tf_tts.engine_order('gemini', None, False, {'key': 'K'}) == ['gemini']



# ---------------------------------------------------------------- 3.2.2: Edge reliability & stale audio
def _fake_cli_module(tmp_path, body):
    (tmp_path / 'fake_cli_mod.py').write_text(body)
    return 'fake_cli_mod'


def test_edge_sentence_retries_and_logs_every_attempt(monkeypatch, tmp_path):
    audio = tmp_path / 'a.mp3'
    _mp3(audio, 1.5)
    calls = {'n': 0}

    def flaky_stream(text, out, voice, rate, cancel=None, words=True):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('NoAudioReceived')        # first try fails, like the user's app runs
        out.write_bytes(audio.read_bytes())
    monkeypatch.setattr(tf_tts, '_edge_stream', flaky_stream)
    monkeypatch.setattr(tf_tts, '_edge_cli', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('cli exit 1')))
    monkeypatch.setattr(tf_tts, 'EDGE_RETRY_DELAYS', (0, 0, 0))
    r = tf_tts.synthesize(['Câu một.', 'Câu hai.'], tmp_path / 'v.mp3', provider='edge')
    log = r['attempt_log']
    assert r['provider'] == 'edge'
    assert [(a['sentence'], a['round'], a['how'], a['ok']) for a in log] == [
        (1, 1, 'stream', False), (1, 1, 'cli', False), (1, 2, 'stream', True), (2, 1, 'stream', True)]
    assert 'NoAudioReceived' in log[0]['error'] and 'cli exit 1' in log[1]['error']


def test_edge_failure_is_not_sticky(monkeypatch, tmp_path):
    audio = tmp_path / 'a.mp3'
    _mp3(audio, 1.5)
    state = {'down': True}

    def stream(text, out, voice, rate, cancel=None, words=True):
        if state['down']:
            raise RuntimeError('NoAudioReceived')
        out.write_bytes(audio.read_bytes())
    monkeypatch.setattr(tf_tts, '_edge_stream', stream)
    monkeypatch.setattr(tf_tts, '_edge_cli', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down')))
    monkeypatch.setattr(tf_tts, 'EDGE_RETRY_DELAYS', (0,))
    with pytest.raises(tf_tts.TTSFailure) as e:
        tf_tts.synthesize(['Câu một.'], tmp_path / 'v.mp3', azure={'key': 'k', 'region': 'x'} and None)
    assert e.value.attempt_log and 'Edge không đọc được câu 1' in str(e.value)
    state['down'] = False                                  # Edge works again -> next job uses it immediately
    assert tf_tts.synthesize(['Câu một.'], tmp_path / 'w.mp3')['provider'] == 'edge'


def test_cancel_during_retry_wait_is_fast(monkeypatch, tmp_path):
    class Stop(Exception):
        pass
    t0 = __import__('time').monotonic()
    deadline = t0 + 0.5

    def cancel():
        if __import__('time').monotonic() > deadline:
            raise Stop()
    with pytest.raises(Stop):
        tf_tts._sleep(10, cancel)
    assert __import__('time').monotonic() - t0 < 1.5


def test_cancel_kills_running_edge_cli(monkeypatch, tmp_path):
    mod = _fake_cli_module(tmp_path, 'import time\ntime.sleep(30)\n')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setattr(tf_tts, 'EDGE_CLI_MODULE', mod)
    t0 = __import__('time').monotonic()

    class Stop(Exception):
        pass

    def cancel():
        if __import__('time').monotonic() - t0 > 0.8:
            raise Stop()
    with pytest.raises(Stop):
        tf_tts._edge_cli('Xin chào.', tmp_path / 'o.mp3', 'vi-VN-NamMinhNeural', 0, cancel)
    assert __import__('time').monotonic() - t0 < 5


def test_voice_is_current_rejects_old_fallback_audio(tmp_path):
    import tf_jobs
    mp3 = tmp_path / 'v.mp3'
    mp3.write_bytes(b'x' * 3000)
    settings = {'tts_provider': 'auto', 'voice_id': 'vi-VN-NamMinhNeural', 'allow_fallback_voice': False}
    project = {'narration': 'Câu một. Câu hai.'}
    base = {'narration_hash': tf_text.narration_hash(project['narration']), 'voice_id': 'vi-VN-NamMinhNeural',
            'path': mp3, 'filename': 'v.mp3'}
    old_gtts = dict(base, provider='gtts', timings={})                    # made before 3.2.2 while Edge failed
    ok, why = tf_jobs.voice_is_current(old_gtts, project, settings)
    assert not ok and 'gTTS' in why
    edge_now = dict(base, provider='edge', timings={'config': tf_tts.config_signature(settings)})
    assert tf_jobs.voice_is_current(edge_now, project, settings)[0]
    changed = dict(settings, tts_provider='azure')
    assert not tf_jobs.voice_is_current(edge_now, project, changed)[0]


def test_stale_audio_and_video_are_hidden_not_shown_as_current(client):
    """Old gTTS audio + its video must not be presented as the current result (3.2.2)."""
    from tf_db import connect, set_settings
    set_settings({'tts_provider': 'auto', 'allow_fallback_voice': False})
    with connect() as db:
        db.execute("UPDATE workflow_items SET stage='draft' WHERE trend_id=3")
        nh = tf_text.narration_hash(db.execute('SELECT narration FROM script_projects WHERE trend_id=3').fetchone()[0])
        db.execute("INSERT INTO voice_tracks VALUES(3,'3_old.mp3',?,'gtts','vi-VN-NamMinhNeural',50,'{\"segments\":[],\"words\":[]}','2026')", (nh,))
        db.execute("INSERT INTO renders(trend_id,kind,filename,plan_hash,duration,width,height,passed,qa_json,created_at) "
                   "VALUES(3,'preview','3_preview_old.mp4','h',50,720,1280,1,'{}','2026')")
    d = client.get('/api/projects/3').json()
    assert d['voice']['fresh'] is False and d['voice']['url'] is None and 'gTTS' in d['voice']['stale_reason']
    assert d['preview'] is None and d['preview_stale'] and 'gTTS' in d['preview_stale']['reason']
    assert not next(i for i in d['gates']['items'] if i['key'] == 'voice')['ok']


# ---------------------------------------------------------------- 3.3: AI-verified photos (A = article, B = web search)
def test_parse_bing_images_html():
    import tf_imagesearch
    html_text = ('<a class="iusc" style="x" m="{&quot;murl&quot;:&quot;https://cdn.example.com/pubg.jpg&quot;,'
                 '&quot;purl&quot;:&quot;https://www.gamesite.vn/pubg-netflix&quot;,&quot;t&quot;:&quot;PUBG movie&quot;}" href="#">')
    r = tf_imagesearch.parse_bing(html_text)
    assert r == [{'url': 'https://cdn.example.com/pubg.jpg', 'page_url': 'https://www.gamesite.vn/pubg-netflix',
                  'title': 'PUBG movie', 'outlet': 'gamesite.vn'}]


def test_parse_serpapi():
    import tf_imagesearch
    r = tf_imagesearch.parse_serpapi({'images_results': [{'original': 'https://a.com/x.jpg', 'link': 'https://a.com/p',
                                                           'title': 'Donald Glover', 'source': 'a.com'}, {'original': 'data:x'}]})
    assert len(r) == 1 and r[0]['outlet'] == 'a.com'


def test_verify_verdict_threshold_and_sentence_bounds():
    import tf_verify
    v = tf_verify._normalise({'relevant': True, 'score': 5, 'sentences': [1, 99, '2']}, 3, 'x')
    assert v['relevant'] is False and v['sentences'] == [1, 2]          # score below 6 -> rejected
    assert tf_verify._normalise({'relevant': True, 'score': 8}, 3, 'x')['relevant']
    assert not tf_verify._normalise({'relevant': 'yes', 'score': 9}, 3, 'x')['relevant']   # must be literal true


def test_no_vision_ai_means_clear_error_not_unverified_photos(monkeypatch):
    import tf_verify
    monkeypatch.delenv('TRENDFLOW_FIXTURE_DIR', raising=False)
    monkeypatch.setattr(tf_verify.tf_llm, 'status', lambda: {'vision_model': None})
    with pytest.raises(tf_verify.VerifyUnavailable) as e:
        tf_verify.backend({'image_verifier': 'auto', 'gemini_key': ''})
    assert 'qwen2.5vl' in str(e.value) and 'Gemini' in str(e.value)
    assert tf_verify.backend({'image_verifier': 'auto', 'gemini_key': 'K'})[0] == 'gemini'


def test_gate_rejects_unverified_photo():
    import tf_article
    plan = {'media': {str(i): {'verdict': {'relevant': True}} for i in range(3)},
            'segments': [{'index': i, 'shots': [{'media_id': i % 3, 'start': i * 10, 'end': i * 10 + 10, 'match': 'ai'}]} for i in range(5)]}
    assert tf_article.evaluate(plan, 50)['passed']
    plan['media']['2'] = {'verdict': {'relevant': False}}
    assert not tf_article.evaluate(plan, 50)['passed']


def test_gemini_vision_timeout_is_retried_not_fatal(monkeypatch):
    import requests, tf_verify
    calls = []

    class R:
        status_code = 200
        text = ''

        def json(self):
            return {'candidates': [{'content': {'parts': [{'text': '{"relevant": true, "score": 8, "sentences": [1]}'}]}}]}

    def post(url, params=None, json=None, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise requests.exceptions.ReadTimeout('Read timed out. (read timeout=60)')   # what the user hit
        return R()
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr(tf_verify, 'GEMINI_RETRY_DELAYS', (0, 0))
    monkeypatch.setattr(tf_verify, 'GEMINI_MIN_INTERVAL', 0)
    tf_verify._OK_MODEL.clear()
    assert tf_verify._gemini(b'x', 'q', 'K')['score'] == 8 and len(calls) == 2

    monkeypatch.setattr(requests, 'post', lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout('slow')))
    tf_verify._OK_MODEL['m'] = 'gemini-2.5-flash'
    with pytest.raises(tf_verify.VerifyTransient):      # transient: caller skips this photo, job continues
        tf_verify._gemini(b'x', 'q', 'K')



def test_gemini_batch_one_request_for_many_photos(monkeypatch, tmp_path):
    import requests, tf_verify, json as _j
    from PIL import Image
    paths = []
    for i in range(3):
        p = tmp_path / f'{i}.jpg'
        Image.new('RGB', (64, 64), (i * 80, 10, 10)).save(p)
        paths.append(p)
    calls = []

    class R:
        status_code = 200
        text = ''

        def json(self):
            return {'candidates': [{'content': {'parts': [{'text': _j.dumps({'images': [
                {'image': 2, 'relevant': True, 'score': 9, 'shows': 'PUBG', 'sentences': [1]},
                {'image': 1, 'relevant': False, 'score': 1, 'shows': 'Spider-Man', 'reason': 'phim khác'}]})}]}}]}

    def post(url, params=None, json=None, timeout=None):
        calls.append(len([p for p in json['contents'][0]['parts'] if 'inline_data' in p]))
        return R()
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr(tf_verify, 'GEMINI_MIN_INTERVAL', 0)
    v = tf_verify.verify_many(paths, title='t', summary='s', sentences=['a', 'b'], hints=['h'] * 3,
                              how=('gemini', {'key': 'K'}))
    assert calls == [3]                                   # ONE request carrying 3 photos
    assert [x['relevant'] for x in v] == [False, True, False] and 'không trả kết quả' in v[2]['reason']


def test_gemini_daily_quota_stops_fast_and_retry_delay_is_respected(monkeypatch):
    import requests, tf_verify
    waits = []
    monkeypatch.setattr(tf_verify, '_wait', lambda s, cancel=None: waits.append(round(s)))
    monkeypatch.setattr(tf_verify, 'GEMINI_MIN_INTERVAL', 0)
    tf_verify._OK_MODEL['m'] = 'gemini-2.5-flash'

    class R429:
        status_code = 429
        text = 'quota'

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body
    seq = [R429({'error': {'details': [{'retryDelay': '23s'}]}}),
           R429({'error': {'details': [{'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}})]
    monkeypatch.setattr(requests, 'post', lambda *a, **k: seq.pop(0))
    with pytest.raises(tf_verify.VerifyUnavailable) as e:
        tf_verify._gemini(b'x', 'q', 'K')
    assert 24 in waits and 'trong ngày' in str(e.value)


# ---------------------------------------------------------------- 3.3.3: verifier choice & failover
def test_vision_model_detected_by_family():
    assert tf_llm.find_vision_model(['qwen3:8b', 'qwen2.5vl:latest']) == 'qwen2.5vl:latest'
    assert tf_llm.find_vision_model(['qwen3:8b', 'qwen2.5vl:7b']) == 'qwen2.5vl:7b'
    assert tf_llm.find_vision_model(['qwen3:8b', 'gemma3:1b']) is None
    assert tf_llm.find_vision_model(['qwen3:8b']) is None


def test_backends_order_and_explicit_choice(monkeypatch):
    import tf_verify
    monkeypatch.delenv('TRENDFLOW_FIXTURE_DIR', raising=False)
    monkeypatch.setattr(tf_verify.tf_llm, 'status', lambda max_age=0: {'vision_model': 'qwen2.5vl:7b'})
    tf_verify._GEMINI_EXHAUSTED['until'] = 0
    assert [b[0] for b in tf_verify.backends({'image_verifier': 'auto', 'gemini_key': 'K'})] == ['gemini', 'ollama']
    assert [b[0] for b in tf_verify.backends({'image_verifier': 'ollama', 'gemini_key': 'K'})] == ['ollama']
    tf_verify._GEMINI_EXHAUSTED['until'] = 10 ** 12          # quota hit earlier today -> auto skips Gemini
    assert [b[0] for b in tf_verify.backends({'image_verifier': 'auto', 'gemini_key': 'K'})] == ['ollama']
    tf_verify._GEMINI_EXHAUSTED['until'] = 0
