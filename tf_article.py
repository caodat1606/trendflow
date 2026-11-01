"""Ảnh THẬT của tin: chỉ lấy ảnh nằm trong bài báo gốc và bài của báo khác viết CÙNG sự kiện.

Không dùng ảnh tìm theo từ khoá (Wikimedia/Openverse) — kiểu đó từng ghép ảnh BLACKPINK vào tin
Netflix/PUBG chỉ vì chú thích có chữ "PUBG". Ảnh thuộc bản quyền toà soạn; credit tên báo hiện trên
màn hình và trong file nguồn.

Ghép ảnh–câu: (1) chú thích ảnh nhắc đúng người/nơi/sự việc của câu; (2) còn lại theo thứ tự xuất hiện
trong bài (bài báo kể theo diễn biến giống lời thoại), ưu tiên ảnh ít dùng, không lặp ở 2 câu liền nhau.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import tf_media as M
from tf_db import connect
from tf_text import normalize, proper_names

ARTICLE_MIN_SIDE = int(os.getenv('ARTICLE_MIN_IMAGE_SIDE', '360'))
CAPTION_THRESHOLD = 4.0
OUTLETS = {'vnexpress.net': 'VnExpress', 'tuoitre.vn': 'Tuổi Trẻ', 'thanhnien.vn': 'Thanh Niên', 'bbc.co.uk': 'BBC',
           'bbc.com': 'BBC', 'theguardian.com': 'The Guardian', 'aljazeera.com': 'Al Jazeera', 'npr.org': 'NPR',
           'theverge.com': 'The Verge', 'techcrunch.com': 'TechCrunch', 'variety.com': 'Variety',
           'hollywoodreporter.com': 'The Hollywood Reporter', 'dantri.com.vn': 'Dân Trí', 'vietnamnet.vn': 'VietNamNet',
           'zingnews.vn': 'Znews', 'znews.vn': 'Znews', 'nld.com.vn': 'Người Lao Động', 'reuters.com': 'Reuters',
           'apnews.com': 'AP'}
STOP = set('''va cua trong dang voi tren duoc sau khi cho tai mot nhung cac nam ngay la co khong nay da se ve tu den
the and that this from with after about will their into over than have has for are was were its his her new news
video photo anh tin bao moi nhat hom nay'''.split())


def outlet(url: str) -> str:
    host = (urlparse(url or '').hostname or '').replace('www.', '')
    for dom, name in OUTLETS.items():
        if host == dom or host.endswith('.' + dom):
            return name
    return host or 'bài báo'


def sig(text: str) -> set[str]:
    return {w for w in normalize(text).split() if len(w) > 2 and w not in STOP}


def _when(row):
    from tf_news import parse_date
    return parse_date(row.get('published')) or parse_date(row.get('first_seen'))


def related_articles(tid: int, limit: int = 3) -> list[dict]:
    """Bài của báo KHÁC về cùng sự kiện (±4 ngày): chung nhiều từ khoá tiêu đề và tên riêng."""
    with connect() as db:
        me = db.execute('SELECT * FROM trends WHERE id=?', (tid,)).fetchone()
        if not me:
            return []
        me = dict(me)
        rows = [dict(r) for r in db.execute('SELECT * FROM trends WHERE id!=? ORDER BY id DESC LIMIT 3000', (tid,))]
    mine = sig(me['title'] + ' ' + (me.get('title_vi') or ''))
    my_names = {normalize(n) for n in proper_names(me['title'] + '. ' + (me.get('title_vi') or ''))}
    t0 = _when(me)
    my_outlet = outlet(me['link'])
    scored = []
    for r in rows:
        if outlet(r['link']) == my_outlet:
            continue
        t1 = _when(r)
        if t0 and t1 and abs(t0 - t1) > timedelta(days=4):
            continue
        theirs = sig(r['title'] + ' ' + (r.get('title_vi') or ''))
        common = mine & theirs
        if not mine or not theirs:
            continue
        overlap = len(common) / min(len(mine), len(theirs))
        names = my_names & {normalize(n) for n in proper_names(r['title'] + '. ' + (r.get('title_vi') or ''))}
        if (len(common) >= 4 and overlap >= 0.45) or (len(names) >= 2 and len(common) >= 3):
            scored.append((len(names), overlap, len(common), r))
    scored.sort(key=lambda x: x[:3], reverse=True)
    out, seen = [], set()
    for *_, r in scored:
        o = outlet(r['link'])
        if o in seen:
            continue
        seen.add(o)
        out.append({'url': r['link'], 'title': r.get('title_vi') or r['title'], 'outlet': o, 'trend_id': r['id']})
        if len(out) >= limit:
            break
    return out


MAX_SOURCES = int(os.getenv('ARTICLE_MAX_SOURCES', '6'))


def same_event(title_a: str, title_b: str) -> bool:
    a, b = sig(title_a), sig(title_b)
    if not a or not b:
        return False
    common = a & b
    names = {normalize(n) for n in proper_names(title_a + '.')} & {normalize(n) for n in proper_names(title_b + '.')}
    return (len(common) >= 4 and len(common) / min(len(a), len(b)) >= 0.45) or (len(names) >= 2 and len(common) >= 3) \
        or (len(names) >= 2 and len(common & names) >= 1 and len(common) >= 2)


def parse_bing_rss(xml_bytes: bytes) -> list[dict]:
    import xml.etree.ElementTree as ET
    from urllib.parse import parse_qs
    out = []
    for item in ET.fromstring(xml_bytes).iter('item'):
        link = (item.findtext('link') or '').strip()
        q = parse_qs(urlparse(link).query)
        real = (q.get('url') or [link])[0]
        if real.startswith('http'):
            out.append({'url': real, 'title': (item.findtext('title') or '').strip(), 'outlet': outlet(real),
                        'published': item.findtext('pubDate') or ''})
    return out


def search_coverage(trend: dict, limit: int = 5) -> list[dict]:
    """Other outlets' articles about the SAME event via Bing News RSS (vi + en). Only those articles'
    own photos are used later — search results themselves are never shown."""
    from urllib.parse import quote_plus
    titles = [t for t in (trend.get('title_vi'), trend.get('title')) if t]
    names = []
    for t in titles:
        for n in proper_names(t + '.'):
            if normalize(n) not in {normalize(x) for x in names}:
                names.append(n)
    queries = [titles[0][:120]]
    if len(names) >= 2:
        queries.append(' '.join(names[:4]))
    found, seen = [], {outlet(trend['link'])}
    for q in queries:
        for lang in ('vi', 'en'):
            url = f'https://www.bing.com/news/search?q={quote_plus(q)}&format=rss&setlang={lang}'
            try:
                _, raw, _ = M.safe_get(url, browser=True, max_bytes=2_000_000, accept='application/rss+xml')
            except Exception as exc:
                logging.warning('Bing News search failed: %s', exc)
                continue
            for r in parse_bing_rss(raw):
                if r['outlet'] in seen or not any(same_event(t, r['title']) for t in titles):
                    continue
                seen.add(r['outlet'])
                found.append(r)
                if len(found) >= limit:
                    return found
    return found


def images_of(url: str) -> tuple[str, list[dict]]:
    if M.FIXTURE_DIR:
        data = json.loads((Path(M.FIXTURE_DIR) / 'articles.json').read_text(encoding='utf-8'))
        art = data.get(url) or {}
        return art.get('title', ''), [dict(i, url='fixture://' + i['file']) for i in art.get('images', [])]
    art = M.extract_article(url)
    return art['title'], art['images']


TARGET_SECONDS_PER_PHOTO = 7.0      # ~1 new photo every 7 s of narration
MIN_VERIFIED = int(os.getenv('MIN_VERIFIED_PHOTOS', '3'))
MAX_VERIFY = int(os.getenv('MAX_PHOTO_CHECKS', '36'))   # AI checks per video (time/cost cap)
WEB_MIN_SIDE = int(os.getenv('WEB_MIN_IMAGE_SIDE', '480'))


def search_queries(trend: dict, story: list[dict], article: dict, use_llm: bool, cancel=None) -> list[str]:
    """3–5 web image queries about the story's main subjects (LLM, with a heuristic fallback)."""
    title = trend.get('title_vi') or trend['title']
    out: list[str] = []
    if use_llm:
        try:
            import tf_llm
            data = tf_llm.chat_json(
                'Viết 4 truy vấn tìm ẢNH trên Google/Bing cho tin dưới đây, mỗi truy vấn 2–6 từ, về các đối tượng chính '
                '(người, sản phẩm, trò chơi, tổ chức, địa điểm, sự kiện) — có thể tiếng Anh hoặc tiếng Việt. '
                'Không thêm đối tượng không có trong tin. Chỉ JSON: {"queries": ["..."]}\n'
                f'TIÊU ĐỀ: {title}\nTÓM TẮT: {article.get("text", "")[:1200]}', max_tokens=200, cancel=cancel, temperature=0.2)
            out = [str(q).strip()[:80] for q in (data.get('queries') or []) if str(q).strip()]
        except Exception as exc:
            if type(exc).__name__ == 'JobCancelled':
                raise
            logging.warning('LLM image queries failed: %s', exc)
    names = [e['en'] for e in story][:4]
    if len(names) >= 2:
        out.append(f'{names[0]} {names[1]}')
    out += names[:3]
    out.append(' '.join(title.split()[:10]))
    seen, uniq = set(), []
    for q in out:
        k = normalize(q)
        if k and k not in seen:
            seen.add(k)
            uniq.append(q)
    return uniq[:6]


