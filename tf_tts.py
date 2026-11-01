"""Timed Vietnamese speech synthesis.

Returns real timestamps for every scene (sentence) so pictures change exactly when the
sentence starts. Two modes:
  * aligned      — Edge Neural on the whole text with boundary='WordBoundary' (edge-tts>=7
                   defaults to SentenceBoundary; 2.7 never received word marks), marks aligned
                   to narration characters.
  * per_segment  — each sentence synthesised separately and concatenated; timings are exact
                   by construction. Used for gTTS / macOS "Linh" / espeak, or when alignment fails.
Audio is never time-stretched to reach a target duration.
"""
from __future__ import annotations

import asyncio
import sys
from xml.sax.saxutils import escape
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from tf_text import normalize

SAMPLE_RATE = 24000
GTTS_TEMPO = float(os.getenv('GTTS_TEMPO', '1.15'))  # gTTS reads slowly; speed only the fallback voice up a bit
GAP_SECONDS = 0.30        # natural pause after a full stop
COMMA_GAP_SECONDS = 0.14  # when a long sentence was split at a comma
TRIM = ('silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse,'
        'silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse')
PROVIDER_LABELS = {
    'edge': 'Nam Minh/Hoài My — Edge (miễn phí)',
    'azure': 'Nam Minh/Hoài My — Azure Speech (Microsoft chính thức)',
    'gemini': 'Gemini TTS (Google AI — giọng khác Nam Minh)',
    'gtts': 'Google gTTS (dự phòng, giọng kém tự nhiên)',
    'macos': 'macOS "Linh" (dự phòng offline)',
    'espeak': 'espeak-ng (chỉ dùng để kiểm thử)',
}
VOICES = {'vi-VN-NamMinhNeural': 'Nam Minh — nam', 'vi-VN-HoaiMyNeural': 'Hoài My — nữ'}


class TTSFailure(RuntimeError):
    pass


def _ffmpeg():
    exe = shutil.which('ffmpeg')
    if not exe:
        raise TTSFailure('Không tìm thấy FFmpeg. Cài bằng: brew install ffmpeg')
    return exe


def probe_duration(path: Path) -> float:
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                       capture_output=True, text=True, timeout=20)
    try:
        return float(p.stdout.strip())
    except ValueError:
        raise TTSFailure('Không đọc được thời lượng audio: ' + p.stderr[-200:])


def mean_volume(path: Path) -> float:
    p = subprocess.run([_ffmpeg(), '-hide_banner', '-nostdin', '-i', str(path), '-af', 'volumedetect',
                        '-f', 'null', '-'], capture_output=True, text=True, timeout=120)
    m = re.search(r'mean_volume:\s*(-?[\d.]+) dB', p.stderr)
    return float(m.group(1)) if m else -99.0


