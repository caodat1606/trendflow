"""Automatic real-photo selection for every spoken sentence.

Pipeline: entities per sentence (LLM, grounded in narration/article; heuristic fallback)
 -> search several provenance-tracked sources -> licence filter by policy
 -> relevance score from returned metadata (title/description/caption/Wikipedia page)
 -> download, validate, de-duplicate (dHash), optional local vision check
 -> assign one or two shots per sentence -> real-photo gate.

Nothing here fabricates imagery. If the gate fails, the caller must NOT render.
"""
from __future__ import annotations

import hashlib
import html
import io
import ipaddress
import json
import logging
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

import tf_llm
from tf_db import connect, now_iso
from tf_text import normalize, proper_names, sha

ROOT = Path(__file__).resolve().parent
MEDIA_DIR = Path(os.getenv('TRENDFLOW_DATA_DIR') or ROOT) / 'media_library'
CONTACT = os.getenv('CONTACT_EMAIL', 'local-user')
USER_AGENT = f'TrendFlow/3.0 (personal news video tool; {CONTACT}) python-requests'
BROWSER_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
MIN_SIDE = int(os.getenv('MIN_IMAGE_SIDE', '600'))
FIXTURE_DIR = os.getenv('TRENDFLOW_FIXTURE_DIR', '')
SENTENCE_THRESHOLD = 4.0
TYPE_WEIGHT = {'person': 6, 'event': 5, 'org': 4, 'place': 4, 'name': 4, 'object': 3}
BAD_TERMS = ('logo', 'icon', 'flag of', 'coat of arms', 'emblem', 'seal of', 'locator', 'location map',
             'map of', 'diagram', 'chart', 'graph', 'infographic', 'signature', 'screenshot', 'document',
             'banner', 'wordmark', 'svg', 'blank', 'placeholder', 'avatar', 'sprite', 'pictogram')
POLICY_CLASSES = {
    'strict': {'pd', 'cc0', 'cc-by'},
    'open': {'pd', 'cc0', 'cc-by', 'cc-by-sa'},
    'editorial': {'pd', 'cc0', 'cc-by', 'cc-by-sa', 'publisher'},
}
RIGHTS_LABEL = {'pd': 'Public domain', 'cc0': 'CC0', 'cc-by': 'CC BY', 'cc-by-sa': 'CC BY-SA',
                'publisher': 'Ảnh của bài báo gốc', 'unknown': 'Chưa rõ giấy phép'}


class MediaError(RuntimeError):
    pass


# ------------------------------------------------------------------ safe HTTP

def safe_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
            return False
        host = p.hostname.lower()
        if host == 'localhost' or host.endswith('.local'):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            pass
        for info in socket.getaddrinfo(host, None):
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
        return True
    except (OSError, ValueError):
        return False


def safe_get(url: str, *, timeout=15, max_bytes=15_000_000, browser=False, accept='*/*', referer=None):
    headers = {'User-Agent': BROWSER_UA if browser else USER_AGENT, 'Accept': accept}
    if referer:
        headers['Referer'] = referer   # many newspaper CDNs refuse hot-linked images without it
    for _ in range(5):
        if not safe_url(url):
            raise MediaError('URL không an toàn hoặc không công khai: ' + url[:80])
        r = requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get('Location', ''))
            r.close()
            continue
        r.raise_for_status()
        chunks, size = [], 0
        for part in r.iter_content(65536):
            size += len(part)
            if size > max_bytes:
                r.close()
                raise MediaError('Tệp quá lớn')
            chunks.append(part)
        r.close()
        return r.url, b''.join(chunks), r.headers.get('Content-Type', '')
    raise MediaError('Quá nhiều chuyển hướng')


def get_json(url, params):
    _, raw, _ = safe_get(url + '?' + urlencode(params), max_bytes=6_000_000, accept='application/json')
    return json.loads(raw)


def strip_html(s: str) -> str:
    return re.sub(r'\s+', ' ', BeautifulSoup(str(s or ''), 'html.parser').get_text(' ', strip=True)).strip()


# ------------------------------------------------------------------ licences

def classify_licence(text: str) -> str:
    t = ' '.join(str(text or '').lower().replace('_', ' ').split())
    if not t:
        return 'unknown'
    if re.search(r'\b(nc|nd|non-?commercial|no ?deriv|fair use|non-free|all rights reserved|gfdl only)\b', t):
        return 'unknown'
    if 'cc0' in t or 'cc zero' in t or 'cc-zero' in t:
        return 'cc0'
    if re.search(r'public domain|^pd\b|^pd-|\bpdm\b|no restrictions', t):
        return 'pd'
    if re.search(r'by[- ]?sa', t):
        return 'cc-by-sa'
    if re.search(r'cc[- ]?by\b|^by\b|attribution', t):
        return 'cc-by'
    return 'unknown'


def allowed(rights_class: str, policy: str) -> bool:
    return rights_class in POLICY_CLASSES.get(policy, POLICY_CLASSES['open'])


def credit_line(c: dict) -> str:
    who = (c.get('creator') or '').strip()
    who = re.sub(r'\s+', ' ', who)[:60]
    if c.get('rights_class') == 'publisher':
        return 'Ảnh: ' + (c.get('provider') if c.get('provider') not in (None, '', 'Bài báo gốc')
                          else (urlparse(c.get('page_url') or '').hostname or 'bài báo gốc').replace('www.', ''))
    parts = ['Ảnh: ' + (who or 'không rõ tác giả'), c.get('provider') or '']
    lic = c.get('license') or RIGHTS_LABEL.get(c.get('rights_class'), '')
    if lic:
        parts.append(lic)
    return ' · '.join(p for p in parts if p)