PERSON_TYPES = ('person', 'name')
MAX_SUBJECTS = 7
PER_SUBJECT_RESULTS = 6       # web results looked at per subject
PER_SUBJECT_GOAL = 2          # stop searching a subject once this many photos of it are confirmed


def build_subjects(per: list[list[dict]], story: list[dict]) -> list[dict]:
    """Subjects an editor would picture: each sentence's main subject (+1 secondary), then the story's subjects."""
    subjects: list[dict] = []

    def find(e):
        keys = {normalize(e['vi']), normalize(e['en'])} - {''}
        for s in subjects:
            if keys & s['keys'] or any(k in sk or sk in k for k in keys for sk in s['keys'] if len(k) > 3 and len(sk) > 3):
                return s
        s = {'name': e['vi'] or e['en'],
             'vi': e['vi'], 'en': e['en'], 'type': e['type'], 'keys': set(keys), 'primary_in': [], 'secondary_in': []}
        subjects.append(s)
        return s

    for i, ents in enumerate(per):
        own = [e for e in ents if not e.get('inherited')] or ents
        for rank, e in enumerate(own[:2]):
            s = find(e)
            (s['primary_in'] if rank == 0 else s['secondary_in']).append(i)
            if e['type'] in PERSON_TYPES and s['type'] not in PERSON_TYPES:
                s['type'] = e['type']
    for e in story:
        find(e)
    subjects.sort(key=lambda s: (-len(s['primary_in']), -len(s['secondary_in'])))
    subjects = subjects[:MAX_SUBJECTS]
    for k, s in enumerate(subjects):
        s['id'] = k
    return subjects


