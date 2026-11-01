"""Script writing (Ollama), measured-length revision and a deterministic fact check."""
from __future__ import annotations

import re

import tf_llm
from tf_text import clean_spaces, normalize, numbers_in, proper_names, split_sentences, word_count

DEFAULT_WPS = 3.4   # Vietnamese Edge Neural, words (syllables) per second at rate 0


def script_prompt(trend: dict, article: dict, target_seconds: int, target_words: int, angle: str = '') -> str:
    lo, hi = target_words - 12, target_words + 12
    return f'''Viết kịch bản video tin tức dọc (TikTok) dài khoảng {target_seconds} GIÂY khi đọc thành tiếng.
Ngách: {trend.get('category', '')}. {('Góc khai thác: ' + angle[:300]) if angle else ''}

YÊU CẦU BẮT BUỘC:
- "narration": lời đọc tiếng Việt tự nhiên, {lo}–{hi} từ (đếm theo âm tiết cách nhau bởi dấu cách).
  Câu ĐẦU TIÊN là câu mở hấp dẫn (hook) nêu ngay sự việc chính. Sau đó: chuyện gì xảy ra, ai/ở đâu/khi nào,
  chi tiết đáng chú ý, bối cảnh, ý nghĩa. Kết bằng một câu tóm ý, không kêu gọi like/follow.
- Giọng kể như người dẫn tin TikTok có duyên: văn NÓI tự nhiên, câu dài ngắn đan xen (phần lớn 12–25 từ),
  dùng từ nối (đáng chú ý, trong khi đó, không chỉ vậy, theo đó…) để mạch kể liền lạc.
  TUYỆT ĐỐI không liệt kê dữ kiện thành chuỗi câu cụt kiểu "A làm X. B là Y. Tối đa 100 người."
  Giải thích vì sao chi tiết đó đáng chú ý thay vì chỉ nêu.
- MỖI CÂU PHẢI CÓ MỘT THÔNG TIN MỚI lấy từ bài (tên, con số, chi tiết, trích dẫn, bối cảnh). Cấm câu sáo rỗng
  không có dữ kiện kiểu "Đây là bước chuyển mình lớn", "Dự án này là một phần trong kế hoạch phát triển",
  "gương mặt quen thuộc với nhiều tác phẩm nổi tiếng" (phải nêu tên tác phẩm). Không nhắc lại ý đã nói.
- Khi chuyển sang người/tổ chức/địa điểm khác, gọi rõ tên (để chọn đúng ảnh); không mở câu bằng đại từ mơ hồ.
- Ngày tháng viết "ngày 30 tháng 10", không viết 30-10 hay 30/10.
- CHỈ dùng dữ kiện có trong BÀI NGUỒN. Không bịa số liệu, ngày tháng, trích dẫn, tên người. Giữ nguyên tên riêng, số liệu.
- Tin quốc tế: viết lại bằng tiếng Việt tự nhiên, không dịch từng chữ; tên riêng giữ nguyên hoặc dùng tên Việt phổ biến.
- Viết số bằng chữ số như trong nguồn (ví dụ 2026, 1,5 triệu).
- "headline": tiêu đề trên màn hình tối đa 9 từ.
- "title": tiêu đề dự án; "caption": mô tả đăng kèm 1–2 câu; "hashtags": 3–5 hashtag;
  "verification": những điểm người duyệt nên kiểm tra lại (để trống nếu không có).
Chỉ trả JSON: {{"title":"","headline":"","narration":"","caption":"","hashtags":"","verification":""}}

TIÊU ĐỀ NGUỒN: {trend.get('title', '')}
TIÊU ĐỀ VIỆT HOÁ: {trend.get('title_vi') or ''}
NGÀY ĐĂNG: {trend.get('published') or 'chưa rõ'}
LINK: {trend.get('link', '')}
BÀI NGUỒN:
{article.get('text', '')[:9000]}'''