# ------------------------------------------------------------------ article

ARTICLE_SELECTORS = ('article, [itemprop="articleBody"], .article-body, .detail-content, .fck_detail, '
                     '.content-detail, #main-detail-body, .detail__content, [data-component="text-block"], '
                     '.article__body, .wysiwyg, .entry-content, main')


def extract_article(url: str) -> dict:
    actual, raw, _ = safe_get(url, max_bytes=6_000_000, browser=True, accept='text/html')
    soup = BeautifulSoup(raw, 'html.parser')
    og = soup.find('meta', attrs={'property': 'og:title'})
    title = og.get('content', '') if og else (soup.title.get_text(' ', strip=True) if soup.title else '')
    images = _article_images(soup, actual)
    for bad in soup(['script', 'style', 'noscript', 'aside', 'footer', 'nav', 'form']):
        bad.decompose()
    paras, seen = [], set()
    for node in (soup.select(ARTICLE_SELECTORS)[:4] or [soup]):
        for p in node.select('p'):
            t = p.get_text(' ', strip=True)
            if 40 <= len(t) <= 2000 and t not in seen:
                seen.add(t)
                paras.append(t)
        if sum(len(x) for x in paras) > 12000:
            break
    return {'url': actual, 'title': (title or '')[:300], 'text': '\n'.join(paras)[:12000], 'images': images}


JUNK_IMG = ('logo', 'icon', 'avatar', 'sprite', 'banner', 'advert', '/ads/', 'placeholder', 'blank.', 'pixel',
            'tracking', 'emoji', 'button', 'share', 'loading', 'spinner', 'author', 'default-image', 'no-image')


def _best_src(img, picture=None):
    """Largest candidate from srcset/data-srcset/<source>, else data-src/data-original/src."""
    cands = []
    for node in ([picture] if picture else []) + [img]:
        for src in (node.find_all('source') if node is picture else [node]):
            for attr in ('data-srcset', 'srcset'):
                for part in (src.get(attr) or '').split(','):
                    bits = part.strip().split()
                    if bits:
                        m = re.match(r'(\d+)(w|x)$', bits[1]) if len(bits) > 1 else None
                        cands.append((int(m.group(1)) * (1 if m and m.group(2) == 'w' else 1000) if m else 1, bits[0]))
    if cands:
        return max(cands)[1]
    return img.get('data-src') or img.get('data-original') or img.get('data-lazy-src') or img.get('src')


def _article_images(soup, base):
    """Photos of THIS article: og:image + figures inside the article body (not sidebars/related links)."""
    out, seen = [], set()

    def add(src, caption, note):
        if not src or str(src).startswith('data:'):
            return
        url = urljoin(base, html.unescape(str(src).strip()))
        low = url.lower()
        if not url.startswith('http') or url in seen or re.search(r'\.(svg|gif)(\?|$)', low) or any(j in low for j in JUNK_IMG):
            return
        seen.add(url)
        out.append({'url': url, 'caption': strip_html(caption)[:400], 'note': note})

    for key in ('og:image', 'twitter:image'):
        tag = soup.find('meta', attrs={'property': key}) or soup.find('meta', attrs={'name': key})
        if tag:
            og = soup.find('meta', attrs={'property': 'og:title'})
            add(tag.get('content'), og.get('content', '') if og else '', 'Ảnh đại diện bài báo')
            break
    body = None
    for node in soup.select(ARTICLE_SELECTORS):
        if len(node.select('p')) >= 3:
            body = node
            break
    for fig in (body or soup).select('figure, .fig-picture, .VCSortableInPreviewMode')[:30]:
        img = fig.find('img')
        if not img:
            continue
        try:
            if 0 < int(img.get('width') or 0) < 300:
                continue
        except ValueError:
            pass
        cap = fig.find('figcaption') or fig.find(class_=re.compile('caption|desc', re.I))
        add(_best_src(img, fig.find('picture')), (cap.get_text(' ', strip=True) if cap else '') or img.get('alt', ''), 'Ảnh trong bài')
    return out[:20]


def article_text(tid: int) -> dict:
    """Cached article text; fetched on demand. Falls back to RSS summary."""
    with connect() as db:
        row = db.execute('SELECT link,title,summary,title_vi,summary_vi FROM trends WHERE id=?', (tid,)).fetchone()
        ctx = db.execute('SELECT * FROM article_context WHERE trend_id=?', (tid,)).fetchone()
    if not row:
        raise MediaError('Không tìm thấy tin')
    if ctx and ctx['body'] and len(ctx['body']) > 300:
        return {'url': ctx['article_url'] or row['link'], 'title': ctx['title'] or row['title'], 'text': ctx['body'],
                'source': 'article'}
    if not FIXTURE_DIR:
        try:
            art = extract_article(row['link'])
            if len(art['text']) > 300:
                with connect() as db:
                    db.execute('''INSERT INTO article_context(trend_id,fetched_at,article_url,title,body) VALUES(?,?,?,?,?)
                                  ON CONFLICT(trend_id) DO UPDATE SET fetched_at=excluded.fetched_at,
                                  article_url=excluded.article_url,title=excluded.title,body=excluded.body''',
                               (tid, now_iso(), art['url'], art['title'], art['text']))
                return {'url': art['url'], 'title': art['title'], 'text': art['text'], 'source': 'article'}
        except Exception as exc:
            logging.warning('Article fetch failed for %s: %s', tid, exc)
    text = '\n'.join(x for x in (row['title'], row['summary'], row['title_vi'], row['summary_vi']) if x)
    return {'url': row['link'], 'title': row['title'], 'text': text, 'source': 'rss'}


