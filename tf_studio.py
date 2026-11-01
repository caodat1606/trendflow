"""Manual Visual Selection Studio.

The narration is split into 5–10 timed segments (by real voice timings when the current voice exists,
otherwise by an estimate). The editor searches photos per segment from ToS-compliant sources, picks 1–3,
sets a focus point, and the render uses exactly those picks. No AI photo verification is required here.

Sources (no page scraping of search engines):
  article  – photos published in the original article and same-event articles already in TrendFlow's feed
  openverse / commons – official free APIs, openly licensed images
  pexels   – official API (free key)
  google   – SerpAPI (paid key, optional); copyright stays with the owner → editor's responsibility
  upload / url – the editor's own file or an image link they paste
"""
from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageOps

import tf_article
import tf_llm
import tf_media as M
from tf_db import connect, now_iso
from tf_text import narration_hash, normalize, proper_names, scene_segments, sha, word_count

MAX_PICKS = 3
MIN_SEGMENTS, MAX_SEGMENTS = 5, 10
SECONDS_PER_SEGMENT = 6.5
STUDIO_MIN_SIDE = 400
SCHEMA = '''CREATE TABLE IF NOT EXISTS visual_selections (
    trend_id INTEGER PRIMARY KEY, narration_hash TEXT NOT NULL, segments_json TEXT NOT NULL, updated_at TEXT NOT NULL)'''


class StudioError(RuntimeError):
    pass


# ------------------------------------------------------------------ timeline & segmentation

def timeline(narration: str, voice: dict | None, wps: float) -> tuple[list[dict], bool]:
    """Start/end per sentence. Real voice timings if `voice` (a current voice track) is given."""
    sents = scene_segments(narration)
    if voice and len(voice['timings'].get('segments', [])) == len(sents):
        return [{'index': i, 'text': s, 'start': float(t['start']), 'end': float(t['end'])}
                for i, (s, t) in enumerate(zip(sents, voice['timings']['segments']))], False
    out, t = [], 0.0
    for i, s in enumerate(sents):
        d = max(1.2, word_count(s) / max(1.5, wps))
        out.append({'index': i, 'text': s, 'start': round(t, 2), 'end': round(t + d, 2)})
        t += d
    return out, True


def auto_groups(durations: list[float], count: int | None = None) -> list[tuple[int, int]]:
    """Contiguous sentence groups with durations as even as possible (DP over squared error)."""
    n = len(durations)
    if n == 0:
        return []
    total = sum(durations)
    k = count or round(total / SECONDS_PER_SEGMENT)
    k = max(1, min(n, max(MIN_SEGMENTS, min(MAX_SEGMENTS, k))))
    target = total / k
    pre = [0.0]
    for d in durations:
        pre.append(pre[-1] + d)
    INF = float('inf')
    best = [[INF] * (n + 1) for _ in range(k + 1)]
    cut = [[0] * (n + 1) for _ in range(k + 1)]
    best[0][0] = 0.0
    for g in range(1, k + 1):
        for j in range(g, n + 1):
            for i in range(g - 1, j):
                if best[g - 1][i] == INF:
                    continue
                v = best[g - 1][i] + (pre[j] - pre[i] - target) ** 2
                if v < best[g][j]:
                    best[g][j], cut[g][j] = v, i
    groups, j = [], n
    for g in range(k, 0, -1):
        i = cut[g][j]
        groups.append((i, j - 1))
        j = i
    return groups[::-1]


def _new_segment(first, last, picks=None, keywords=''):
    return {'id': uuid.uuid4().hex[:10], 'first': first, 'last': last, 'keywords': keywords, 'picks': picks or []}


def heuristic_keywords(text: str) -> str:
    names = proper_names(text)
    if names:
        return ', '.join(names[:3])
    words = [w for w in re.findall(r'[\wÀ-ỹ]+', text) if len(w) > 3]
    return ' '.join(words[:4])