def build_article_plan(tid: int, timings: dict, headline: str, settings: dict, *, cancel=None, progress=None,
                       use_llm=True) -> dict:
    """Editor-style photo selection.
    1) Decide each sentence's subject (who/what is being talked about).
    2) A = the original article's photos; B = web image search PER SUBJECT (Donald Glover, Stephen Glover, PUBG, Netflix…).
    3) The AI says which subjects each photo shows (people: only with page/visible-text evidence, never by face).
    4) Each sentence gets a photo of its main subject → secondary subject → broader story photo (labelled as such).
    Photos that fail the AI check are never used."""
    import tf_imagesearch
    import tf_verify
    log_lines: list[str] = []

    def log(msg, p=None):
        log_lines.append(msg)
        if progress:
            progress(msg, p)

    segs = timings['segments']
    texts = [s['text'] for s in segs]
    duration = segs[-1]['end'] if segs else 0
    with connect() as db:
        trend = dict(db.execute('SELECT * FROM trends WHERE id=?', (tid,)).fetchone())
    article = M.article_text(tid)
    title = trend.get('title_vi') or trend['title']
    summary = trend.get('summary_vi') or trend.get('summary') or article.get('text', '')[:700]
    chain = tf_verify.backends(settings)
    state = {'i': 0}
    how = chain[0]
    used_verifiers = [how[0]]
    batch = tf_verify.batch_size(how)
    log('AI kiểm tra ảnh: ' + ' → '.join(f"{n}{' (' + c['model'] + ')' if 'model' in c else ''}" for n, c in chain), 0.01)

    def switch(reason) -> bool:
        nonlocal how, batch
        if state['i'] + 1 >= len(chain):
            return False
        state['i'] += 1
        how = chain[state['i']]
        batch = tf_verify.batch_size(how)
        used_verifiers.append(how[0])
        log(f"⇄ {chain[state['i'] - 1][0]} không dùng được ({str(reason)[:90]}) → chuyển sang "
            f"{how[0]}{' (' + how[1].get('model', '') + ')' if how[1].get('model') else ''}, kiểm tiếp")
        return True

    # ---- 1. who/what each sentence is about
    per, story, ent_source, _ = M.build_entities(texts, article, use_llm=use_llm, cancel=cancel)
    subjects = build_subjects(per, story)
    subj_payload = [{'name': s['name'], 'type': s['type']} for s in subjects]
    log('Chủ thể theo câu: ' + ' · '.join(f"{s['name']} (câu {', '.join(str(i + 1) for i in s['primary_in']) or '—'})"
                                          for s in subjects), 0.03)

    verified: list[tuple[dict, dict, dict]] = []
    checked, hashes, rejected = 0, [], []
    seen_urls: set[str] = set()
    fails = {'n': 0}
    last_exc = None

    def fetch(c, min_side):
        if cancel:
            cancel()
        if c['url'] in seen_urls:
            return None
        seen_urls.add(c['url'])
        try:
            row = M.download(c, min_side=min_side)
        except Exception as exc:
            if type(exc).__name__ == 'JobCancelled':
                raise
            rejected.append(f"{c['group']} · {c['provider']}: {str(exc)[:60]}")
            return None
        if any(M.hamming(row['dhash'], h) <= 8 for h in hashes):
            return None
        hashes.append(row['dhash'])
        return c, row

    def provenance_subjects(c, v) -> list[int]:
        """People are confirmed by evidence, not faces: the photo was found for that name AND the page/caption names
        them AND the AI says it is a real photo of a person/event."""
        extra = []
        hint = normalize(f"{c.get('description', '')} {c.get('title', '')}")
        for s in subjects:
            if s['type'] not in PERSON_TYPES or s['id'] in v['subjects']:
                continue
            named = any(len(k) > 3 and f' {k} ' in f' {hint} ' for k in s['keys'])
            searched = c.get('searched_for') == s['id'] or c['group'] == 'A'
            if named and searched and v['kind'] in ('person_photo', 'event', ''):
                extra.append(s['id'])
        return extra

    def judge(items):
        nonlocal checked, last_exc
        items = items[:max(0, MAX_VERIFY - checked)]
        if not items:
            return
        checked += len(items)
        verdicts = None
        while True:
            try:
                verdicts = tf_verify.verify_many(
                    [M.MEDIA_DIR / row['local_filename'] for _, row in items], title=title, summary=summary, sentences=texts,
                    hints=[f"{c.get('description', '')} | {c.get('title', '')}" for c, _ in items], how=how, cancel=cancel,
                    fixture_keys=[c['url'].replace('fixture://', '') for c, _ in items], subjects=subj_payload)
                break
            except tf_verify.VerifyUnavailable as exc:
                if switch(exc):
                    continue
                raise
            except Exception as exc:
                if type(exc).__name__ == 'JobCancelled':
                    raise
                if fails['n'] >= 1 and switch(exc):
                    fails['n'] = 0
                    continue
                last_exc = exc
                break
        if verdicts is None:
            fails['n'] += 1
            for c, _ in items:
                rejected.append(f"{c['group']} · {c['provider']}: AI không kiểm tra được ({str(last_exc)[:70]}) — bỏ qua")
            log(f"! AI không phản hồi cho {len(items)} ảnh ({str(last_exc)[:60]}) — bỏ các ảnh này, kiểm tiếp")
            if fails['n'] >= 2:
                raise tf_verify.VerifyUnavailable(f'AI kiểm tra ảnh không phản hồi {fails["n"]} lượt liên tiếp '
                                                  f'(mạng chậm/bị giới hạn). Lỗi cuối: {str(last_exc)[:120]}')
            return
        fails['n'] = 0
        for (c, row), v in zip(items, verdicts):
            v['evidence'] = {k: 'ai' for k in v['subjects']}
            for sid in provenance_subjects(c, v):
                v['subjects'].append(sid)
                v['evidence'][sid] = 'nguồn ảnh ghi tên'
            c['verdict'] = v
            names = [subjects[k]['name'] for k in v['subjects']]
            tag = f"{c['group']} · {c['provider']}"
            usable_photo = v['relevant'] or (v['subjects'] and v['kind'] not in ('meme', 'document', 'other'))
            v['accepted'] = bool(usable_photo)
            if usable_photo:
                verified.append((c, row, v))
                log(f"✓ {tag}: {v['shows']} → chủ thể: {', '.join(names) or 'chung của tin'} ({v['score']:.0f}/10)")
            else:
                rejected.append(f"{tag}: {v['shows'] or 'không rõ'} — {v['reason'] or 'không liên quan'} ({v['score']:.0f}/10)")
                log(f"✕ {tag}: {v['shows']} — {v['reason']}")

    def have_subject(sid):
        return sum(1 for _, _, v in verified if sid in v['subjects'])

    try:
        # ---- 2A. the original article's own photos
        src_outlet = outlet(trend['link'])
        log('A · Đọc ảnh trong bài gốc…', 0.05)
        try:
            page_title, imgs = images_of(trend['link'])
        except Exception as exc:
            page_title, imgs = title, []
            log(f'Không đọc được ảnh bài gốc: {str(exc)[:80]}')
        pool_a = [x for x in (fetch({'url': im['url'], 'page_url': trend['link'], 'provider': src_outlet,
                                     'title': page_title or title, 'description': im.get('caption', ''),
                                     'creator': src_outlet, 'group': 'A',
                                     'license': f'Ảnh của {src_outlet} — ghi nguồn khi đăng', 'rights_class': 'publisher',
                                     'rank': k, 'og': 'đại diện' in im.get('note', '')}, ARTICLE_MIN_SIDE)
                              for k, im in enumerate(imgs)) if x]
        for i in range(0, len(pool_a), batch):
            judge(pool_a[i:i + batch])
            log(f'A · đã kiểm {min(len(pool_a), i + batch)}/{len(pool_a)} ảnh bài gốc', 0.05 + 0.2 * min(1, (i + batch) / max(1, len(pool_a))))
        n_a = len(verified)

        # ---- 2B. web search PER SUBJECT (people first: an editor shows the person being talked about)
        engine = ''
        todo = [s for s in subjects if (s['primary_in'] or s['secondary_in']) and have_subject(s['id']) < PER_SUBJECT_GOAL]
        todo.sort(key=lambda s: (s['type'] not in PERSON_TYPES, -len(s['primary_in'])))
        for si, s in enumerate(todo):
            if checked >= MAX_VERIFY:
                log('B · Đã dùng hết lượt kiểm ảnh cho video này.')
                break
            query = s['en'] or s['vi']
            log(f'B · Tìm ảnh cho "{s["name"]}" ({s["type"]}): {query}', 0.25 + 0.5 * si / max(1, len(todo)))
            try:
                engine, results = tf_imagesearch.search(query, settings)
            except Exception as exc:
                log(f'B · tìm "{query}" lỗi: {str(exc)[:80]}')
                continue
            pending, per_domain = [], {}
            for r in results[:PER_SUBJECT_RESULTS * 2]:
                if len(pending) >= PER_SUBJECT_RESULTS:
                    break
                d = r.get('outlet') or 'web'
                if per_domain.get(d, 0) >= 2:
                    continue
                got = fetch({'url': r['url'], 'page_url': r.get('page_url') or '', 'provider': d, 'title': r.get('title', ''),
                             'description': r.get('title', ''), 'creator': d, 'group': 'B', 'searched_for': s['id'],
                             'license': f'Ảnh từ {d} — ghi nguồn khi đăng', 'rights_class': 'publisher',
                             'rank': 1000 + si * 100, 'og': False}, WEB_MIN_SIDE)
                if got:
                    per_domain[d] = per_domain.get(d, 0) + 1
                    pending.append(got)
            for i in range(0, len(pending), batch):
                if have_subject(s['id']) >= PER_SUBJECT_GOAL:
                    break
                judge(pending[i:i + batch])
            log(f'B · "{s["name"]}": {have_subject(s["id"])} ảnh đúng chủ thể · đã kiểm {checked}/{MAX_VERIFY}')
        log(f'Tổng: {len(verified)} ảnh đạt (A: {n_a}, B: {len(verified) - n_a}) · {len(rejected)} ảnh bị loại.', 0.8)
    except Exception as exc:
        exc.payload = {'plan_report': {'log': log_lines[-60:], 'rejected_examples': rejected[:30], 'verified': len(verified),
                                       'checked': checked}}
        raise

    # ---- 3. per sentence: main subject → secondary subject → broader story photo (labelled)
    subject_of = {}
    for s in subjects:
        for i in s['primary_in']:
            subject_of.setdefault(i, [None, []])[0] = s['id']
        for i in s['secondary_in']:
            subject_of.setdefault(i, [None, []])[1].append(s['id'])
    media, out_segments, uses, prev = {}, [], {}, set()

    def pick(pool, i):
        def rank(x):
            c, row, v = x
            return (v['score'] + (1.0 if (i + 1) in v['sentences'] else 0) - 1.5 * uses.get(row['id'], 0)
                    + (0.3 if c['group'] == 'A' else 0) + (0.5 if v['kind'] == 'person_photo' else 0))
        ordered = sorted(pool, key=rank, reverse=True)
        return next((x for x in ordered if x[1]['id'] not in prev), ordered[0] if ordered else None)

    for i, seg in enumerate(segs):
        if cancel:
            cancel()
        main, secondary = subject_of.get(i, [None, []])
        shots, chosen = [], None
        levels = ([('subject', main)] if main is not None else []) + [('secondary', sid) for sid in secondary]
        for level, sid in levels:
            pool = [x for x in verified if sid in x[2]['subjects']]
            if pool:
                chosen = (pick(pool, i), level, sid)
                break
        if not chosen and verified:
            pool = [x for x in verified if x[2]['relevant']] or verified
            chosen = (pick(pool, i), 'story', main)
        if chosen and chosen[0]:
            (c, row, v), level, sid = chosen
            sname = subjects[sid]['name'] if sid is not None else ''
            if level == 'story':
                hits = [f"liên quan rộng — chưa có ảnh {sname}" if sname else 'liên quan rộng']
            else:
                ev = v.get('evidence', {}).get(sid, 'ai')
                hits = [f"đúng chủ thể: {sname}" + (' (xác nhận qua nguồn ảnh ghi tên)' if ev != 'ai' else '')]
            shots.append(_shot(c, row, level, v['score'], hits, media))
            dur = seg['end'] - seg['start']
            if dur > 7.5 and level != 'story':
                alt = [x for x in verified if sid in x[2]['subjects'] and x[1]['id'] != row['id']]
                if alt:
                    c2, row2, v2 = pick(alt, i)
                    shots.append(_shot(c2, row2, level, v2['score'], hits, media))
        for sh in shots:
            uses[sh['media_id']] = uses.get(sh['media_id'], 0) + 1
        words = [w for w in timings.get('words', []) if w['seg'] == i]
        bounds = [seg['start']]
        if len(shots) == 2:
            mid_t = seg['start'] + (seg['end'] - seg['start']) / 2
            cut = min((w['start'] for w in words), key=lambda t: abs(t - mid_t), default=mid_t)
            bounds.append(cut if seg['start'] + 2 < cut < seg['end'] - 2 else mid_t)
        bounds.append(seg['end'])
        for k, sh in enumerate(shots):
            sh['start'], sh['end'] = round(bounds[k], 3), round(bounds[k + 1], 3)
        prev = {sh['media_id'] for sh in shots}
        out_segments.append({'index': i, 'text': seg['text'], 'start': seg['start'], 'end': seg['end'],
                             'subject': subjects[main]['name'] if main is not None else '',
                             'entities': [{'vi': e['vi'], 'en': e['en'], 'type': e['type']} for e in per[i]],
                             'shots': shots})
    sources = [{'url': trend['link'], 'title': title, 'outlet': src_outlet, 'primary': True}]
    for c, row, v in verified:
        if c['group'] == 'B' and c['provider'] not in {x['outlet'] for x in sources}:
            sources.append({'url': c.get('page_url') or c['url'], 'title': c.get('title', ''), 'outlet': c['provider'], 'primary': False})
    plan = {'version': 3, 'image_source': 'article', 'headline': headline, 'policy': settings.get('rights_policy'),
            'segments': out_segments, 'media': media, 'entity_source': ent_source, 'sources': sources,
            'subjects': [{'name': s['name'], 'type': s['type'], 'sentences': [i + 1 for i in s['primary_in']]} for s in subjects],
            'article_url': article.get('url'), 'article_source': article.get('source'),
            'verifier': '→'.join(used_verifiers), 'search_engine': engine}
    report = evaluate(plan, duration)
    report.update({'log': log_lines[-60:], 'rejected_examples': rejected[:20], 'candidates': checked,
                   'sources': sources, 'vision_model': '→'.join(used_verifiers), 'verified_a': n_a,
                   'verified_b': len(verified) - n_a, 'subjects': plan['subjects']})
    log('Xong kế hoạch ảnh.', 1.0)
    return {'plan': plan, 'report': report, 'passed': report['passed']}