# ------------------------------------------------------------------ entities

def entities_llm(segments: list[str], article: dict, cancel=None) -> dict:
    numbered = '\n'.join(f'{i}. {s}' for i, s in enumerate(segments))
    prompt = f'''Nhiệm vụ: chọn ẢNH THẬT minh hoạ cho từng câu của bản tin video.
Với MỖI câu, liệt kê tối đa 3 đối tượng có thể có ảnh chụp thật: người, tổ chức, địa điểm, sự kiện có tên,
hoặc vật thể cụ thể là CHỦ THỂ của câu. Nếu câu dùng đại từ ("ông", "đội bóng này"), lấy đối tượng từ câu trước.
Mỗi đối tượng gồm:
 "vi": tên viết ĐÚNG như trong lời thoại hoặc bài nguồn,
 "en": tên tiếng Anh chuẩn để tìm trên Wikipedia/Wikimedia Commons,
 "type": một trong person|org|place|event|object.
Đặt CHỦ THỂ CHÍNH của câu (cái câu đang nói về) ở vị trí đầu tiên.
Mỗi câu thêm "scene": 2–5 từ TIẾNG ANH mô tả cảnh chung, không tên riêng, dùng làm ảnh minh hoạ khi không có ảnh
của chủ thể (ví dụ câu về giải đấu game PUBG -> "esports tournament arena"; về giá xăng -> "gas station pump").
Thêm "story": tối đa 5 đối tượng chính của cả bản tin (cùng định dạng).
KHÔNG đưa đối tượng không có trong lời thoại hoặc bài nguồn. Không thêm chữ ngoài JSON.
Định dạng: {{"sentences":[{{"i":0,"entities":[{{"vi":"...","en":"...","type":"place"}}],"scene":"..."}}],"story":[...]}}

TIÊU ĐỀ NGUỒN: {article.get('title', '')[:300]}
BÀI NGUỒN (trích): {article.get('text', '')[:3500]}

LỜI THOẠI THEO CÂU:
{numbered}'''
    return tf_llm.chat_json(prompt, max_tokens=1800, cancel=cancel, temperature=0.1)


def _entity(raw, ground_text: str):
    if not isinstance(raw, dict):
        return None
    vi = str(raw.get('vi') or '').strip()[:80]
    en = str(raw.get('en') or '').strip()[:80]
    typ = str(raw.get('type') or 'object').strip().lower()
    if typ not in TYPE_WEIGHT:
        typ = 'object'
    nv, ne = normalize(vi), normalize(en)
    hay = ' ' + ground_text + ' '
    grounded = (len(nv) >= 3 and f' {nv} ' in hay) or (len(ne) >= 3 and f' {ne} ' in hay)
    if not grounded:
        return None
    return {'vi': vi or en, 'en': en or vi, 'type': typ, 'key': nv or ne}


def build_entities(segments: list[str], article: dict, use_llm=True, cancel=None):
    """Returns (per-sentence entities, story entities, source, per-sentence generic scene queries)."""
    ground = normalize(' '.join(segments) + ' ' + article.get('title', '') + ' ' + article.get('text', ''))
    per = [[] for _ in segments]
    scenes = [''] * len(segments)
    story: list[dict] = []
    source = 'heuristic'
    if use_llm:
        try:
            data = entities_llm(segments, article, cancel)
            for item in data.get('sentences', []) if isinstance(data, dict) else []:
                i = item.get('i') if isinstance(item, dict) else None
                if isinstance(i, int) and 0 <= i < len(segments):
                    sc = re.sub(r'[^A-Za-z0-9 -]', ' ', str(item.get('scene') or ''))
                    scenes[i] = ' '.join(sc.split()[:6])
                    for raw in (item.get('entities') or [])[:4]:
                        e = _entity(raw, ground)
                        if e and e['key'] not in {x['key'] for x in per[i]}:
                            per[i].append(e)
            for raw in (data.get('story') or [])[:6] if isinstance(data, dict) else []:
                e = _entity(raw, ground)
                if e and e['key'] not in {x['key'] for x in story}:
                    story.append(e)
            source = 'llm'
        except Exception as exc:
            if type(exc).__name__ == 'JobCancelled':
                raise
            logging.warning('Entity LLM failed, using heuristic: %s', exc)
    # Heuristic names always complement (catches what the LLM missed).
    known = {normalize(n) for n in proper_names(' '.join(segments) + ' ' + article.get('text', '')[:6000])}
    for i, seg in enumerate(segments):
        for name in proper_names(seg, known)[:4]:
            key = normalize(name)
            if key and key not in {x['key'] for x in per[i]} and not any(key in x['key'] or x['key'] in key for x in per[i]):
                per[i].append({'vi': name, 'en': name, 'type': 'name', 'key': key})
    if not story:
        counts: dict[str, list] = {}
        for ents in per:
            for e in ents:
                counts.setdefault(e['key'], [0, e])[0] += 1
        for name in proper_names(article.get('title', '')):
            key = normalize(name)
            counts.setdefault(key, [0, {'vi': name, 'en': name, 'type': 'name', 'key': key}])[0] += 2
        story = [v[1] for v in sorted(counts.values(), key=lambda x: -x[0])][:5]
    # Pronoun sentences inherit the previous sentence's subjects.
    for i in range(1, len(per)):
        if not per[i]:
            per[i] = [dict(e, inherited=True) for e in per[i - 1][:2]]
    return per, story, source, scenes