def revise_prompt(data: dict, article: dict, current_words: int, measured: float, target_words: int) -> str:
    direction = 'NGẮN HƠN' if target_words < current_words else 'DÀI HƠN (bổ sung chi tiết CÓ TRONG NGUỒN)'
    return f'''Lời đọc hiện có {current_words} từ, đọc thật mất {measured:.1f} giây. Cần 45–60 giây.
Hãy viết lại "narration" {direction}, khoảng {target_words} từ (±8). Giữ câu mở đầu hấp dẫn, giữ đúng dữ kiện,
không lặp ý, không thêm thông tin ngoài nguồn. Văn nói tự nhiên, câu liền mạch có từ nối, không liệt kê câu cụt.
Mỗi câu một thông tin mới; nếu cần dài hơn thì lấy THÊM chi tiết từ bài nguồn, tuyệt đối không thêm câu sáo rỗng.
Trả lại toàn bộ JSON cùng các khoá.
JSON HIỆN TẠI: {{"title":{_q(data['title'])},"headline":{_q(data['headline'])},"narration":{_q(data['narration'])},"caption":{_q(data['caption'])},"hashtags":{_q(data['hashtags'])},"verification":{_q(data['verification'])}}}
BÀI NGUỒN:
{article.get('text', '')[:8000]}'''


def _q(s):
    import json
    return json.dumps(str(s or ''), ensure_ascii=False)


def clean_result(d) -> dict:
    if not isinstance(d, dict):
        raise tf_llm.LLMError('AI trả về sai cấu trúc.')
    out = {k: clean_spaces(str(d.get(k) or ''))[:limit] for k, limit in
           (('title', 250), ('headline', 120), ('narration', 4000), ('caption', 600), ('hashtags', 300), ('verification', 1500))}
    out['narration'] = re.sub(r'^\s*(hook|lời thoại|narration)\s*[:：]\s*', '', out['narration'], flags=re.I)
    if word_count(out['narration']) < 40:
        raise tf_llm.LLMError('AI viết lời thoại quá ngắn hoặc rỗng.')
    if not out['title']:
        out['title'] = (split_sentences(out['narration']) or [''])[0][:120]
    if not out['headline']:
        out['headline'] = out['title'][:80]
    return out


def generate(trend: dict, article: dict, target_seconds: int, wps: float, cancel=None, angle='') -> dict:
    target_words = int(round(target_seconds * wps))
    data = tf_llm.chat_json(script_prompt(trend, article, target_seconds, target_words, angle),
                            max_tokens=1600, cancel=cancel, temperature=0.4)
    return clean_result(data)


def revise_length(data: dict, article: dict, measured: float, target_seconds: int, cancel=None) -> dict:
    words = word_count(data['narration'])
    wps = words / max(1.0, measured)
    target_words = int(round(target_seconds * wps))
    revised = clean_result(tf_llm.chat_json(revise_prompt(data, article, words, measured, target_words),
                                            max_tokens=1600, cancel=cancel, temperature=0.3))
    return revised


def fact_check(narration: str, source_text: str) -> dict:
    """Deterministic: every number and proper name in the narration should appear in the source."""
    src_norm = ' ' + normalize(source_text) + ' '
    src_numbers = numbers_in(source_text)
    unsupported_numbers = sorted(n for n in numbers_in(narration) if n not in src_numbers
                                 and n.replace('.', '') not in {x.replace('.', '') for x in src_numbers})
    unsupported_names = []
    for name in proper_names(narration):
        key = normalize(name)
        if len(key) < 3 or f' {key} ' in src_norm:
            continue
        # Accept if every word of the name appears somewhere (e.g. inflected order).
        if all(f' {w} ' in src_norm for w in key.split()):
            continue
        unsupported_names.append(name)
    return {'ok': not unsupported_numbers, 'unsupported_numbers': unsupported_numbers,
            'unsupported_names': unsupported_names[:12],
            'note': 'Số liệu không có trong nguồn sẽ chặn duyệt. Tên riêng lạ chỉ là cảnh báo (có thể do phiên âm/dịch).'}
