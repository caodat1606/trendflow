"""AI kiểm tra ảnh: ảnh có thể hiện ĐÚNG nội dung tin không, và hợp với câu nào.

Không ảnh nào được dùng nếu chưa qua bước này (kể cả ảnh của chính bài gốc — bài Tuổi Trẻ về Netflix/PUBG
có cả ảnh Spider-Man/Naruto). Backend: Gemini (nếu có key, nhanh) hoặc model nhìn ảnh của Ollama trên máy.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from pathlib import Path

import requests
from PIL import Image

import tf_llm

ACCEPT_SCORE = int(os.getenv('IMAGE_ACCEPT_SCORE', '6'))
GEMINI_VISION_MODELS = ['gemini-2.5-flash', 'gemini-flash-latest', 'gemini-2.0-flash']
_OK_MODEL: dict = {}


class VerifyUnavailable(RuntimeError):
    """Configuration problem (no AI, key rejected, no model) — stop the job with instructions."""


class VerifyTransient(RuntimeError):
    """Network/timeout/rate limit on ONE image — skip that image (never use it unverified), keep going."""


GEMINI_TIMEOUT = (10, float(os.getenv('GEMINI_READ_TIMEOUT', '90')))
GEMINI_BATCH = int(os.getenv('GEMINI_BATCH', '6'))              # photos per request (free tier: few requests/minute)
GEMINI_MIN_INTERVAL = float(os.getenv('GEMINI_MIN_INTERVAL', '7'))   # seconds between requests (~8/minute)
_LAST_CALL = {'t': 0.0}


def _retry_after(r) -> tuple[float, bool]:
    """Seconds Gemini asks us to wait, and whether the DAILY quota is exhausted."""
    try:
        err = r.json().get('error', {})
    except ValueError:
        return 20.0, False
    daily = 'perday' in json.dumps(err).lower().replace('_', '').replace(' ', '')
    wait = 20.0
    for d in err.get('details', []):
        m = re.match(r'([\d.]+)s', str(d.get('retryDelay', '')))
        if m:
            wait = float(m.group(1))
    return min(60.0, wait + 1), daily
GEMINI_RETRY_DELAYS = (0, 3, 8, 15)


def _subject_block(subjects) -> str:
    if not subjects:
        return ''
    lines = '\n'.join(f"{k + 1}) {x['name']} ({x['type']})" for k, x in enumerate(subjects))
    return f"""CHỦ THỂ CẦN NHẬN DIỆN TRONG ẢNH:
{lines}
"""


RULES = """Tiêu chí cho từng ảnh:
- "subjects": số thứ tự các CHỦ THỂ ảnh thực sự thể hiện. Trò chơi/sản phẩm/tổ chức: thấy rõ (hình game, logo,
  biển hiệu, sản phẩm). NGƯỜI: chỉ đánh dấu khi gợi ý trang/chữ trong ảnh nêu tên người đó VÀ ảnh là ảnh người thật
  (chân dung, sự kiện); không đoán danh tính chỉ bằng khuôn mặt.
- "kind": person_photo | game_art | logo_brand | product | event | place | document | meme | other.
- "relevant": true nếu ảnh dùng được cho TIN (đúng chủ thể của tin). false nếu ảnh về phim/nhân vật/người/sản phẩm KHÁC
  (ví dụ tin PUBG mà ảnh Spider-Man, Naruto), meme, ảnh ghép lạ, ảnh chụp màn hình toàn chữ, quảng cáo, ảnh mờ/vỡ.
  Logo/biển hiệu của một tổ chức có trong danh sách chủ thể là chấp nhận được.