# ------------------------------------------------------------------ sources

def _commons_imageinfo(titles: list[str]) -> list[dict]:
    if not titles:
        return []
    data = get_json('https://commons.wikimedia.org/w/api.php', {
        'action': 'query', 'format': 'json', 'titles': '|'.join(titles[:40]), 'prop': 'imageinfo',
        'iiprop': 'url|size|mime|extmetadata', 'iiurlwidth': '1600'})
    return [_commons_page(p) for p in ((data.get('query') or {}).get('pages') or {}).values() if p.get('imageinfo')]


def _commons_page(page: dict) -> dict:
    info = (page.get('imageinfo') or [{}])[0]
    meta = info.get('extmetadata') or {}
    val = lambda k: strip_html((meta.get(k) or {}).get('value', ''))
    lic = val('LicenseShortName') or val('UsageTerms') or val('License')
    return {'url': info.get('thumburl') or info.get('url'), 'page_url': info.get('descriptionurl') or '',
            'provider': 'Wikimedia Commons', 'title': page.get('title', '').replace('File:', ''),
            'description': ' '.join(x for x in (val('ImageDescription'), val('ObjectName'), val('Categories')) if x)[:1500],
            'creator': val('Artist') or val('Credit'), 'license': lic, 'license_url': val('LicenseUrl'),
            'rights_class': classify_licence(lic), 'mime': info.get('mime', ''),
            'width': info.get('thumbwidth') or info.get('width') or 0,
            'height': info.get('thumbheight') or info.get('height') or 0, 'taken': val('DateTimeOriginal')[:40]}


def commons_search(query: str, limit=15) -> list[dict]:
    data = get_json('https://commons.wikimedia.org/w/api.php', {
        'action': 'query', 'format': 'json', 'generator': 'search', 'gsrsearch': f'{query[:120]} filetype:bitmap',
        'gsrnamespace': '6', 'gsrlimit': str(limit), 'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata',
        'iiurlwidth': '1600'})
    return [_commons_page(p) for p in ((data.get('query') or {}).get('pages') or {}).values() if p.get('imageinfo')]


def wikipedia_entity_images(name: str, lang: str, max_files=8) -> list[dict]:
    api = f'https://{lang}.wikipedia.org/w/api.php'
    found = get_json(api, {'action': 'query', 'format': 'json', 'list': 'search', 'srsearch': name[:120], 'srlimit': '2'})
    out = []
    want = set(normalize(name).split())
    for hit in ((found.get('query') or {}).get('search') or [])[:2]:
        title = hit.get('title', '')
        have = set(normalize(title).split())
        if not want or len(want & have) / len(want) < 0.6:
            continue
        pages = get_json(api, {'action': 'query', 'format': 'json', 'titles': title, 'prop': 'pageimages|images',
                               'piprop': 'name', 'imlimit': '40'})
        for page in ((pages.get('query') or {}).get('pages') or {}).values():
            lead = page.get('pageimage')
            files = [f'File:{lead}'] if lead else []
            files += [im['title'] for im in page.get('images', []) if re.search(r'\.(jpe?g|png|webp)$', im.get('title', ''), re.I)
                      and f"File:{lead}" != im['title'].replace('Tập tin:', 'File:').replace('Hình:', 'File:')]
            files = [re.sub(r'^(Tập tin|Hình|Image):', 'File:', f) for f in files][:max_files + 1]
            for c in _commons_imageinfo(files):  # non-free local files are not on Commons -> skipped
                c['wiki_page'] = title
                c['lead'] = bool(lead) and c['title'] == lead.replace('_', ' ')
                out.append(c)
    return out


def openverse_search(query: str, limit=12) -> list[dict]:
    data = get_json('https://api.openverse.org/v1/images/', {'q': query[:120], 'license': 'by,by-sa,cc0,pdm',
                                                           'page_size': str(limit), 'mature': 'false'})
    out = []
    for r in data.get('results', []):
        lic = f"{r.get('license', '')} {r.get('license_version', '')}".strip()
        lic_name = ('CC ' + r.get('license', '').upper() + ' ' + str(r.get('license_version') or '')).strip()
        if r.get('license') in ('pdm',):
            lic_name = 'Public Domain Mark'
        out.append({'url': r.get('url'), 'page_url': r.get('foreign_landing_url') or '',
                    'provider': 'Openverse/' + str(r.get('source') or r.get('provider') or ''),
                    'title': r.get('title') or '', 'description': ' '.join(t.get('name', '') for t in (r.get('tags') or [])[:15]),
                    'creator': r.get('creator') or '', 'license': lic_name, 'license_url': r.get('license_url') or '',
                    'rights_class': classify_licence(lic if r.get('license') != 'pdm' else 'public domain'),
                    'width': r.get('width') or 0, 'height': r.get('height') or 0, 'taken': ''})
    return out