# ------------------------------------------------------------------ storage

def _ensure_table():
    with connect() as db:
        db.execute(SCHEMA)


def load(tid: int) -> dict | None:
    _ensure_table()
    with connect() as db:
        r = db.execute('SELECT * FROM visual_selections WHERE trend_id=?', (tid,)).fetchone()
    return {'narration_hash': r['narration_hash'], 'segments': json.loads(r['segments_json']),
            'updated_at': r['updated_at']} if r else None


def save(tid: int, nh: str, segments: list[dict]):
    _ensure_table()
    with connect() as db:
        db.execute('''INSERT INTO visual_selections(trend_id,narration_hash,segments_json,updated_at) VALUES(?,?,?,?)
                      ON CONFLICT(trend_id) DO UPDATE SET narration_hash=excluded.narration_hash,
                      segments_json=excluded.segments_json, updated_at=excluded.updated_at''',
                   (tid, nh, json.dumps(segments, ensure_ascii=False), now_iso()))


def delete(tid: int):
    _ensure_table()
    with connect() as db:
        db.execute('DELETE FROM visual_selections WHERE trend_id=?', (tid,))


def _text_of(seg, sents):
    return ' '.join(sents[seg['first']:seg['last'] + 1])


def ensure(tid: int, narration: str, durations: list[float], count: int | None = None, force=False) -> dict:
    """Selection for the CURRENT narration. When the text changed, segments are rebuilt and the picks of
    any segment whose text is unchanged are kept (nothing is silently attached to different words)."""
    nh = narration_hash(narration)
    sel = load(tid)
    if sel and sel['narration_hash'] == nh and not force:
        return sel
    sents = scene_segments(narration)
    old = {normalize(s['text']): s for s in (sel or {}).get('segments', []) if s.get('text')}
    same_text = bool(sel and sel['narration_hash'] == nh)
    used, segs = set(), []
    for first, last in auto_groups(durations, count):
        text = ' '.join(sents[first:last + 1])
        prev = old.get(normalize(text))
        if not prev and same_text:   # regrouping the same narration: inherit from the old segment that starts it
            prev = next((s for s in sel['segments'] if s['first'] <= first <= s['last'] and s['id'] not in used), None)
        if prev:
            used.add(prev['id'])
        segs.append(_new_segment(first, last, (prev or {}).get('picks', []), (prev or {}).get('keywords') or heuristic_keywords(text)))
        segs[-1]['text'] = text
    save(tid, nh, segs)
    return load(tid)


def selection_hash(sel: dict | None) -> str | None:
    if not sel:
        return None
    core = [[s['first'], s['last'], [(p['media_id'], tuple(p.get('focus') or ()), p.get('credit') or '') for p in s['picks']]]
            for s in sel['segments']]
    return sha(json.dumps([sel['narration_hash'], core]))


def media_rows(ids) -> dict:
    ids = sorted({int(i) for i in ids})
    if not ids:
        return {}
    with connect() as db:
        rows = db.execute(f'SELECT * FROM media_cache WHERE id IN ({",".join("?" * len(ids))})', ids).fetchall()
    return {r['id']: dict(r) for r in rows}


# ------------------------------------------------------------------ editing

def _find(sel, seg_id):
    for k, s in enumerate(sel['segments']):
        if s['id'] == seg_id:
            return k, s
    raise StudioError('Không tìm thấy đoạn này (có thể lời thoại vừa đổi). Tải lại trang.')


def _store(tid, sel, sents):
    for s in sel['segments']:
        s['text'] = _text_of(s, sents)
    save(tid, sel['narration_hash'], sel['segments'])
    return load(tid)


