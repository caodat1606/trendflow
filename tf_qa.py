"""Output QA on the actual MP4 (not just return codes).

Checks: container/streams, duration vs audio and 45–60 s target, loudness, long silences,
and — per sentence — that the decoded frame at the sentence's spoken time shows the photo
assigned to that sentence (correlation against the reference photo crop, and better than
any other photo in the video). Writes a contact sheet for human review.
"""
from __future__ import annotations

import io
import json
import math
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

import tf_render


def ffprobe(path: Path) -> dict:
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                       capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise RuntimeError('ffprobe lỗi: ' + p.stderr[-200:])
    return json.loads(p.stdout)


def audio_stats(path: Path) -> dict:
    p = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-i', str(path), '-vn', '-af',
                        'volumedetect,silencedetect=noise=-40dB:d=1.0', '-f', 'null', '-'],
                       capture_output=True, text=True, timeout=300)
    mean = re.search(r'mean_volume:\s*(-?[\d.]+)', p.stderr)
    peak = re.search(r'max_volume:\s*(-?[\d.]+)', p.stderr)
    starts = [float(x) for x in re.findall(r'silence_start:\s*(-?[\d.]+)', p.stderr)]
    ends = [float(x) for x in re.findall(r'silence_end:\s*([\d.]+)', p.stderr)]
    gaps = [(round(s, 2), round(e, 2)) for s, e in zip(starts, ends)]
    return {'mean_db': float(mean.group(1)) if mean else -99, 'max_db': float(peak.group(1)) if peak else -99, 'silences': gaps}


def pause_total(path: Path) -> float:
    p = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-i', str(path), '-vn', '-af',
                        'silencedetect=noise=-40dB:d=0.35', '-f', 'null', '-'], capture_output=True, text=True, timeout=300)
    return sum(float(x) for x in re.findall(r'silence_duration:\s*([\d.]+)', p.stderr))


def grab(path: Path, t: float) -> Image.Image:
    p = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-ss', f'{t:.3f}', '-i', str(path),
                        '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, timeout=60)
    if p.returncode or not p.stdout:
        raise RuntimeError(f'Không trích được khung hình tại {t:.2f}s')
    return Image.open(io.BytesIO(p.stdout)).convert('RGB')


def _vec(img: Image.Image, box):
    g = img.crop(box).convert('L').resize((40, 40), Image.Resampling.BILINEAR)
    px = list(g.tobytes())
    m = sum(px) / len(px)
    d = [v - m for v in px]
    n = math.sqrt(sum(v * v for v in d)) or 1.0
    return [v / n for v in d], ImageStat.Stat(g).stddev[0]


def corr(a, b):
    return sum(x * y for x, y in zip(a, b))