def fixture_search(query: str) -> list[dict]:
    """Offline test source that behaves like real title search over a local, labelled photo set:
    all query words must appear (AND); an item with `wiki_page` is returned as that page's lead
    image only when the query names exactly that page."""
    items = json.loads((Path(FIXTURE_DIR) / 'media.json').read_text(encoding='utf-8'))
    q = set(normalize(query).split())
    out = []
    for it in items:
        hay = set(normalize(it.get('title', '') + ' ' + it.get('description', '')).split())
        lead = bool(it.get('wiki_page')) and normalize(it['wiki_page']) == normalize(query)
        if (q and q <= hay) or lead:
            c = {k: v for k, v in it.items() if k not in ('wiki_page', 'file')}
            c['url'] = 'fixture://' + it['file']
            c['rights_class'] = classify_licence(it.get('license', ''))
            if lead:
                c['wiki_page'], c['lead'] = it['wiki_page'], True
            out.append(c)
    return out


def fixture_article_images() -> list[dict]:
    p = Path(FIXTURE_DIR) / 'article_images.json'
    if not p.is_file():
        return []
    return [dict(it, url='fixture://' + it['file'], rights_class='publisher')
            for it in json.loads(p.read_text(encoding='utf-8'))]


def gather_candidates(entities: list[dict], article: dict, policy: str, log) -> list[dict]:
    """Search each entity in several sources. Every candidate keeps its provenance."""
    pool: dict[str, dict] = {}

    def add(c, entity=None, via=''):
        if not c.get('url'):
            return
        if c.get('mime') and not str(c['mime']).startswith(('image/jpeg', 'image/png', 'image/webp')):
            return
        cur = pool.setdefault(c['url'], dict(c, found_for=[], via=[]))
        if entity and entity['key'] not in cur['found_for']:
            cur['found_for'].append(entity['key'])
        if c.get('lead'):
            cur['lead'] = True
            cur.setdefault('lead_of', [])
            if entity and entity['key'] not in cur['lead_of']:
                cur['lead_of'].append(entity['key'])
        if c.get('wiki_page'):
            cur['wiki_page'] = c['wiki_page']
        if via and via not in cur['via']:
            cur['via'].append(via)

    if FIXTURE_DIR:
        for e in entities:
            for q in {e['en'], e['vi']}:
                for c in fixture_search(q):
                    add(c, e, 'fixture')
        if policy == 'editorial':
            for c in fixture_article_images():
                add(dict(c, provider='Bài báo gốc', page_url=article.get('url', '')), None, 'article')
        return [c for c in pool.values() if allowed(c['rights_class'], policy)]

    if policy == 'editorial':
        try:
            art = extract_article(article['url'])
            for im in art['images']:
                add({'url': im['url'], 'page_url': art['url'], 'provider': 'Bài báo gốc', 'title': art['title'],
                     'description': im['caption'], 'creator': '', 'license': 'Bản quyền của tòa soạn',
                     'rights_class': 'publisher', 'width': 0, 'height': 0}, None, 'article')
        except Exception as exc:
            log(f'Không đọc được ảnh bài gốc: {str(exc)[:80]}')
    for e in entities:
        jobs = [('wikipedia-vi', lambda: wikipedia_entity_images(e['vi'], 'vi')),
                ('wikipedia-en', lambda: wikipedia_entity_images(e['en'], 'en')),
                ('commons', lambda: commons_search(f'"{e["en"]}"')),
                ('openverse', lambda: openverse_search(e['en']))]
        if normalize(e['vi']) != normalize(e['en']):
            jobs.append(('commons-vi', lambda: commons_search(f'"{e["vi"]}"')))
            jobs.append(('openverse-vi', lambda: openverse_search(e['vi'])))
        for via, fn in jobs:
            try:
                for c in fn():
                    add(c, e, via)
            except Exception as exc:
                log(f'{via} lỗi khi tìm "{e["en"]}": {str(exc)[:70]}')
    return [c for c in pool.values() if allowed(c.get('rights_class', 'unknown'), policy)]


# ------------------------------------------------------------------ scoring

def _phrase_in(name: str, hay: str) -> bool:
    n = normalize(name)
    return len(n) >= 3 and f' {n} ' in hay


def score(c: dict, entities: list[dict], *, article_bonus=False) -> tuple[float, list[str]]:
    hay = ' ' + normalize(' '.join(str(c.get(k) or '') for k in ('title', 'description', 'wiki_page')) + ' ' +
                          unquote(urlparse(c.get('url') or '').path.rsplit('/', 1)[-1])) + ' '
    if any(f' {normalize(b)} ' in hay for b in BAD_TERMS):
        return -10.0, ['loại: logo/bản đồ/tài liệu']
    total, hits = 0.0, []
    own = [e for e in entities if not e.get('inherited')]
    for e in entities:
        w = TYPE_WEIGHT[e['type']]
        if len(own) >= 2 and e is not own[0]:
            # Secondary mention ("…7 kỷ lục Guinness" in a sentence about PUBG): alone it is not enough,
            # except for a named person.
            w *= 0.75
        if e['key'] in (c.get('lead_of') or []):
            total += w + 2
            hits.append(e['vi'])
        elif _phrase_in(e['vi'], hay) or _phrase_in(e['en'], hay):
            total += w
            hits.append(e['vi'])
        elif e['key'] in (c.get('found_for') or []) and c.get('wiki_page') and \
                (_phrase_in(e['vi'], ' ' + normalize(c['wiki_page']) + ' ') or _phrase_in(e['en'], ' ' + normalize(c['wiki_page']) + ' ')):
            total += w * 0.6  # image used on that entity's Wikipedia page
            hits.append(e['vi'] + ' (trang Wikipedia)')
    if len(hits) >= 2:
        total += 2
    if article_bonus and c.get('rights_class') == 'publisher':
        total += 3  # the article's own photo depicts the reported event
    return total, hits