def _shot(c, row, match, s, hits, media):
    entry = {k: row.get(k) for k in ('id', 'url', 'local_filename', 'width', 'height')}
    # Provenance comes from the article we took the photo from — never from an older cache entry.
    entry.update({k: c.get(k) for k in ('page_url', 'provider', 'title', 'creator', 'license', 'rights_class')})
    entry['credit'] = M.credit_line(c)
    entry['caption'] = (c.get('description') or '')[:200]
    entry['verdict'] = c.get('verdict')
    entry['ai_shows'] = (c.get('verdict') or {}).get('shows')
    entry['group'] = c.get('group')
    media[str(row['id'])] = entry
    return {'media_id': row['id'], 'match': match, 'score': round(s, 1), 'hits': hits,
            'note': ('Chú thích: ' + c['description'][:120]) if c.get('description') else ''}


def evaluate(plan: dict, duration: float) -> dict:
    issues, warnings, per_media = [], [], {}
    unverified = [k for k, m in plan['media'].items()
                  if not ((m.get('verdict') or {}).get('accepted') or (m.get('verdict') or {}).get('relevant'))]
    if unverified:
        issues.append('Có ảnh chưa qua kiểm tra AI — không dùng.')
    if len(plan['media']) < MIN_VERIFIED:
        issues.append(f'Chỉ có {len(plan["media"])} ảnh đạt kiểm tra AI (cần ít nhất {MIN_VERIFIED}). '
                      'Không xuất video với ảnh không liên quan — chọn tin khác hoặc bấm "Tìm ảnh lại".')
    missing = [s['index'] + 1 for s in plan['segments'] if not s['shots']]
    if missing and plan['media']:
        issues.append('Câu chưa có ảnh: ' + ', '.join(map(str, missing)))
    for s in plan['segments']:
        for sh in s['shots']:
            per_media[sh['media_id']] = per_media.get(sh['media_id'], 0) + sh['end'] - sh['start']
    top = max(per_media.values()) / duration if per_media and duration else 0
    broad = [s for s in plan['segments'] if s['shots'] and s['shots'][0]['match'] == 'story']
    if broad:
        warnings.append('Ảnh liên quan rộng (chưa có ảnh đúng chủ thể): ' +
                        '; '.join(f"câu {s['index'] + 1}" + (f" ({s.get('subject')})" if s.get('subject') else '') for s in broad))
    if per_media and len(per_media) < 5:
        warnings.append(f'Chỉ {len(per_media)} ảnh khác nhau; ảnh được lặp lại với góc cắt khác nhau.')
    if top > 0.4:
        warnings.append(f'Một ảnh chiếm {top:.0%} thời lượng.')
    matched = sum(1 for s in plan['segments'] if s['shots'] and s['shots'][0]['match'] in ('subject', 'secondary'))
    return {'passed': not issues, 'issues': issues, 'warnings': warnings, 'distinct_photos': len(per_media),
            'sentence_matched': matched, 'segments': len(plan['segments']), 'max_photo_share': round(top, 3)}
