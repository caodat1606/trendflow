"""Video renderer: real photos (Ken Burns + crossfade), word-synchronised captions.

Frames are composed with Pillow and piped as raw RGB into FFmpeg, so no `subtitles`
(libass) filter or `-vsync` flag is needed — both broke on some macOS FFmpeg builds.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
MEDIA_DIR = Path(os.getenv('TRENDFLOW_DATA_DIR') or ROOT) / 'media_library'
FPS = 30
XFADE = 0.3
ACCENT = (255, 214, 64)
SECTIONS = {'Phim ảnh': 'GIẢI TRÍ', 'Tin thế giới': 'THẾ GIỚI', 'Công nghệ': 'CÔNG NGHỆ', 'Bóng đá': 'BÓNG ĐÁ'}
_WIN = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
FONT_BOLD = [os.path.join(_WIN, 'arialbd.ttf'), os.path.join(_WIN, 'segoeuib.ttf'),
             '/System/Library/Fonts/Supplemental/Arial Bold.ttf', '/Library/Fonts/Arial Bold.ttf',
             '/System/Library/Fonts/Supplemental/Verdana Bold.ttf',
             '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
             '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf']
FONT_REG = [os.path.join(_WIN, 'arial.ttf'), os.path.join(_WIN, 'segoeui.ttf'),
            '/System/Library/Fonts/Supplemental/Arial.ttf', '/Library/Fonts/Arial.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf']


class RenderError(RuntimeError):
    pass


@lru_cache(maxsize=32)
def font(size: int, bold: bool = True):
    for p in (FONT_BOLD if bold else FONT_REG) + (FONT_REG if bold else FONT_BOLD):
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    raise RenderError('Không tìm thấy font hỗ trợ tiếng Việt (Arial/DejaVu).')


def dims(quality: str):
    return (1080, 1920) if quality == '1080p' else (720, 1280)


def wrap(draw, text, ft, max_w):
    lines, cur = [], ''
    for w in text.split():
        cand = (cur + ' ' + w).strip()
        if cur and draw.textlength(cand, font=ft) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------------------------------ shot images

class Shot:
    """Pre-computed layers for one photo. `frame(p)` returns an RGB frame for progress p∈[0,1]."""

    def __init__(self, path: Path, W: int, H: int, seed: int):
        self.W, self.H = W, H
        img = Image.open(path).convert('RGB')
        self.aspect = img.width / img.height
        self.landscape = self.aspect > 0.95
        direction = seed % 4
        if self.landscape:
            # Blurred, darkened full-frame background + large foreground box (4:5) — keeps the subject.
            bg = _cover(img, W, H, (0.5, 0.5)).filter(ImageFilter.GaussianBlur(max(12, W // 30)))
            self.bg = Image.blend(bg, Image.new('RGB', bg.size, (8, 10, 14)), 0.45)
            self.box = (W, int(W * 1.2))
            self.box_y = int(H * 0.16)
            self.src = _cover(img, int(self.box[0] * 1.14), int(self.box[1] * 1.14), (0.5, 0.42))
        else:
            self.bg = None
            self.box = (W, H)
            self.box_y = 0
            self.src = _cover(img, int(W * 1.14), int(H * 1.14), (0.5, 0.38))
        sw, sh = self.src.size
        bw, bh = self.box
        self.mask = None
        if self.landscape:   # feather top/bottom edges into the blurred background
            mask = Image.new('L', self.box, 255)
            md = ImageDraw.Draw(mask)
            feather = int(bh * 0.09)
            for y in range(feather):
                v = int(255 * (y / feather) ** 1.5)
                md.line([(0, y), (bw, y)], fill=v)
                md.line([(0, bh - 1 - y), (bw, bh - 1 - y)], fill=v)
            self.mask = mask
        z0, z1 = (1.0, 1.12) if direction % 2 == 0 else (1.12, 1.0)   # zoom in / out
        pan = [(-1, 0), (1, 0), (0, -1), (0, 1)][direction]
        self.rects = []
        for z, side in ((z0, -1), (z1, 1)):
            cw, ch = min(sw, sw / z), min(sh, sh / z)
            cx = sw / 2 + pan[0] * side * (sw - cw) / 2 * 0.8
            cy = sh / 2 + pan[1] * side * (sh - ch) / 2 * 0.8
            self.rects.append((cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))

    def frame(self, p: float) -> Image.Image:
        e = 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, p)))   # ease in-out
        a, b = self.rects
        box = tuple(a[i] + (b[i] - a[i]) * e for i in range(4))
        fg = self.src.transform(self.box, Image.Transform.EXTENT, box, Image.Resampling.BILINEAR)
        if self.bg is None:
            return fg
        out = self.bg.copy()
        out.paste(fg, (0, self.box_y), self.mask)
        return out


def _cover(img, w, h, centering):
    from PIL import ImageOps
    return ImageOps.fit(img, (w, h), Image.Resampling.LANCZOS, centering=centering)


# ------------------------------------------------------------------ overlays

def static_overlay(W, H, category, preview):
    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    # Bottom & top gradients for legibility.
    for y in range(int(H * 0.45), H):
        a = int(200 * ((y - H * 0.45) / (H * 0.55)) ** 1.3)
        d.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    for y in range(0, int(H * 0.2)):
        a = int(120 * (1 - y / (H * 0.2)) ** 1.5)
        d.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    u = W / 720
    tag = 'TRENDFLOW  ·  ' + SECTIONS.get(category, 'TIN NHANH')
    ft = font(int(19 * u))
    tw = d.textlength(tag, font=ft)
    x, y = int(36 * u), int(64 * u)
    d.rounded_rectangle((x, y, x + tw + 28 * u, y + 38 * u), radius=int(19 * u), fill=(10, 14, 22, 170))
    d.ellipse((x + 12 * u, y + 15 * u, x + 20 * u, y + 23 * u), fill=ACCENT + (255,))
    d.text((x + 26 * u, y + 8 * u), tag, font=ft, fill=(255, 255, 255, 235))
    if preview:
        f2 = font(int(17 * u))
        label = 'BẢN XEM THỬ'
        lw = d.textlength(label, font=f2)
        d.rounded_rectangle((W - lw - 62 * u, y, W - 36 * u, y + 38 * u), radius=int(19 * u), fill=(170, 40, 40, 190))
        d.text((W - lw - 49 * u, y + 9 * u), label, font=f2, fill=(255, 255, 255, 240))
    return ov


def headline_layer(W, H, text):
    u = W / 720
    ft = font(int(46 * u))
    probe = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    lines = wrap(probe, text.upper() if len(text) < 60 else text, ft, W - int(90 * u))[:3]
    lh = int(56 * u)
    layer = Image.new('RGBA', (W, lh * len(lines) + int(40 * u)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = int(10 * u)
    for line in lines:
        d.text((int(40 * u), y), line, font=ft, fill=(255, 255, 255, 255), stroke_width=max(2, int(4 * u)),
               stroke_fill=(0, 0, 0, 255))
        y += lh
    d.rounded_rectangle((int(40 * u), y + int(6 * u), int(130 * u), y + int(14 * u)), radius=int(4 * u), fill=ACCENT + (255,))
    return layer


def caption_layer(W, words, active):
    u = W / 720
    ft = font(int(34 * u))
    probe = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    max_w = W - int(130 * u)
    items = list(enumerate(words))
    full = ' '.join(words)
    if probe.textlength(full, font=ft) <= max_w:
        lines = [items]
    else:   # two balanced lines (no orphan word); fall back to greedy if still too wide
        best = None
        for cut in range(1, len(items)):
            a = probe.textlength(' '.join(words[:cut]), font=ft)
            b = probe.textlength(' '.join(words[cut:]), font=ft)
            if max(a, b) <= max_w and (best is None or max(a, b) < best[0]):
                best = (max(a, b), cut)
        if best:
            lines = [items[:best[1]], items[best[1]:]]
        else:
            lines, cur = [], []
            for i, w in items:
                test = ' '.join(x for _, x in cur + [(i, w)])
                if cur and probe.textlength(test, font=ft) > max_w:
                    lines.append(cur)
                    cur = []
                cur.append((i, w))
            if cur:
                lines.append(cur)
    lines = lines[:2]   # never more than two lines on screen
    lh = int(46 * u)
    layer = Image.new('RGBA', (W, lh * len(lines) + int(20 * u)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    sw = max(2, int(4 * u))
    for li, line in enumerate(lines):
        text = ' '.join(w for _, w in line)
        x = (W - probe.textlength(text, font=ft)) / 2
        y = int(8 * u) + li * lh
        for i, w in line:
            color = ACCENT + (255,) if i == active else (255, 255, 255, 255)
            d.text((x, y), w, font=ft, fill=color, stroke_width=sw, stroke_fill=(0, 0, 0, 255))
            x += probe.textlength(w + ' ', font=ft)
    return layer


def credit_layer(W, text):
    u = W / 720
    ft = font(int(15 * u), bold=False)
    probe = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    text = re.sub(r'\s+', ' ', text)
    if probe.textlength(text, font=ft) > W * 0.8:
        while probe.textlength(text + '…', font=ft) > W * 0.8 and len(text) > 10:
            text = text[:text.rstrip().rfind(' ')] if ' ' in text.strip() else text[:-1]
        text = text.rstrip(' ·,') + '…'
    layer = Image.new('RGBA', (int(probe.textlength(text, font=ft) + 24 * u), int(28 * u)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((0, 0, layer.width - 1, layer.height - 1), radius=int(8 * u), fill=(0, 0, 0, 120))
    d.text((int(12 * u), int(5 * u)), text, font=ft, fill=(235, 238, 245, 230))
    return layer


def caption_groups(timings, max_words=7):
    groups = []
    for seg in timings['segments']:
        words = [w for w in timings['words'] if w['seg'] == seg['index']]
        cur = []
        for w in words:
            cur.append(w)
            if len(cur) >= max_words or (len(cur) >= 3 and re.search(r'[.,;:!?…]$', w['text'])):
                groups.append(cur)
                cur = []
        if cur:
            if groups and len(cur) <= 2 and groups[-1][0]['seg'] == cur[0]['seg']:
                groups[-1].extend(cur)
            else:
                groups.append(cur)
    out = []
    for k, g in enumerate(groups):
        end = groups[k + 1][0]['start'] if k + 1 < len(groups) else timings['segments'][-1]['end']
        out.append({'start': g[0]['start'], 'end': end, 'words': g})
    return out


# ------------------------------------------------------------------ render

def flatten_shots(plan, duration):
    shots = [dict(sh, seg=s['index']) for s in plan['segments'] for sh in s['shots']]
    if not shots:
        raise RenderError('Kế hoạch cảnh không có ảnh thật — không dựng video.')
    shots.sort(key=lambda x: x['start'])
    merged = [shots[0]]
    for sh in shots[1:]:   # same photo in consecutive sentences = one continuous shot
        if sh['media_id'] == merged[-1]['media_id']:
            merged[-1]['segs'] = merged[-1].get('segs', [merged[-1]['seg']]) + [sh['seg']]
        else:
            merged.append(sh)
    shots = merged
    for sh in shots:
        sh.setdefault('segs', [sh['seg']])
    shots[0]['start'] = 0.0
    for a, b in zip(shots, shots[1:]):
        a['end'] = b['start']
    shots[-1]['end'] = duration
    return shots


def encoder_args(final: bool):
    choice = os.getenv('VIDEO_ENCODER', 'libx264')
    if choice == 'videotoolbox':
        return ['-c:v', 'h264_videotoolbox', '-b:v', '10M' if final else '5M', '-allow_sw', '1']
    return ['-c:v', 'libx264', '-preset', 'medium' if final else 'veryfast', '-crf', '19' if final else '23']


def render(plan, timings, project, audio: Path, out: Path, *, quality='720p', preview=False, cancel=None,
           progress=None) -> dict:
    if not shutil.which('ffmpeg'):
        raise RenderError('Không tìm thấy FFmpeg trên PATH. Cài: brew install ffmpeg')
    W, H = dims(quality)
    duration = float(timings['segments'][-1]['end'])
    shots = flatten_shots(plan, duration)
    media = plan['media']
    groups = caption_groups(timings)
    total = int(math.ceil(duration * FPS))
    static = static_overlay(W, H, project.get('category'), preview)
    head = headline_layer(W, H, plan.get('headline') or '') if plan.get('headline') else None
    head_end = min(3.6, plan['segments'][0]['end'] + 0.4) if head else 0
    credits = {}
    cache: dict[int, Shot] = {}

    def shot_obj(k):
        if k not in cache:
            m = media[str(shots[k]['media_id'])]
            path = MEDIA_DIR / m['local_filename']
            if not path.is_file():
                raise RenderError(f'Thiếu file ảnh {m["local_filename"]} — chạy lại Test.')
            cache[k] = Shot(path, W, H, k + shots[k]['media_id'])
            for old in [x for x in cache if x < k - 1]:
                cache.pop(old)
        return cache[k]

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + '.partial.mp4')
    cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0', *encoder_args(not preview),
           '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '44100',
           '-movflags', '+faststart', '-t', f'{duration:.3f}', str(tmp)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    k = g = 0
    cap_key, cap_img = None, None
    try:
        for f in range(total):
            t = f / FPS
            while k + 1 < len(shots) and t >= shots[k + 1]['start']:
                k += 1
            sh = shots[k]
            span = max(0.5, sh['end'] - sh['start'] + XFADE)
            frame = shot_obj(k).frame((t - sh['start']) / span)
            if k > 0 and t - sh['start'] < XFADE:
                prev = shots[k - 1]
                pspan = max(0.5, prev['end'] - prev['start'] + XFADE)
                pf = shot_obj(k - 1).frame((t - prev['start']) / pspan)
                frame = Image.blend(pf, frame, (t - sh['start']) / XFADE)
            frame.paste(static, (0, 0), static)
            if head and t < head_end:
                alpha = min(1.0, (head_end - t) / 0.35)
                layer = head if alpha >= 1 else _fade(head, alpha)
                frame.paste(layer, (0, int(H * 0.115)), layer)
            while g + 1 < len(groups) and t >= groups[g + 1]['start']:
                g += 1
            grp = groups[g] if groups else None
            if grp and grp['start'] <= t < grp['end'] + 0.05:
                active = max([i for i, w in enumerate(grp['words']) if w['start'] <= t] or [0])
                key = (g, active)
                if key != cap_key:
                    cap_key, cap_img = key, caption_layer(W, [w['text'] for w in grp['words']], active)
                frame.paste(cap_img, (0, int(H * 0.735) - cap_img.height // 2), cap_img)
            mid = sh['media_id']
            if mid not in credits:
                credits[mid] = credit_layer(W, media[str(mid)].get('credit') or '')
            cl = credits[mid]
            frame.paste(cl, (int(W * 0.05), int(H * 0.795)), cl)
            proc.stdin.write(frame.tobytes())
            if f % 15 == 0:
                if cancel:
                    cancel()
                if progress and f % 90 == 0:
                    progress(f / total, f'Dựng khung {f}/{total} ({quality})…')
                if time.monotonic() - started > 1500:
                    raise RenderError('Render quá 25 phút — dừng để bảo vệ máy.')
        proc.stdin.close()
        err = proc.stderr.read().decode('utf-8', 'replace')[-800:]
        if proc.wait(timeout=600) != 0 or not tmp.is_file() or tmp.stat().st_size < 10000:
            raise RenderError('FFmpeg thất bại: ' + err)
        os.replace(tmp, out)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        tmp.unlink(missing_ok=True)
        raise
    finally:
        if proc.stderr:
            proc.stderr.close()
    manifest = {'width': W, 'height': H, 'fps': FPS, 'duration': duration,
                'shots': [{'start': s['start'], 'end': s['end'], 'media_id': s['media_id'], 'segs': s['segs']} for s in shots],
                'captions': [{'start': c['start'], 'end': c['end'], 'text': ' '.join(w['text'] for w in c['words'])} for c in groups],
                'headline_until': head_end, 'render_seconds': round(time.monotonic() - started, 1)}
    return manifest


def _fade(layer, alpha):
    r, g, b, a = layer.split()
    a = a.point(lambda v: int(v * alpha))
    return Image.merge('RGBA', (r, g, b, a))


def reference_frame(plan, shots_manifest, t, W, H):
    """Photo-only frame (no overlays) that should appear at time t — used by QA."""
    shots = shots_manifest
    k = max(i for i, s in enumerate(shots) if s['start'] <= t + 1e-6)
    sh = shots[k]
    m = plan['media'][str(sh['media_id'])]
    shot = Shot(MEDIA_DIR / m['local_filename'], W, H, k + sh['media_id'])
    span = max(0.5, sh['end'] - sh['start'] + XFADE)
    return shot.frame((t - sh['start']) / span)