def illustration_candidates(queries: list[str], policy: str, log) -> list[dict]:
    """Generic, clearly labelled illustration photos for sentences whose subject has no usable photo."""
    out, seen = [], set()
    for q in dict.fromkeys(x for x in queries if x):
        try:
            found = fixture_search(q) if FIXTURE_DIR else commons_search(q, limit=10) + openverse_search(q, limit=10)
        except Exception as exc:
            log(f'Tìm ảnh minh hoạ "{q}" lỗi: {str(exc)[:60]}')
            continue
        for c in found:
            if c.get('url') and c['url'] not in seen and allowed(c.get('rights_class', 'unknown'), policy):
                if c.get('mime') and not str(c['mime']).startswith(('image/jpeg', 'image/png', 'image/webp')):
                    continue
                seen.add(c['url'])
                out.append(dict(c, scene_query=q, found_for=[], via=['illustration']))
    return out


ILLUS_STOP = {'the', 'a', 'an', 'of', 'and', 'with', 'in', 'on', 'at', 'for', 'photo', 'image'}


def illustration_score(c: dict, scene: str) -> float:
    words = [w for w in normalize(scene).split() if w not in ILLUS_STOP and len(w) > 2]
    if not words:
        return 0.0
    hay = ' ' + normalize(' '.join(str(c.get(k) or '') for k in ('title', 'description'))) + ' '
    if any(f' {normalize(b)} ' in hay for b in BAD_TERMS):
        return 0.0
    ratio = sum(1 for w in words if f' {w} ' in hay) / len(words)
    return round(4 * ratio, 2) if ratio >= 0.6 else 0.0


def dhash(img: Image.Image) -> str:
    g = img.convert('L').resize((9, 8), Image.Resampling.BILINEAR)
    px = list(g.tobytes())
    bits = ''.join('1' if px[r * 9 + c] > px[r * 9 + c + 1] else '0' for r in range(8) for c in range(8))
    return f'{int(bits, 2):016x}'


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count('1')


def download(c: dict, min_side: int | None = None) -> dict:
    """Download+validate once; persists in media_cache. Returns the cache row as dict."""
    MEDIA_DIR.mkdir(exist_ok=True)
    with connect() as db:
        row = db.execute('SELECT * FROM media_cache WHERE url=?', (c['url'],)).fetchone()
    if row and row['local_filename'] and (MEDIA_DIR / row['local_filename']).is_file():
        if min(row['width'] or 0, row['height'] or 0) < (min_side or MIN_SIDE):   # cache must not bypass the size rule
            raise MediaError(f"Ảnh nhỏ {row['width']}×{row['height']}")
        return dict(row)
    if c['url'].startswith('fixture://'):
        raw = (Path(FIXTURE_DIR) / c['url'][len('fixture://'):]).read_bytes()
    else:
        pub = c.get('rights_class') == 'publisher'
        _, raw, _ = safe_get(c['url'], max_bytes=20_000_000, browser=pub, accept='image/*',
                             referer=c.get('page_url') if pub else None)
    try:
        im = Image.open(io.BytesIO(raw))
        im.verify()
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    except Exception as exc:
        raise MediaError('Ảnh hỏng/không đọc được: ' + str(exc)[:60])
    if min(im.size) < (min_side or MIN_SIDE):
        raise MediaError(f'Ảnh nhỏ {im.size[0]}×{im.size[1]}')
    im.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    name = f'm_{sha(c["url"])[:20]}.jpg'
    im.save(MEDIA_DIR / name, 'JPEG', quality=90)
    dh = dhash(im)
    with connect() as db:
        db.execute('''INSERT INTO media_cache(url,page_url,provider,title,description,creator,license,license_url,
                      rights_class,width,height,taken,local_filename,dhash,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(url) DO UPDATE SET local_filename=excluded.local_filename,dhash=excluded.dhash,
                      width=excluded.width,height=excluded.height,fetched_at=excluded.fetched_at''',
                   (c['url'], c.get('page_url'), c.get('provider'), c.get('title'), (c.get('description') or '')[:1500],
                    (c.get('creator') or '')[:300], c.get('license'), c.get('license_url'), c.get('rights_class'),
                    im.size[0], im.size[1], c.get('taken'), name, dh, now_iso()))
        return dict(db.execute('SELECT * FROM media_cache WHERE url=?', (c['url'],)).fetchone())