def update_segment(tid, sel, sents, seg_id, keywords=None, picks=None):
    _, seg = _find(sel, seg_id)
    if keywords is not None:
        seg['keywords'] = str(keywords)[:200]
    if picks is not None:
        rows = media_rows(p['media_id'] for p in picks)
        clean = []
        for p in picks[:MAX_PICKS]:
            if int(p['media_id']) not in rows:
                raise StudioError('Ảnh không còn trong thư viện.')
            f = p.get('focus')
            focus = [round(max(0.0, min(1.0, float(f[0]))), 3), round(max(0.0, min(1.0, float(f[1]))), 3)] if f else None
            clean.append({'media_id': int(p['media_id']), 'focus': focus, 'credit': (p.get('credit') or '').strip()[:160] or None})
        seg['picks'] = clean
    return _store(tid, sel, sents)


def add_pick(tid, sel, sents, seg_id, row: dict, credit=None):
    _, seg = _find(sel, seg_id)
    if any(p['media_id'] == row['id'] for p in seg['picks']):
        return load(tid)
    if len(seg['picks']) >= MAX_PICKS:
        raise StudioError(f'Mỗi đoạn tối đa {MAX_PICKS} ảnh. Bỏ bớt một ảnh trước.')
    seg['picks'].append({'media_id': row['id'], 'focus': None, 'credit': (credit or '').strip()[:160] or None})
    return _store(tid, sel, sents)


def merge(tid, sel, sents, seg_id):
    k, seg = _find(sel, seg_id)
    if k + 1 >= len(sel['segments']):
        raise StudioError('Đây là đoạn cuối, không có đoạn sau để gộp.')
    nxt = sel['segments'][k + 1]
    seg['last'] = nxt['last']
    seen = {p['media_id'] for p in seg['picks']}
    seg['picks'] = (seg['picks'] + [p for p in nxt['picks'] if p['media_id'] not in seen])[:MAX_PICKS]
    seg['keywords'] = seg['keywords'] or nxt['keywords']
    del sel['segments'][k + 1]
    return _store(tid, sel, sents)


def split(tid, sel, sents, seg_id):
    k, seg = _find(sel, seg_id)
    if seg['first'] == seg['last']:
        raise StudioError('Đoạn chỉ có 1 câu — không tách được.')
    mid = (seg['first'] + seg['last'] + 1) // 2
    second = _new_segment(mid, seg['last'], seg['picks'][1:], heuristic_keywords(' '.join(sents[mid:seg['last'] + 1])))
    seg['last'] = mid - 1
    seg['picks'] = seg['picks'][:1]
    sel['segments'].insert(k + 1, second)
    return _store(tid, sel, sents)


# ------------------------------------------------------------------ AI keyword suggestions (optional)

def suggest_keywords(segments_text: list[str], title: str, cancel=None) -> list[str]:
    """Ollama suggests a short image-search query per segment. Falls back to names in the text."""
    fallback = [heuristic_keywords(t) for t in segments_text]
    try:
        if not tf_llm.status(max_age=30).get('online'):
            return fallback
        prompt = ('Tin: ' + title + '\nVới mỗi đoạn lời thoại dưới đây, viết 1 cụm từ khoá NGẮN (2–5 từ) để tìm ẢNH THẬT '
                  'minh hoạ đúng nội dung đoạn đó: ưu tiên tên người/tổ chức/địa điểm/sự vật cụ thể; dùng tiếng Anh nếu chủ thể '
                  'là quốc tế. Trả JSON {"keywords": ["...", ...]} đúng ' + str(len(segments_text)) + ' phần tử.\n\n'
                  + '\n'.join(f'{i + 1}. {t}' for i, t in enumerate(segments_text)))
        data = tf_llm.chat_json(prompt, max_tokens=400, cancel=cancel, timeout=120)
        kws = [str(x).strip()[:80] for x in (data.get('keywords') or [])]
        if len(kws) != len(segments_text):
            return fallback
        return [k or f for k, f in zip(kws, fallback)]
    except Exception:
        return fallback


# ------------------------------------------------------------------ search sources