def _decode_to_pcm(src: Path, dst: Path, trim: bool = False, tempo: float = 1.0):
    """Decode to 24 kHz mono PCM. trim=True removes leading/trailing silence (gTTS/say add ~0.3–0.5 s each side)."""
    r = subprocess.run([_ffmpeg(), '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', '-i', str(src),
                        *(['-af', ','.join(x for x in (TRIM if trim else '', f'atempo={tempo:.3f}' if abs(tempo - 1) > 0.01 else '') if x)]
                          if trim or abs(tempo - 1) > 0.01 else []), '-ac', '1', '-ar', str(SAMPLE_RATE), '-sample_fmt', 's16', str(dst)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode or not dst.is_file():
        raise TTSFailure('Không giải mã được audio: ' + r.stderr[-200:])


def _encode_final(src: Path, dest: Path):
    tmp = dest.with_name(dest.stem + '.partial.mp3')
    r = subprocess.run([_ffmpeg(), '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', '-i', str(src),
                        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', '-ar', '44100', '-ac', '1',
                        '-c:a', 'libmp3lame', '-b:a', '160k', str(tmp)], capture_output=True, text=True, timeout=300)
    if r.returncode or not tmp.is_file() or tmp.stat().st_size < 2000:
        tmp.unlink(missing_ok=True)
        raise TTSFailure('Không mã hoá được MP3: ' + r.stderr[-200:])
    os.replace(tmp, dest)


# ---------------------------------------------------------------- engines (one text -> file)

EDGE_TIMEOUT = float(os.getenv('EDGE_TIMEOUT', '45'))   # seconds per request (one sentence)
EDGE_RETRY_DELAYS = (0, 2, 5, 10)                          # 4 rounds; each round tries in-process, then CLI
EDGE_CLI_MODULE = os.getenv('TF_EDGE_CLI_MODULE', 'edge_tts')


def _edge_stream(text: str, out: Path, voice_id: str, rate: int, cancel=None, words=True):
    """In-process edge-tts. Explicit connect/receive timeouts; cancel is checked while audio streams."""
    import edge_tts

    async def run():
        marks = []
        kwargs = {'boundary': 'WordBoundary'} if words else {}
        communicate = edge_tts.Communicate(text=text, voice=voice_id, rate=f'{rate:+d}%',
                                           connect_timeout=10, receive_timeout=30, **kwargs)
        with out.open('wb') as f:
            async for chunk in communicate.stream():
                if cancel:
                    cancel()
                if chunk.get('type') == 'audio':
                    f.write(chunk.get('data', b''))
                elif chunk.get('type') == 'WordBoundary':
                    marks.append({'start': chunk.get('offset', 0) / 1e7,
                                  'end': (chunk.get('offset', 0) + chunk.get('duration', 0)) / 1e7,
                                  'text': chunk.get('text', '')})
        return marks

    marks = asyncio.run(asyncio.wait_for(run(), timeout=EDGE_TIMEOUT))
    if not out.is_file() or out.stat().st_size < 1500:
        raise TTSFailure('Edge trả về audio rỗng.')
    return marks


def _edge_cli(text: str, out: Path, voice_id: str, rate: int, cancel=None):
    """edge-tts command line in a separate process (same as diagnose test 8). Killable on cancel/timeout."""
    txt = out.with_suffix('.txt')
    txt.write_text(text, encoding='utf-8')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen([sys.executable, '-m', EDGE_CLI_MODULE, '--voice', voice_id, '--rate', f'{rate:+d}%',
                             '--file', str(txt), '--write-media', str(out)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    t0 = time.monotonic()
    try:
        while proc.poll() is None:
            if cancel:
                cancel()
            if time.monotonic() - t0 > EDGE_TIMEOUT:
                raise TimeoutError(f'edge-tts CLI quá {EDGE_TIMEOUT:.0f}s')
            time.sleep(0.2)
        err = proc.stderr.read().decode('utf-8', 'replace')
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        raise
    finally:
        for pipe in (proc.stdout, proc.stderr):
            if pipe:
                pipe.close()
        txt.unlink(missing_ok=True)
    if proc.returncode or not out.is_file() or out.stat().st_size < 1500:
        lines = [ln for ln in err.strip().splitlines() if ln.strip()]
        raise RuntimeError(f'CLI exit {proc.returncode}: ' + (' | '.join(lines[-2:]) if lines else 'không có thông báo')[:300])


def _sleep(seconds, cancel=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel:
            cancel()
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))


def _edge_sentence(text, out, voice_id, rate, cancel, log, index):
    """One sentence with Edge. Every attempt is logged (sentence, round, method, time, result).
    Failure here never disables Edge for later sentences or later jobs."""
    for rnd, delay in enumerate(EDGE_RETRY_DELAYS, 1):
        _sleep(delay, cancel)
        for how, fn in (('stream', lambda: _edge_stream(text, out, voice_id, rate, cancel, words=False)),
                        ('cli', lambda: _edge_cli(text, out, voice_id, rate, cancel))):
            out.unlink(missing_ok=True)
            t0 = time.monotonic()
            try:
                fn()
                log.append({'engine': 'edge', 'sentence': index + 1, 'round': rnd, 'how': how, 'ok': True,
                            'seconds': round(time.monotonic() - t0, 2), 'bytes': out.stat().st_size})
                return
            except Exception as exc:
                if type(exc).__name__ == 'JobCancelled':
                    raise
                msg = f'{type(exc).__name__}: {str(exc)[:200]}'
                log.append({'engine': 'edge', 'sentence': index + 1, 'round': rnd, 'how': how, 'ok': False,
                            'seconds': round(time.monotonic() - t0, 2), 'error': msg})
                logging.warning('Edge câu %d lần %d (%s) lỗi: %s', index + 1, rnd, how, msg)
    errors = [f"lần {a['round']}/{a['how']}: {a['error']}" for a in log if a.get('sentence') == index + 1 and not a['ok']]
    raise TTSFailure(f'Edge không đọc được câu {index + 1} sau {len(EDGE_RETRY_DELAYS)} vòng thử. ' + ' ; '.join(errors[-2:]))


def azure_config(settings: dict | None = None) -> dict | None:
    s = settings or {}
    key = (s.get('azure_key') or os.getenv('AZURE_SPEECH_KEY') or '').strip()
    region = (s.get('azure_region') or os.getenv('AZURE_SPEECH_REGION') or 'southeastasia').strip()
    return {'key': key, 'region': region} if key else None


def _azure(text: str, out: Path, voice_id: str, rate: int, cfg: dict):
    import requests
    ssml = (f"<speak version='1.0' xml:lang='vi-VN'><voice name='{voice_id}'>"
            f"<prosody rate='{rate:+d}%'>{escape(text)}</prosody></voice></speak>")
    try:
        r = requests.post(f"https://{cfg['region']}.tts.speech.microsoft.com/cognitiveservices/v1",
                          data=ssml.encode('utf-8'), timeout=60,
                          headers={'Ocp-Apim-Subscription-Key': cfg['key'], 'Content-Type': 'application/ssml+xml',
                                   'X-Microsoft-OutputFormat': 'audio-24khz-96kbitrate-mono-mp3',
                                   'User-Agent': 'TrendFlow'})
    except requests.RequestException as exc:
        raise TTSFailure('Không kết nối được Azure Speech: ' + str(exc)[:120])
    if r.status_code == 401:
        raise TTSFailure('Azure Speech từ chối key (401) — kiểm tra key và vùng (region) trong Cài đặt.')
    if r.status_code != 200 or len(r.content) < 1500:
        raise TTSFailure(f'Azure Speech lỗi {r.status_code}: {r.text[:150]}')
    out.write_bytes(r.content)


GEMINI_MODELS = ['gemini-3.1-flash-tts-preview', 'gemini-2.5-flash-preview-tts', 'gemini-2.5-flash-tts']
GEMINI_VOICES = {'Charon': 'Charon — nam, rõ ràng (tin tức)', 'Orus': 'Orus — nam, chắc giọng',
                 'Fenrir': 'Fenrir — nam, sôi nổi', 'Puck': 'Puck — nam, vui', 'Kore': 'Kore — nữ, chắc giọng',
                 'Aoede': 'Aoede — nữ, nhẹ nhàng'}
GEMINI_STYLE = 'Read this Vietnamese news sentence aloud as a natural, confident TV news anchor at a brisk pace: '
_GEMINI_MODEL_OK: dict = {}


def gemini_config(settings: dict | None = None) -> dict | None:
    s = settings or {}
    key = (s.get('gemini_key') or os.getenv('GEMINI_API_KEY') or '').strip()
    if not key:
        return None
    return {'key': key, 'voice': s.get('gemini_voice') or 'Charon', 'model': (s.get('gemini_model') or '').strip()}


def _gemini(text: str, out: Path, cfg: dict):
    """Gemini API TTS -> 24 kHz 16-bit mono PCM, wrapped as WAV. Retries on rate limits (free tier)."""
    import base64
    import requests
    models = [cfg['model']] if cfg.get('model') else ([_GEMINI_MODEL_OK['m']] if 'm' in _GEMINI_MODEL_OK else GEMINI_MODELS)
    body = {'contents': [{'parts': [{'text': GEMINI_STYLE + text}]}],
            'generationConfig': {'responseModalities': ['AUDIO'],
                                 'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': cfg['voice']}}}}}
    last = ''
    for model in models:
        for attempt in range(4):
            try:
                r = requests.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                                  params={'key': cfg['key']}, json=body, timeout=90)
            except requests.RequestException as exc:
                raise TTSFailure('Không kết nối được Gemini: ' + str(exc)[:120])
            if r.status_code == 429:          # free-tier rate limit: wait and retry
                last = 'Gemini báo vượt giới hạn miễn phí (429).'
                time.sleep(min(40, 8 * (attempt + 1)))
                continue
            if r.status_code in (400, 403) and 'API key' in r.text:
                raise TTSFailure('Gemini từ chối API key — kiểm tra key trong Cài đặt.')
            if r.status_code == 404:          # model name not available for this key/version -> try the next one
                last = f'Không có model {model}'
                break
            if r.status_code != 200:
                raise TTSFailure(f'Gemini lỗi {r.status_code}: {r.text[:160]}')
            try:
                part = r.json()['candidates'][0]['content']['parts'][0]['inlineData']
                pcm = base64.b64decode(part['data'])
            except (KeyError, IndexError, ValueError):
                raise TTSFailure('Gemini không trả âm thanh: ' + r.text[:160])
            rate_m = re.search(r'rate=(\d+)', part.get('mimeType', ''))
            with wave.open(str(out), 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(rate_m.group(1)) if rate_m else 24000)
                w.writeframes(pcm)
            _GEMINI_MODEL_OK['m'] = model
            return
    raise TTSFailure(last or 'Gemini TTS không khả dụng.')