def vision_check(path: Path, sentence: str, entities: list[dict], model: str, cancel=None) -> tuple[bool, str]:
    with Image.open(path) as im:
        im = im.convert('RGB')
        im.thumbnail((768, 768))
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=80)
    names = ', '.join(f"{e['en']} ({e['type']})" for e in entities[:3])
    prompt = ('Look at the image pixels. Answer JSON only: {"is_photo": true/false, "kind": "photo|logo|map|document|chart|illustration",'
              ' "shows": "short description", "relevance": 0-10}. relevance = how well this real photo could illustrate the '
              f'news sentence below (subjects: {names}). Do not identify people by face.\nSentence: {sentence[:400]}')
    try:
        data = tf_llm.parse_json(tf_llm.chat(prompt, model=model, images=[buf.getvalue()], json_mode=True,
                                             max_tokens=160, temperature=0, cancel=cancel, system='You are a strict photo editor.'))
    except tf_llm.LLMError as exc:
        return True, 'Không chạy được AI nhìn ảnh: ' + str(exc)[:80]  # enhancement only
    ok = bool(data.get('is_photo')) and str(data.get('kind', 'photo')).lower() == 'photo' and float(data.get('relevance') or 0) >= 4
    return ok, f"AI nhìn ảnh: {data.get('shows', '')[:100]} (liên quan {data.get('relevance')}/10)"


# ------------------------------------------------------------------ plan

def build_plan(tid: int, timings: dict, headline: str, settings: dict, *, cancel=None, progress=None,
               use_llm=True) -> dict:
    """Return {'plan', 'report', 'passed'}. Never fabricates or substitutes graphics."""
    log_lines: list[str] = []

    def log(msg):
        log_lines.append(msg)
        if progress:
            progress(msg)

    segs = timings['segments']
    texts = [s['text'] for s in segs]
    article = article_text(tid)
    policy = settings.get('rights_policy', 'open')
    log('Đang xác định nhân vật/địa điểm/sự kiện của từng câu…')
    per, story, ent_source, scene_queries = build_entities(texts, article, use_llm=use_llm, cancel=cancel)
    all_entities = {e['key']: e for ents in per for e in ents if not e.get('inherited')}
    for e in story:
        all_entities.setdefault(e['key'], e)
    log(f'Tìm ảnh cho {len(all_entities)} đối tượng trên Wikipedia/Wikimedia/Openverse…')
    if cancel:
        cancel()
    candidates = gather_candidates(list(all_entities.values())[:14], article, policy, log)
    log(f'Tìm thấy {len(candidates)} ảnh hợp lệ giấy phép ({policy}). Đang chấm điểm theo từng câu…')
    illus_pool = None  # searched lazily, only if some sentence needs it
    vision_model = None if FIXTURE_DIR else tf_llm.status().get('vision_model')

    used_ids: list[int] = []
    used_hash: dict[int, str] = {}
    rejected: dict[str, str] = {}
    media: dict[str, dict] = {}
    downloaded: dict[str, dict] = {}

    def usable(c, sentence, ents):
        if c['url'] in rejected:
            return None
        if c['url'] not in downloaded:
            try:
                if cancel:
                    cancel()
                downloaded[c['url']] = download(c)
            except Exception as exc:
                if type(exc).__name__ == 'JobCancelled':
                    raise
                rejected[c['url']] = str(exc)[:90]
                return None
        row = downloaded[c['url']]
        if any(hamming(row['dhash'], h) <= 8 and mid != row['id'] for mid, h in used_hash.items()):
            return None  # near duplicate of an image already in the video
        if vision_model and not c.get('_vision_ok'):
            ok, why = vision_check(MEDIA_DIR / row['local_filename'], sentence, ents, vision_model, cancel)
            c['_vision_note'] = why
            if not ok:
                rejected[c['url']] = why
                return None
            c['_vision_ok'] = True
        return row

    story_ranked = sorted(((score(c, story, article_bonus=True)[0], c) for c in candidates), key=lambda x: -x[0])
    url_to_id: dict[str, int] = {}
    out_segments = []

    def take(c, row, s, hits, match):
        media[str(row['id'])] = _media_entry(row, c, illustration=(match == 'illustration'))
        url_to_id[c['url']] = row['id']
        if row['id'] not in used_ids:
            used_ids.append(row['id'])
            used_hash[row['id']] = row['dhash']
        return {'media_id': row['id'], 'match': match, 'score': round(s, 1), 'hits': hits, 'note': c.get('_vision_note', '')}

    for i, seg in enumerate(segs):
        if cancel:
            cancel()
        ents = per[i]
        ranked = []
        for c in candidates:
            s, hits = score(c, ents, article_bonus=(i == 0))
            if s >= SENTENCE_THRESHOLD:
                ranked.append((s, hits, c))
        ranked.sort(key=lambda x: -x[0])
        dur = seg['end'] - seg['start']
        want = 2 if dur > 7.5 else 1
        prev_ids = {sh['media_id'] for sh in (out_segments[-1]['shots'] if out_segments else [])}
        shots = []
        # 1) photos of THIS sentence's subject not yet in the video
        for s, hits, c in ranked:
            if c['url'] in url_to_id:
                continue
            row = usable(c, seg['text'], ents)
            if row:
                shots.append(take(c, row, s, hits, 'sentence'))
                if len(shots) >= want:
                    break
        # 2) same subject already shown earlier (not in the previous sentence), then continuation
        if not shots:
            for allow_adjacent in (False, True):
                for s, hits, c in ranked:
                    mid = url_to_id.get(c['url'])
                    if mid is not None and (allow_adjacent or mid not in prev_ids):
                        shots.append({'media_id': mid, 'match': 'sentence', 'score': round(s, 1), 'hits': hits,
                                      'note': 'Cùng chủ thể với câu khác'})
                        break
                if shots:
                    break
        # 3) sentence names its own subject but no photo of it exists: a generic, LABELLED illustration
        #    is more honest than a photo of a different named subject.
        own_subject = any(not e.get('inherited') for e in ents)
        if not shots and own_subject and scene_queries[i]:
            if illus_pool is None:
                illus_pool = illustration_candidates([q for q in scene_queries if q], policy, log)
            ranked_i = sorted(((illustration_score(c, scene_queries[i]), c) for c in illus_pool
                               if c.get('scene_query') == scene_queries[i] or illustration_score(c, scene_queries[i]) > 0),
                              key=lambda x: -x[0])
            for s, c in ranked_i:
                if s <= 0 or c['url'] in url_to_id:
                    continue
                row = usable(c, seg['text'], ents)
                if row:
                    shots.append(take(c, row, s, ['minh hoạ: ' + scene_queries[i]], 'illustration'))
                    break
        # 4) the story's main subject (still a real, relevant photo), unused first
        if not shots:
            for s, c in story_ranked:
                if s < SENTENCE_THRESHOLD or c['url'] in url_to_id:
                    continue
                row = usable(c, seg['text'], story)
                if row:
                    shots.append(take(c, row, s, score(c, story)[1], 'story'))
                    break
        if not shots:
            for s, c in story_ranked:
                mid = url_to_id.get(c['url'])
                if s >= SENTENCE_THRESHOLD and mid is not None and mid not in prev_ids:
                    shots.append({'media_id': mid, 'match': 'story', 'score': round(s, 1), 'hits': score(c, story)[1],
                                  'note': 'Ảnh chủ thể chính của tin'})
                    break
        # Time each shot inside the sentence (split long sentences at a word boundary).
        words = [w for w in timings.get('words', []) if w['seg'] == i]
        bounds = [seg['start']]
        if len(shots) == 2:
            mid_t = seg['start'] + dur / 2
            cut = min((w['start'] for w in words), key=lambda t: abs(t - mid_t), default=mid_t)
            bounds.append(cut if seg['start'] + 2 < cut < seg['end'] - 2 else mid_t)
        bounds.append(seg['end'])
        for k, sh in enumerate(shots):
            sh['start'], sh['end'] = round(bounds[k], 3), round(bounds[k + 1], 3)
        out_segments.append({'index': i, 'text': seg['text'], 'start': seg['start'], 'end': seg['end'],
                             'entities': [{'vi': e['vi'], 'en': e['en'], 'type': e['type']} for e in ents],
                             'shots': shots})
    plan = {'version': 3, 'headline': headline, 'policy': policy, 'segments': out_segments, 'media': media,
            'entity_source': ent_source, 'story': [{'vi': e['vi'], 'en': e['en'], 'type': e['type']} for e in story],
            'article_url': article.get('url'), 'article_source': article.get('source')}
    report = evaluate(plan, timings['segments'][-1]['end'])
    report['log'] = log_lines[-40:]
    report['rejected_examples'] = list(rejected.values())[:8]
    report['candidates'] = len(candidates)
    report['vision_model'] = vision_model
    return {'plan': plan, 'report': report, 'passed': report['passed']}