_ARTICLE_CACHE: dict[int, tuple[float, list[dict]]] = {}
_THUMBS: OrderedDict = OrderedDict()
_THUMB_LOCK = threading.Lock()


def sources(settings: dict) -> list[dict]:
    has = lambda k: bool((settings.get(k) or '').strip())
    return [
        {'key': 'article', 'label': 'Bài gốc + báo cùng sự kiện', 'ok': True, 'note': 'Ảnh của tòa soạn — credit tên báo'},
        {'key': 'openverse', 'label': 'Openverse (CC)', 'ok': True, 'note': 'Ảnh giấy phép mở'},
        {'key': 'commons', 'label': 'Wikimedia Commons', 'ok': True, 'note': 'Ảnh giấy phép mở'},
        {'key': 'pexels', 'label': 'Pexels', 'ok': has('pexels_key'), 'note': 'Cần Pexels API key (miễn phí)'},
        {'key': 'google', 'label': 'Google Images (SerpAPI)', 'ok': has('serpapi_key'),
         'note': 'Cần SerpAPI key · ảnh có bản quyền — m tự chịu trách nhiệm'},
    ]


def _article_candidates(tid: int) -> list[dict]:
    hit = _ARTICLE_CACHE.get(tid)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    with connect() as db:
        t = db.execute('SELECT link FROM trends WHERE id=?', (tid,)).fetchone()
        ctx = db.execute('SELECT article_url FROM article_context WHERE trend_id=?', (tid,)).fetchone()
    urls = []
    for u in ([t['link']] if t else []) + ([ctx['article_url']] if ctx and ctx['article_url'] else []):
        if u and u not in urls:
            urls.append(u)
    urls += [r['url'] for r in tf_article.related_articles(tid, limit=4) if r['url'] not in urls]
    out, seen = [], set()
    for k, url in enumerate(urls):
        try:
            title, images = tf_article.images_of(url)
        except Exception:
            continue
        for im in images:
            if im['url'] in seen:
                continue
            seen.add(im['url'])
            out.append({'url': im['url'], 'page_url': url, 'provider': tf_article.outlet(url), 'title': title,
                        'description': im.get('caption') or '', 'creator': '', 'license': '',
                        'rights_class': 'publisher', 'primary': k == 0})
    _ARTICLE_CACHE[tid] = (time.time(), out)
    return out


def _pexels(q: str, key: str) -> list[dict]:
    import requests
    r = requests.get('https://api.pexels.com/v1/search', params={'query': q[:100], 'per_page': 24},
                     headers={'Authorization': key, 'User-Agent': M.USER_AGENT}, timeout=15)
    if r.status_code in (401, 403):
        raise StudioError('Pexels API key không hợp lệ.')
    r.raise_for_status()
    out = []
    for p in r.json().get('photos', []):
        src = p.get('src') or {}
        out.append({'url': src.get('large2x') or src.get('original'), 'page_url': p.get('url') or '', 'provider': 'Pexels',
                    'title': p.get('alt') or '', 'description': p.get('alt') or '', 'creator': p.get('photographer') or '',
                    'license': 'Pexels License', 'rights_class': 'free', 'width': p.get('width'), 'height': p.get('height'),
                    'thumb_src': src.get('medium')})
    return out


def _google(q: str, key: str) -> list[dict]:
    import tf_imagesearch
    return [{'url': r['url'], 'page_url': r.get('page_url') or '', 'provider': r.get('outlet') or '', 'title': r.get('title') or '',
             'description': r.get('title') or '', 'creator': '', 'license': 'Bản quyền thuộc chủ sở hữu', 'rights_class': 'web'}
            for r in tf_imagesearch.serpapi_images(q, key, 30)]


def search(tid: int, source: str, q: str, settings: dict) -> list[dict]:
    try:
        return _search(tid, source, q, settings)
    except StudioError:
        raise
    except Exception as exc:
        raise StudioError(f'Không tìm được ảnh từ nguồn này lúc này ({type(exc).__name__}). Thử lại hoặc chọn nguồn khác.')