def _edge_hint(exc) -> str:
    try:
        import edge_tts
        ver = getattr(edge_tts, '__version__', '?')
    except Exception:
        ver = 'chưa cài'
    msg = f'Edge TTS lỗi ({type(exc).__name__}: {str(exc)[:120]}) — edge-tts {ver}.'
    if type(exc).__name__ in ('NoAudioReceived', 'WebSocketBadStatus', 'WSServerHandshakeError'):
        msg += ' Microsoft đã đổi máy chủ Edge TTS, bản edge-tts ≥ 7.3.1 dùng máy chủ mới — cập nhật bằng install_mac.sh; kiểm tra giờ hệ thống Mac đúng; tắt VPN/DNS chặn quảng cáo rồi thử lại.'
    return msg


def _gtts(text: str, out: Path):
    from gtts import gTTS
    gTTS(text=text, lang='vi', slow=False).save(str(out))
    if not out.is_file() or out.stat().st_size < 1500:
        raise TTSFailure('gTTS trả về audio rỗng.')


def macos_voice_available() -> bool:
    if platform.system() != 'Darwin' or not shutil.which('say'):
        return False
    try:
        out = subprocess.run(['say', '-v', '?'], capture_output=True, text=True, timeout=10).stdout
        return bool(re.search(r'^Linh\s', out, re.M))
    except (OSError, subprocess.SubprocessError):
        return False