def envelope(path: Path, seconds: float = 30.0) -> list[float]:
    """Loudness envelope (RMS per 50 ms) of the first `seconds` of audio, mono 8 kHz."""
    p = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-i', str(path), '-vn', '-t', str(seconds),
                        '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], capture_output=True, timeout=120)
    import array
    a = array.array('h', p.stdout[: len(p.stdout) // 2 * 2])
    win = 400
    return [math.sqrt(sum(x * x for x in a[i:i + win]) / win) for i in range(0, len(a) - win, win)]


def same_audio(mp4: Path, mp3: Path) -> float:
    a, b = envelope(mp4), envelope(mp3)
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    da, db = [x - ma for x in a], [x - mb for x in b]
    den = math.sqrt(sum(x * x for x in da) * sum(x * x for x in db)) or 1.0
    return sum(x * y for x, y in zip(da, db)) / den


def run(video: Path, plan: dict, timings: dict, manifest: dict, *, final: bool, sheet_path: Path | None = None,
        voice_path: Path | None = None) -> dict:
    checks = []

    def check(name, ok, detail, blocking=True):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail, 'blocking': blocking})

    info = ffprobe(video)
    v = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
    a = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
    W, H = manifest['width'], manifest['height']
    check('streams', v and a and v.get('codec_name') == 'h264' and a.get('codec_name') == 'aac'
          and v.get('width') == W and v.get('height') == H,
          f"video={v and v.get('codec_name')} {v and v.get('width')}×{v and v.get('height')}, audio={a and a.get('codec_name')}")
    dur = float(info['format'].get('duration') or 0)
    audio_d = float(timings['segments'][-1]['end'])
    check('duration_matches_voice', abs(dur - audio_d) <= 0.35, f'MP4 {dur:.2f}s / giọng đọc {audio_d:.2f}s')
    check('duration_45_60', 44.5 <= dur <= 60.5, f'{dur:.1f} giây (mục tiêu 45–60)', blocking=final)
    if voice_path:
        c = same_audio(video, Path(voice_path))
        check('audio_is_current_voice', c >= 0.9, f'Âm thanh trong MP4 khớp file giọng mới {Path(voice_path).name} (tương quan {c:.3f})')
    st = audio_stats(video)
    check('loudness', st['mean_db'] > -30 and st['max_db'] <= 0.1, f"trung bình {st['mean_db']:.1f} dB, đỉnh {st['max_db']:.1f} dB")
    pauses = pause_total(video)
    check('pacing', pauses / max(1.0, dur) <= 0.18,
          f'Tổng thời gian ngắt nghỉ {pauses:.1f}s ({pauses / max(1.0, dur):.0%}; nên ≤ 18%)', blocking=False)
    long_gaps = [g for g in st['silences'] if g[1] - g[0] >= 1.5]
    check('no_long_silence', not long_gaps, 'Không có khoảng lặng ≥1,5 s' if not long_gaps else f'Khoảng lặng: {long_gaps[:4]}')

    shots = manifest['shots']
    box = (int(W * 0.12), int(H * 0.30), int(W * 0.88), int(H * 0.56))  # free of headline/caption/credit
    refs = {}
    for idx, sh in enumerate(shots):
        refs[idx] = _vec(tf_render.reference_frame(plan, shots, (sh['start'] + sh['end']) / 2, W, H), box)[0]
    per_segment, thumbs = [], []
    for seg in plan['segments']:
        mid = (seg['start'] + seg['end']) / 2
        k = max(i for i, s in enumerate(shots) if s['start'] <= mid + 1e-6)
        sh = shots[k]
        t = min(max(sh['start'] + tf_render.XFADE + 0.1, mid), sh['end'] - 0.05)
        frame = grab(video, t)
        vec, sd = _vec(frame, box)
        expect = _vec(tf_render.reference_frame(plan, shots, t, W, H), box)[0]
        c_exp = corr(vec, expect)
        others = [corr(vec, refs[j]) for j in refs if shots[j]['media_id'] != sh['media_id']]
        best_other = max(others) if others else -1
        assigned = {x['media_id'] for x in seg['shots']}
        ok = sh['media_id'] in assigned and c_exp >= 0.80 and c_exp > best_other + 0.05 and sd > 8
        per_segment.append({'segment': seg['index'] + 1, 'time': round(t, 2), 'media_id': sh['media_id'],
                            'corr': round(c_exp, 3), 'best_other': round(best_other, 3), 'ok': ok})
        thumbs.append((frame, seg, t, plan['media'][str(sh['media_id'])].get('credit', '')))
    bad = [p['segment'] for p in per_segment if not p['ok']]
    check('photo_matches_sentence', not bad, 'Mọi câu hiển thị đúng ảnh đã gán tại thời điểm đọc' if not bad else f'Câu lệch hình: {bad}')
    passed = all(c['ok'] for c in checks if c['blocking'])
    if sheet_path:
        contact_sheet(thumbs, sheet_path)
    return {'passed': passed, 'checks': checks, 'segments': per_segment, 'duration': round(dur, 2),
            'audio': st}


def contact_sheet(thumbs, path: Path, cols=4):
    tw, th, pad, text_h = 216, 384, 10, 120
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new('RGB', (cols * (tw + pad) + pad, rows * (th + text_h + pad) + pad), (16, 18, 24))
    d = ImageDraw.Draw(sheet)
    ft = tf_render.font(13, bold=False)
    fb = tf_render.font(14, bold=True)
    for i, (frame, seg, t, credit) in enumerate(thumbs):
        x = pad + (i % cols) * (tw + pad)
        y = pad + (i // cols) * (th + text_h + pad)
        sheet.paste(frame.resize((tw, th)), (x, y))
        d.text((x, y + th + 4), f'Câu {seg["index"] + 1} · {t:.1f}s', font=fb, fill=(255, 214, 64))
        lines = tf_render.wrap(d, seg['text'], ft, tw)[:5]
        for j, line in enumerate(lines):
            d.text((x, y + th + 24 + j * 16), line, font=ft, fill=(230, 233, 240))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, 'PNG')
    return path