def _media_entry(row: dict, c: dict, illustration: bool = False) -> dict:
    entry = {k: row.get(k) for k in ('id', 'url', 'page_url', 'provider', 'title', 'creator', 'license',
                                       'rights_class', 'local_filename', 'width', 'height')}
    entry['credit'] = ('Ảnh minh hoạ · ' if illustration else '') + credit_line(row)
    entry['via'] = c.get('via', [])
    return entry


def evaluate(plan: dict, duration: float) -> dict:
    """Real-photo gate. A graphics-only or single-photo slideshow is a FAILURE."""
    issues, per_media = [], {}
    missing = [s['index'] + 1 for s in plan['segments'] if not s['shots']]
    if missing:
        issues.append('Câu chưa có ảnh thật: ' + ', '.join(map(str, missing)))
    for s in plan['segments']:
        for sh in s['shots']:
            per_media[sh['media_id']] = per_media.get(sh['media_id'], 0) + sh['end'] - sh['start']
    distinct = len(per_media)
    need = min(4, len(plan['segments']))
    if distinct < need:
        issues.append(f'Chỉ có {distinct} ảnh khác nhau (cần ít nhất {need}).')
    if per_media and duration > 0:
        top = max(per_media.values()) / duration
        if top > 0.35:
            issues.append(f'Một ảnh chiếm {top:.0%} thời lượng (tối đa 35%).')
    else:
        top = 0
    sentence_level = sum(1 for s in plan['segments'] if s['shots'] and s['shots'][0]['match'] == 'sentence')
    ratio = sentence_level / max(1, len(plan['segments']))
    warnings = []
    if ratio < 0.4 and not missing:
        warnings.append(f'Chỉ {sentence_level}/{len(plan["segments"])} câu có ảnh khớp riêng; các câu còn lại dùng ảnh chủ thể chính của tin.')
    return {'passed': not issues, 'issues': issues, 'warnings': warnings, 'distinct_photos': distinct,
            'sentence_matched': sentence_level, 'segments': len(plan['segments']),
            'max_photo_share': round(top, 3)}


def plan_hash(plan: dict, narration_h: str, voice_filename: str) -> str:
    core = {'n': narration_h, 'v': voice_filename,
            's': [[s['text'], [(sh['media_id'], sh['start'], sh['end']) for sh in s['shots']]] for s in plan['segments']],
            'm': sorted((k, v.get('local_filename')) for k, v in plan['media'].items()), 'h': plan.get('headline')}
    return sha(json.dumps(core, ensure_ascii=False, sort_keys=True))