def _macos(text: str, out: Path):
    aiff = out.with_suffix('.aiff')
    r = subprocess.run(['say', '-v', 'Linh', '-o', str(aiff), text], capture_output=True, text=True, timeout=120)
    if r.returncode or not aiff.is_file():
        raise TTSFailure('macOS say thất bại: ' + r.stderr[-150:])
    os.replace(aiff, out)


def _espeak(text: str, out: Path):
    exe = shutil.which('espeak-ng') or shutil.which('espeak')
    if not exe:
        raise TTSFailure('Không có espeak-ng.')
    r = subprocess.run([exe, '-v', 'vi', '-s', '165', '-w', str(out), text], capture_output=True, text=True, timeout=120)
    if r.returncode or not out.is_file():
        raise TTSFailure('espeak thất bại: ' + r.stderr[-150:])


def speakable(text: str) -> str:
    """What the TTS engine reads (captions keep the original text): '30-10' -> '30 tháng 10'."""
    text = re.sub(r'\b(ngày|hôm|từ|đến|tới)\s+(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{4}))?\b',
                  lambda m: f"{m.group(1)} {int(m.group(2))} tháng {int(m.group(3))}" + (f" năm {m.group(4)}" if m.group(4) else ''),
                  text, flags=re.I)
    return re.sub(r'(\d)\s?%', r'\1 phần trăm', text)


def _single(engine, text, out, voice_id, rate, cancel, azure=None, gemini=None, log=None, index=0):
    text = speakable(text)
    if engine == 'edge':
        return _edge_sentence(text, out, voice_id, rate, cancel, log if log is not None else [], index)
    elif engine == 'azure':
        if not azure:
            raise TTSFailure('Chưa nhập key Azure Speech trong Cài đặt.')
        return _azure(text, out, voice_id, rate, azure)
    elif engine == 'gemini':
        if not gemini:
            raise TTSFailure('Chưa nhập Gemini API key trong Cài đặt.')
        return _gemini(text, out, gemini)
    elif engine == 'gtts':
        return _gtts(text, out)
    elif engine == 'macos':
        return _macos(text, out)
    elif engine == 'espeak':
        return _espeak(text, out)
    raise TTSFailure('Nhà cung cấp TTS không hợp lệ: ' + engine)


# ---------------------------------------------------------------- alignment