def _search(tid: int, source: str, q: str, settings: dict) -> list[dict]:
    q = (q or '').strip()
    if source == 'article':
        items = _article_candidates(tid)
        if q:   # photos whose caption mentions the keywords first; never hide the others
            words = set(normalize(q).split())
            items = sorted(items, key=lambda c: -len(words & set(normalize(c['description'] + ' ' + c['title']).split())))
        return items
    if not q:
        raise StudioError('Nhập từ khoá để tìm ảnh.')
    if source == 'pexels' and not (settings.get('pexels_key') or '').strip():
        raise StudioError('Chưa có Pexels API key (Cài đặt → Pexels).')
    if source == 'google' and not (settings.get('serpapi_key') or '').strip():
        raise StudioError('Chưa có SerpAPI key (Cài đặt).')
    if M.FIXTURE_DIR and source in ('openverse', 'commons', 'pexels', 'google'):
        return [dict(c, provider=c.get('provider') or source) for c in M.fixture_search(q)]
    if source == 'openverse':
        return M.openverse_search(q, limit=30)
    if source == 'commons':
        return M.commons_search(q, limit=30)
    if source == 'pexels':
        key = (settings.get('pexels_key') or '').strip()
        if not key:
            raise StudioError('Chưa có Pexels API key (Cài đặt → Pexels).')
        return _pexels(q, key)
    if source == 'google':
        key = (settings.get('serpapi_key') or '').strip()
        if not key:
            raise StudioError('Chưa có SerpAPI key (Cài đặt).')
        return _google(q, key)
    raise StudioError('Nguồn ảnh không hợp lệ.')


def thumbnail(url: str, referer: str = '') -> bytes:
    """Small JPEG preview fetched by the server (newspaper CDNs often block hot-linking from the browser)."""
    key = (url, referer)
    with _THUMB_LOCK:
        if key in _THUMBS:
            _THUMBS.move_to_end(key)
            return _THUMBS[key]
    if url.startswith('fixture://'):
        if not M.FIXTURE_DIR:
            raise StudioError('Ảnh không hợp lệ.')
        raw = (Path(M.FIXTURE_DIR) / Path(url[len('fixture://'):]).name).read_bytes()
    else:
        _, raw, _ = M.safe_get(url, max_bytes=20_000_000, browser=True, accept='image/*', referer=referer or None)
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    except Exception:
        raise StudioError('Không đọc được ảnh.')
    size = f'{im.width}×{im.height}'
    im.thumbnail((420, 420))
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=80)
    data = buf.getvalue()
    with _THUMB_LOCK:
        _THUMBS[key] = data
        while len(_THUMBS) > 400:
            _THUMBS.popitem(last=False)
    return data


def fetch_candidate(c: dict) -> dict:
    """Download the full image of a search result the editor picked; returns the media_cache row."""
    url = str(c.get('url') or '')
    if url.startswith('fixture://') and not M.FIXTURE_DIR:
        raise StudioError('Ảnh không hợp lệ.')
    if not (url.startswith('http') or url.startswith('fixture://')):
        raise StudioError('Link ảnh không hợp lệ (cần http/https).')
    keep = {k: c.get(k) for k in ('url', 'page_url', 'provider', 'title', 'description', 'creator', 'license',
                                  'license_url', 'rights_class')}
    keep['rights_class'] = keep.get('rights_class') or 'web'
    if keep['rights_class'] not in ('publisher', 'web', 'free', 'user') and not M.RIGHTS_LABEL.get(keep['rights_class']):
        keep['rights_class'] = 'web'
    if not keep.get('provider'):
        from tf_imagesearch import domain
        keep['provider'] = domain(keep.get('page_url') or url)
    try:
        return M.download(keep, min_side=STUDIO_MIN_SIDE)
    except M.MediaError as exc:
        raise StudioError(f'Không dùng được ảnh này: {exc}')
    except Exception as exc:
        raise StudioError(f'Không tải được ảnh ({type(exc).__name__}). Thử ảnh khác.')