- "score" 0–10: mức phù hợp; "sentences": số câu ảnh minh hoạ hợp nhất (tối đa 4)."""


def prompt(title: str, summary: str, sentences: list[str], hint: str, subjects=None) -> str:
    numbered = '\n'.join(f'{i + 1}. {s}' for i, s in enumerate(sentences))
    return f'''Bạn là biên tập viên ảnh của một bản tin video. Nhìn ẢNH và trả lời bằng JSON.
TIN: {title[:300]}
TÓM TẮT: {summary[:700]}
LỜI THOẠI THEO CÂU:
{numbered}
{_subject_block(subjects)}GỢI Ý từ trang chứa ảnh (có thể SAI, chỉ tham khảo): {hint[:300]}
{RULES}
Chỉ trả JSON: {{"relevant": true, "score": 0, "subjects": [1], "kind": "person_photo", "shows": "ảnh thể hiện gì (ngắn)", "sentences": [1], "reason": "ngắn"}}'''


def _jpeg(path: Path, side=768) -> bytes:
    with Image.open(path) as im:
        im = im.convert('RGB')
        im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=82)
        return buf.getvalue()


def _normalise(data: dict, n_sentences: int, backend: str, subjects=None) -> dict:
    try:
        score = float(data.get('score') or 0)
    except (TypeError, ValueError):
        score = 0.0
    sents = []
    for x in data.get('sentences') or []:
        try:
            k = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= k <= n_sentences and k not in sents:
            sents.append(k)
    subj_ids = []
    names = [tf_llm_norm(x['name']) for x in (subjects or [])]
    for x in list(data.get('subjects') or []) + list(data.get('subject_names') or []):
        idx = None
        if isinstance(x, (int, float)) or (isinstance(x, str) and x.strip().isdigit()):
            idx = int(x) - 1
        elif isinstance(x, str):
            nx = tf_llm_norm(x)
            idx = next((k for k, n in enumerate(names) if n and (n == nx or n in nx or nx in n)), None)
        if idx is not None and 0 <= idx < len(names) and idx not in subj_ids:
            subj_ids.append(idx)
    relevant = data.get('relevant') is True and score >= ACCEPT_SCORE
    return {'relevant': relevant, 'score': score, 'sentences': sents[:4], 'subjects': subj_ids,
            'kind': str(data.get('kind') or '')[:20].lower(), 'shows': str(data.get('shows') or '')[:160],
            'reason': str(data.get('reason') or '')[:200], 'by': backend}


def tf_llm_norm(text: str) -> str:
    from tf_text import normalize
    return normalize(text)


def _wait(seconds, cancel=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel:
            cancel()
        time.sleep(0.2)


def _gemini(img, text: str, key: str, cancel=None) -> dict:
    imgs = img if isinstance(img, list) else [img]
    parts = [{'inline_data': {'mime_type': 'image/jpeg', 'data': base64.b64encode(b).decode()}} for b in imgs]
    base = {'contents': [{'parts': parts + [{'text': text}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0,
                                 'thinkingConfig': {'thinkingBudget': 0}}}   # no "thinking": much faster for a yes/no check
    models = [_OK_MODEL['m']] if 'm' in _OK_MODEL else GEMINI_VISION_MODELS
    last = ''
    for model in models:
        body = json.loads(json.dumps(base))
        for attempt, delay in enumerate(GEMINI_RETRY_DELAYS):
            _wait(max(delay, GEMINI_MIN_INTERVAL - (time.monotonic() - _LAST_CALL['t'])), cancel)
            _LAST_CALL['t'] = time.monotonic()
            try:
                r = requests.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                                  params={'key': key}, json=body, timeout=GEMINI_TIMEOUT)
            except requests.RequestException as exc:     # timeout / connection reset: transient, retry
                last = f'{type(exc).__name__}'
                continue
            if r.status_code == 429:
                wait, daily = _retry_after(r)
                if daily:
                    _GEMINI_EXHAUSTED['until'] = time.time() + 3 * 3600
                    raise VerifyUnavailable('Hết hạn mức Gemini miễn phí trong ngày. Chờ sang ngày mới, dùng AI trên máy '
                                            '(ollama pull qwen2.5vl:7b) hoặc bật thanh toán cho key Gemini.')
                last = f'Gemini 429 (chờ {wait:.0f}s)'
                _wait(wait, cancel)
                continue
            if r.status_code >= 500:
                last = f'Gemini {r.status_code}'
                _wait(min(30, 6 * (attempt + 1)), cancel)
                continue
            if r.status_code == 404:
                last = f'không có model {model}'
                break
            if r.status_code in (400, 403) and 'API key' in r.text:
                raise VerifyUnavailable('Gemini từ chối API key — kiểm tra key trong Cài đặt.')
            if r.status_code == 400 and 'thinking' in r.text.lower() and 'thinkingConfig' in body['generationConfig']:
                body['generationConfig'].pop('thinkingConfig')   # model without thinking control
                continue
            if r.status_code != 200:
                raise VerifyTransient(f'Gemini lỗi {r.status_code}: {r.text[:150]}')
            _OK_MODEL['m'] = model
            try:
                raw = r.json()['candidates'][0]['content']['parts'][0]['text']
            except (KeyError, IndexError, ValueError):
                raise VerifyTransient('Gemini không trả kết quả cho ảnh này: ' + r.text[:120])
            return tf_llm.parse_json(raw)
        if last.startswith('không có model'):
            continue
        raise VerifyTransient(f'Gemini không phản hồi sau {len(GEMINI_RETRY_DELAYS)} lần ({last})')
    raise VerifyUnavailable('Không tìm được model Gemini nhìn ảnh dùng được với key này (' + last + ')')


_GEMINI_EXHAUSTED = {'until': 0.0}   # daily quota hit: 'auto' goes straight to the local AI for a while


def available(settings: dict) -> dict:
    key = (settings.get('gemini_key') or os.getenv('GEMINI_API_KEY') or '').strip()
    return {'gemini': bool(key), 'gemini_exhausted': time.time() < _GEMINI_EXHAUSTED['until'],
            'ollama': tf_llm.status(max_age=30).get('vision_model')}


def backends(settings: dict) -> list[tuple[str, dict]]:
    """Ordered verifiers to try. auto = Gemini (if keyed and not out of quota) then the local AI."""
    fixture = os.getenv('TRENDFLOW_FIXTURE_DIR')
    if fixture and (Path(fixture) / 'verify.json').is_file():
        return [('fixture', {'file': Path(fixture) / 'verify.json'})]
    choice = settings.get('image_verifier', 'auto')
    key = (settings.get('gemini_key') or os.getenv('GEMINI_API_KEY') or '').strip()
    out = []
    if choice in ('auto', 'gemini') and key and (choice == 'gemini' or time.time() >= _GEMINI_EXHAUSTED['until']):
        out.append(('gemini', {'key': key}))
    if choice in ('auto', 'ollama'):
        vm = tf_llm.status().get('vision_model')
        if vm:
            out.append(('ollama', {'model': vm}))
    if out:
        return out
    if choice == 'ollama':
        raise VerifyUnavailable('Chưa thấy AI nhìn ảnh trên máy. Mở PowerShell chạy: ollama pull qwen2.5vl:7b (Ollama phải đang chạy).')
    if choice == 'gemini':
        raise VerifyUnavailable('Chưa nhập Gemini API key trong Cài đặt.')
    raise VerifyUnavailable('Chưa có AI kiểm tra ảnh. Cách 1 (trên máy, miễn phí): mở PowerShell chạy '
                            '`ollama pull qwen2.5vl:7b`. Cách 2 (nhanh hơn): nhập Gemini API key trong Cài đặt.')


def verify(path: Path, *, title: str, summary: str, sentences: list[str], hint: str, how: tuple, cancel=None,
           fixture_key: str = '', subjects=None) -> dict:
    name, cfg = how
    if cancel:
        cancel()
    if name == 'fixture':
        data = json.loads(cfg['file'].read_text(encoding='utf-8')).get(fixture_key, {})
        return _normalise(data, len(sentences), 'fixture', subjects)
    text = prompt(title, summary, sentences, hint, subjects)
    img = _jpeg(path)
    if name == 'gemini':
        data = _gemini(img, text, cfg['key'], cancel)
    else:
        data = tf_llm.parse_json(tf_llm.chat(text, model=cfg['model'], images=[img], json_mode=True, max_tokens=300,
                                             temperature=0, cancel=cancel, system='Bạn là biên tập viên ảnh nghiêm khắc.'))
    return _normalise(data if isinstance(data, dict) else {}, len(sentences), name, subjects)


def prompt_many(title: str, summary: str, sentences: list[str], hints: list[str], subjects=None) -> str:
    numbered = '\n'.join(f'{i + 1}. {s}' for i, s in enumerate(sentences))
    lines = '\n'.join(f'Ảnh {i + 1}: {h[:200]}' for i, h in enumerate(hints))
    return f'''Bạn là biên tập viên ảnh của một bản tin video. Có {len(hints)} ảnh, đánh số Ảnh 1..{len(hints)} theo đúng thứ tự gửi.
TIN: {title[:300]}
TÓM TẮT: {summary[:700]}
LỜI THOẠI THEO CÂU:
{numbered}
{_subject_block(subjects)}GỢI Ý từ trang chứa từng ảnh (có thể SAI):
{lines}
Đánh giá TỪNG ảnh độc lập.
{RULES}
Chỉ trả JSON: {{"images": [{{"image": 1, "relevant": true, "score": 0, "subjects": [1], "kind": "person_photo", "shows": "...", "sentences": [1], "reason": "..."}}]}}'''


def batch_size(how: tuple) -> int:
    return GEMINI_BATCH if how[0] == 'gemini' else 1


def verify_many(paths: list[Path], *, title: str, summary: str, sentences: list[str], hints: list[str], how: tuple,
                cancel=None, fixture_keys: list[str] | None = None, subjects=None) -> list[dict]:
    """Verdicts for several photos. Gemini: ONE request for the whole batch (free tier allows few requests/minute)."""
    keys = fixture_keys or [''] * len(paths)
    if how[0] != 'gemini' or len(paths) == 1:
        return [verify(p, title=title, summary=summary, sentences=sentences, hint=h, how=how, cancel=cancel, fixture_key=k,
                       subjects=subjects) for p, h, k in zip(paths, hints, keys)]
    data = _gemini([_jpeg(p, 640) for p in paths], prompt_many(title, summary, sentences, hints, subjects), how[1]['key'], cancel)
    items = data.get('images') if isinstance(data, dict) else data
    by_index = {}
    for k, it in enumerate(items if isinstance(items, list) else []):
        if isinstance(it, dict):
            try:
                idx = int(it.get('image') or k + 1)
            except (TypeError, ValueError):
                idx = k + 1
            by_index.setdefault(idx, it)
    out = []
    for i in range(len(paths)):
        it = by_index.get(i + 1)
        out.append(_normalise(it, len(sentences), 'gemini', subjects) if it else
                   {'relevant': False, 'score': 0, 'sentences': [], 'subjects': [], 'kind': '', 'shows': '',
                    'reason': 'AI không trả kết quả cho ảnh này', 'by': 'gemini'})
    return out


def backend(settings: dict) -> tuple[str, dict]:
    return backends(settings)[0]