def align_marks(segments: list[str], marks: list[dict], duration: float):
    """Map word marks to narration characters. Returns (segment_starts, words, coverage)."""
    tokens = []  # (segment index, display word, normalised chars)
    for si, seg in enumerate(segments):
        for w in seg.split():
            tokens.append((si, w, normalize(w).replace(' ', '')))
    stream = ''.join(t[2] for t in tokens)
    char_time = [None] * (len(stream) + 1)
    cursor = matched = 0
    for mk in marks:
        key = normalize(mk.get('text', '')).replace(' ', '')
        if not key:
            continue
        pos = stream.find(key, cursor, cursor + len(key) + 60)
        if pos < 0:
            continue
        span = max(1, len(key))
        for k in range(span):
            frac = k / span
            char_time[pos + k] = mk['start'] + (mk['end'] - mk['start']) * frac
        cursor = pos + span
        matched += span
    coverage = matched / max(1, len(stream))
    # Interpolate missing characters linearly between known anchors.
    known = [(i, t) for i, t in enumerate(char_time[:-1]) if t is not None]
    if not known:
        return None, None, 0.0
    char_time[-1] = duration
    known.append((len(stream), duration))
    first_i, first_t = known[0]
    for i in range(0, first_i):
        char_time[i] = first_t * i / max(1, first_i)
    for (a, ta), (b, tb) in zip(known, known[1:]):
        for i in range(a + 1, b):
            char_time[i] = ta + (tb - ta) * (i - a) / (b - a)
    words, starts, pos = [], [None] * len(segments), 0
    for si, w, chars in tokens:
        start = char_time[pos] if chars else (char_time[pos] or 0.0)
        end = char_time[min(len(stream), pos + len(chars))]
        words.append({'seg': si, 'text': w, 'start': round(start, 3), 'end': round(max(start, end), 3)})
        if starts[si] is None:
            starts[si] = start
        pos += len(chars)
    starts[0] = 0.0
    return starts, words, coverage


def _segments_from_starts(segments, starts, duration):
    out = []
    for i, text in enumerate(segments):
        end = starts[i + 1] if i + 1 < len(segments) else duration
        out.append({'index': i, 'text': text, 'start': round(starts[i], 3), 'end': round(end, 3)})
    return out


def _proportional_words(seg_index, text, start, end):
    words = text.split()
    weights = [max(1, len(normalize(w).replace(' ', ''))) for w in words]
    total, acc, out = sum(weights), 0, []
    for w, k in zip(words, weights):
        a = start + (end - start) * acc / total
        acc += k
        out.append({'seg': seg_index, 'text': w, 'start': round(a, 3), 'end': round(start + (end - start) * acc / total, 3)})
    return out


# ---------------------------------------------------------------- public API

def engine_order(provider: str, azure: dict | None = None, allow_fallback: bool = False, gemini: dict | None = None) -> list[str]:
    """auto = the Nam Minh/Hoài My voice only (Edge, then Azure if keyed). Gemini (a different voice) is used only
    when chosen explicitly; gTTS/macOS only when the user enabled robotic fallbacks."""
    if provider == 'auto':
        order = ['edge'] + (['azure'] if azure else [])
        if allow_fallback:
            order += ['gtts'] + (['macos'] if macos_voice_available() else [])
        return order
    if provider not in PROVIDER_LABELS:
        raise TTSFailure('Nhà cung cấp TTS không hợp lệ.')
    return [provider]


def expected_providers(settings: dict) -> set:
    """Providers whose audio counts as 'current' for these settings."""
    p = settings.get('tts_provider', 'auto')
    if p == 'auto':
        out = {'edge', 'azure'}
        if settings.get('allow_fallback_voice'):
            out |= {'gtts', 'macos'}
        return out
    return {p}


def config_signature(settings: dict) -> str:
    import hashlib
    keys = [settings.get('tts_provider', 'auto'), settings.get('voice_id'), bool(settings.get('allow_fallback_voice'))]
    if settings.get('tts_provider') == 'gemini':
        keys += [settings.get('gemini_voice'), settings.get('gemini_model')]
    return hashlib.sha256(json.dumps(keys).encode()).hexdigest()[:16]