def store_upload(data_url: str, filename: str, credit: str) -> dict:
    m = re.match(r'data:image/[\w.+-]+;base64,(.+)$', data_url or '', re.S)
    if not m:
        raise StudioError('File tải lên không phải ảnh.')
    raw = base64.b64decode(m.group(1))
    if len(raw) > 25_000_000:
        raise StudioError('Ảnh quá lớn (tối đa 25 MB).')
    c = {'url': 'upload://' + sha(base64.b64encode(raw[:2_000_000]).decode() + str(len(raw)))[:24],
         'page_url': '', 'provider': 'Ảnh tải lên', 'title': (filename or '')[:120], 'description': '',
         'creator': (credit or '').strip()[:120], 'license': '', 'rights_class': 'user'}
    with connect() as db:
        row = db.execute('SELECT * FROM media_cache WHERE url=?', (c['url'],)).fetchone()
    if row and row['local_filename'] and (M.MEDIA_DIR / row['local_filename']).is_file():
        return dict(row)
    try:
        return M.store_image(c, raw, STUDIO_MIN_SIDE)
    except M.MediaError as exc:
        raise StudioError(f'Không dùng được ảnh này: {exc}')


def credit_for(row: dict, override: str | None = None) -> str:
    if override:
        return override if override.lower().startswith('ảnh') else 'Ảnh: ' + override
    if row.get('rights_class') == 'user':
        return ('Ảnh: ' + row['creator']) if row.get('creator') else ''
    if row.get('rights_class') == 'free':
        return f"Ảnh: {row.get('creator') or 'không rõ'} · {row.get('provider') or ''}".strip(' ·')
    if row.get('rights_class') == 'web':
        return 'Ảnh: ' + (row.get('provider') or 'nguồn web')
    return M.credit_line(row)


# ------------------------------------------------------------------ plan for the renderer

