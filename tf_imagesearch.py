"""Tìm ảnh trên web theo từ khoá chính của tin (nhóm B). Kết quả CHƯA được dùng — mọi ảnh phải qua tf_verify.

- Mặc định: Bing Images (không cần key; đọc trang kết quả — không chính thức, có thể hỏng nếu Bing đổi giao diện).
- Tuỳ chọn: SerpAPI (kết quả Google Hình ảnh; cần key serpapi.com). Google Custom Search API đã đóng với người dùng mới.
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import tf_media as M


def domain(url: str) -> str:
    return (urlparse(url or '').hostname or '').replace('www.', '')


def parse_bing(html_text: str) -> list[dict]:
    out = []
    for m in re.finditer(r'class="iusc"[^>]*\sm="([^"]+)"', html_text):
        try:
            d = json.loads(html.unescape(m.group(1)))
        except ValueError:
            continue
        if d.get('murl', '').startswith('http'):
            out.append({'url': d['murl'], 'page_url': d.get('purl') or '', 'title': d.get('t') or '',
                        'outlet': domain(d.get('purl') or d['murl'])})
    return out


def bing_images(query: str, n: int = 20) -> list[dict]:
    url = (f'https://www.bing.com/images/search?q={quote_plus(query)}&form=HDRSC2&first=1'
           '&qft=+filterui:imagesize-large&setlang=vi')
    _, raw, _ = M.safe_get(url, browser=True, max_bytes=4_000_000, accept='text/html')
    return parse_bing(raw.decode('utf-8', 'replace'))[:n]


def parse_serpapi(data: dict) -> list[dict]:
    out = []
    for r in data.get('images_results', []):
        if str(r.get('original', '')).startswith('http'):
            out.append({'url': r['original'], 'page_url': r.get('link') or '', 'title': r.get('title') or '',
                        'outlet': r.get('source') or domain(r.get('link') or '')})
    return out


def serpapi_images(query: str, key: str, n: int = 20) -> list[dict]:
    data = M.get_json('https://serpapi.com/search.json', {'engine': 'google_images', 'q': query, 'hl': 'vi', 'gl': 'vn',
                                                          'api_key': key})
    return parse_serpapi(data)[:n]


def search(query: str, settings: dict, n: int = 20) -> tuple[str, list[dict]]:
    if M.FIXTURE_DIR and (Path(M.FIXTURE_DIR) / 'image_search.json').is_file():
        items = json.loads((Path(M.FIXTURE_DIR) / 'image_search.json').read_text(encoding='utf-8'))
        return 'fixture', [dict(i, url='fixture://' + i['file']) for i in items][:n]
    key = (settings.get('serpapi_key') or os.getenv('SERPAPI_KEY') or '').strip()
    if key:
        return 'Google Images (SerpAPI)', serpapi_images(query, key, n)
    return 'Bing Images', bing_images(query, n)