def synthesize(segments: list[str], dest: Path, voice_id: str = 'vi-VN-NamMinhNeural', rate: int = 0,
               provider: str = 'auto', cancel=None, progress=None, azure: dict | None = None,
               allow_fallback: bool = False, gemini: dict | None = None) -> dict:
    if not segments:
        raise TTSFailure('Chưa có lời thoại.')
    if voice_id not in VOICES:
        raise TTSFailure('Giọng đọc không hợp lệ.')
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    reasons = []
    attempt_log: list[dict] = []
    with tempfile.TemporaryDirectory(prefix='tf_tts_') as td:
        tmp = Path(td)
        for engine in engine_order(provider, azure, allow_fallback, gemini):
            try:
                if cancel:
                    cancel()
                # Sentence by sentence: exact scene timings, and the same request shape as the working CLI test.
                result = _per_segment(engine, segments, tmp, voice_id, rate, cancel, progress, azure, gemini, attempt_log)
                _encode_final(result.pop('_source'), dest)
                result['duration'] = round(probe_duration(dest), 3)
                result['segments'][-1]['end'] = result['duration']
                vol = mean_volume(dest)
                if vol < -40:
                    raise TTSFailure(f'Audio gần như im lặng ({vol:.1f} dB).')
                result.update({'attempt_log': attempt_log[-200:], 'provider': engine, 'provider_label': PROVIDER_LABELS[engine],
                               'fallback_reason': '; '.join(reasons), 'voice_id': voice_id,
                               'mean_volume_db': vol})
                return result
            except TTSFailure as exc:
                reasons.append(f'{engine}: {exc}')
                logging.warning('TTS %s failed: %s', engine, exc)
            except Exception as exc:
                if type(exc).__name__ in ('JobCancelled',):
                    raise
                reasons.append(f'{engine}: {type(exc).__name__}: {str(exc)[:160]}')
                logging.warning('TTS %s failed', engine, exc_info=True)
    hint = '' if allow_fallback else (' → Vào Cài đặt nhập key Azure Speech (cùng giọng Nam Minh) hoặc Gemini API key.'
                                      if not (azure or gemini) else '')
    err = TTSFailure('Không tạo được giọng đọc. ' + ' | '.join(reasons) + hint)
    err.attempt_log = attempt_log[-200:]
    raise err


def _per_segment(engine, segments, tmp, voice_id, rate, cancel, progress, azure=None, gemini=None, log=None):
    pcm_parts, timings, words, t = [], [], [], 0.0
    for i, text in enumerate(segments):
        if cancel:
            cancel()
        if progress:
            progress(f'{PROVIDER_LABELS[engine]}: câu {i + 1}/{len(segments)}')
        raw = tmp / f'{engine}_{i:03}.{"wav" if engine in ("espeak", "gemini") else "mp3" if engine != "macos" else "aiff"}'
        _single(engine, text, raw, voice_id, rate, cancel, azure, gemini, log, i)
        wav = tmp / f'pcm_{i:03}.wav'
        _decode_to_pcm(raw, wav, trim=True, tempo=GTTS_TEMPO if engine == 'gtts' else 1.0)
        with wave.open(str(wav), 'rb') as w:
            frames = w.readframes(w.getnframes())
        seconds = len(frames) / 2 / SAMPLE_RATE
        if seconds < 0.3:
            raise TTSFailure(f'Câu {i + 1} không có âm thanh.')
        start = t
        pcm_parts.append(frames)
        t += seconds
        words.extend(_proportional_words(i, text, start, t))
        if i + 1 < len(segments):
            g = GAP_SECONDS if re.search(r'[.!?…]["”’)]*$', text) else COMMA_GAP_SECONDS
            pcm_parts.append(b'\x00\x00' * int(SAMPLE_RATE * g))
            t += g
        timings.append({'index': i, 'text': text, 'start': round(start, 3), 'end': round(t, 3)})
    joined = tmp / f'{engine}_joined.wav'
    with wave.open(str(joined), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for part in pcm_parts:
            w.writeframes(part)
    return {'_source': joined, 'mode': 'per_segment', 'alignment_coverage': 1.0,
            'segments': timings, 'words': words}


def sample(text: str, dest: Path, voice_id: str, provider: str = 'auto', azure=None, allow_fallback=False, gemini=None) -> dict:
    """Short listening sample (first sentence)."""
    return synthesize([text], dest, voice_id, 0, provider, azure=azure, allow_fallback=allow_fallback, gemini=gemini)


def timings_json(result: dict) -> str:
    keep = {k: result[k] for k in ('mode', 'alignment_coverage', 'segments', 'words', 'provider', 'provider_label',
                                   'fallback_reason', 'mean_volume_db', 'config', 'attempt_log') if k in result}
    return json.dumps(keep, ensure_ascii=False)