def build_plan(sel: dict, timings: dict, headline: str) -> dict:
    """Same plan structure the renderer/QA already use: segments → shots → media."""
    tsegs = timings['segments']
    rows = media_rows(p['media_id'] for s in sel['segments'] for p in s['picks'])
    media, segments = {}, []
    for k, s in enumerate(sel['segments']):
        if s['last'] >= len(tsegs):
            raise StudioError('Phân đoạn không khớp giọng đọc hiện tại — bấm "Chia lại đoạn".')
        start, end = float(tsegs[s['first']]['start']), float(tsegs[s['last']]['end'])
        picks = [p for p in s['picks'] if p['media_id'] in rows]
        n = max(1, min(len(picks), int((end - start) // 2.2))) if picks else 0
        picks = picks[:n]
        word_starts = [w['start'] for w in timings.get('words', []) if s['first'] <= w['seg'] <= s['last'] and start < w['start'] < end]
        bounds = [start]
        for j in range(1, n):
            ideal = start + (end - start) * j / n
            cut = min(word_starts, key=lambda t: abs(t - ideal), default=ideal)
            bounds.append(cut if bounds[-1] + 1.2 < cut < end - 1.2 else ideal)
        bounds.append(end)
        shots = []
        for j, p in enumerate(picks):
            row = rows[p['media_id']]
            if str(row['id']) not in media:
                media[str(row['id'])] = {**{x: row.get(x) for x in ('id', 'url', 'page_url', 'provider', 'title', 'creator',
                                                                       'license', 'rights_class', 'local_filename', 'width', 'height')},
                                         'credit': credit_for(row, p.get('credit')), 'group': 'manual'}
            shots.append({'media_id': row['id'], 'match': 'manual', 'score': 0, 'hits': [], 'note': '',
                          'focus': p.get('focus'), 'start': round(bounds[j], 3), 'end': round(bounds[j + 1], 3)})
        segments.append({'index': k, 'text': s.get('text') or '', 'start': start, 'end': end, 'keywords': s.get('keywords', ''),
                         'sentences': list(range(s['first'], s['last'] + 1)), 'entities': [], 'subject': '', 'shots': shots})
    return {'version': 3, 'image_source': 'manual', 'headline': headline, 'segments': segments, 'media': media,
            'selection_hash': selection_hash(sel)}


def evaluate(plan: dict, duration: float) -> dict:
    issues, warnings, per_media = [], [], {}
    missing = [s['index'] + 1 for s in plan['segments'] if not s['shots']]
    if missing:
        issues.append('Đoạn chưa chọn ảnh: ' + ', '.join(map(str, missing)))
    gone = [m['local_filename'] for m in plan['media'].values() if not (M.MEDIA_DIR / (m['local_filename'] or '')).is_file()]
    if gone:
        issues.append('Thiếu file ảnh trong thư viện — chọn lại ảnh: ' + ', '.join(gone[:3]))
    for s in plan['segments']:
        for sh in s['shots']:
            per_media[sh['media_id']] = per_media.get(sh['media_id'], 0) + sh['end'] - sh['start']
    top = max(per_media.values()) / duration if per_media and duration else 0
    if top > 0.4:
        warnings.append(f'Một ảnh chiếm {top:.0%} thời lượng.')
    if per_media and len(per_media) < 4 and not missing:
        warnings.append(f'Chỉ {len(per_media)} ảnh khác nhau — nên thêm ảnh cho đỡ lặp.')
    no_credit = [s['index'] + 1 for s in plan['segments'] for sh in s['shots']
                 if not plan['media'][str(sh['media_id'])].get('credit')]
    if no_credit:
        warnings.append('Ảnh chưa có credit ở đoạn: ' + ', '.join(map(str, sorted(set(no_credit)))))
    web = sorted({s['index'] + 1 for s in plan['segments'] for sh in s['shots']
                  if plan['media'][str(sh['media_id'])].get('rights_class') == 'web'})
    if web:
        warnings.append('Ảnh web (bản quyền chủ sở hữu) ở đoạn: ' + ', '.join(map(str, web)))
    done = sum(1 for s in plan['segments'] if s['shots'])
    return {'passed': not issues, 'issues': issues, 'warnings': warnings, 'distinct_photos': len(per_media),
            'sentence_matched': done, 'segments': len(plan['segments']), 'max_photo_share': round(top, 3), 'manual': True}


def payload(sel: dict, tl: list[dict], approx: bool) -> dict:
    rows = media_rows(p['media_id'] for s in sel['segments'] for p in s['picks'])
    segs = []
    for k, s in enumerate(sel['segments']):
        picks = []
        for p in s['picks']:
            row = rows.get(p['media_id'])
            if not row:
                continue
            picks.append({'media_id': p['media_id'], 'focus': p.get('focus'), 'credit': p.get('credit'),
                          'credit_text': credit_for(row, p.get('credit')), 'thumb': f"/api/media/{row['id']}/image",
                          'page_url': row.get('page_url') or '', 'provider': row.get('provider') or '',
                          'rights_class': row.get('rights_class'), 'size': f"{row.get('width')}×{row.get('height')}",
                          'missing': not (M.MEDIA_DIR / (row.get('local_filename') or '')).is_file()})
        last = min(s['last'], len(tl) - 1)
        segs.append({'id': s['id'], 'n': k + 1, 'first': s['first'], 'last': s['last'], 'text': s.get('text', ''),
                     'start': tl[s['first']]['start'] if tl else 0, 'end': tl[last]['end'] if tl else 0,
                     'keywords': s.get('keywords', ''), 'picks': picks})
    return {'segments': segs, 'approx': approx, 'complete': bool(segs) and all(s['picks'] for s in segs),
            'hash': selection_hash(sel), 'max_picks': MAX_PICKS}
