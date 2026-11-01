"""Chẩn đoán giọng Edge trên Windows/Mac. Chạy: .venv\\Scripts\\python.exe diagnose_tts.py  (hoặc nhấp đúp diagnose_tts.bat)
So sánh: gọi edge-tts kiểu dòng lệnh (luồng chính) và đúng đường app dùng (luồng phụ, WordBoundary, câu dài)."""
import asyncio
import os
import platform
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
lines = []


def log(msg=''):
    print(msg)
    lines.append(str(msg))


def attempt(name, fn):
    t0 = time.time()
    try:
        info = fn()
        log(f'OK    {name}  ({time.time() - t0:.1f}s) {info or ""}')
        return True
    except Exception as exc:
        log(f'FAIL  {name}  ({time.time() - t0:.1f}s) {type(exc).__name__}: {str(exc)[:200]}')
        lines.append('      ' + traceback.format_exc().strip().splitlines()[-1][:300])
        return False


def main():
    import edge_tts
    import tf_tts
    log(f'=== TrendFlow TTS check {datetime.now().isoformat(timespec="seconds")} ===')
    log(f'OS: {platform.platform()} · Python {sys.version.split()[0]} · edge-tts {edge_tts.__version__}')
    log(f'Giờ UTC máy: {datetime.now(timezone.utc).isoformat(timespec="seconds")}')
    for k in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy'):
        if os.environ.get(k):
            log(f'Proxy {k}={os.environ[k]}')
    narration = 'Xin chào, đây là bài kiểm tra giọng đọc tiếng Việt của TrendFlow.'
    import tf_paths
    db = tf_paths.DB_PATH
    if db.is_file():
        with sqlite3.connect(db) as c:
            r = c.execute('SELECT narration FROM script_projects ORDER BY updated_at DESC LIMIT 1').fetchone()
            if r and r[0]:
                narration = r[0]
    from tf_text import split_sentences
    sents = split_sentences(narration)
    first = sents[0]
    tmp = Path(tempfile.mkdtemp(prefix='tf_diag_'))
    voice = 'vi-VN-NamMinhNeural'

    def cli_style(text, words=False):
        def run():
            kw = {'boundary': 'WordBoundary'} if words else {}
            asyncio.run(edge_tts.Communicate(text, voice, **kw).save(str(tmp / 'a.mp3')))
            return f'{(tmp / "a.mp3").stat().st_size} byte'
        return run

    def in_thread(fn):
        def run():
            box = {}
            th = threading.Thread(target=lambda: box.update(r=_safe(fn)))
            th.start(); th.join(180)
            if isinstance(box.get('r'), Exception):
                raise box['r']
            return box.get('r')
        return run

    attempt('1. Câu chào ngắn, luồng chính (giống lệnh test)', cli_style('Xin chào, đây là bài kiểm tra.'))
    attempt('2. Câu đầu kịch bản, luồng chính', cli_style(first))
    attempt('3. Câu đầu kịch bản, luồng phụ (như app)', in_thread(cli_style(first)))
    attempt('4. Câu đầu + WordBoundary', cli_style(first, words=True))
    attempt('5. Cả kịch bản + WordBoundary (cách app đọc cả bài)', cli_style(tf_tts.speakable(' '.join(sents)), words=True))
    attempt('6. Hàm app _edge_stream, luồng phụ', in_thread(lambda: tf_tts._edge_stream(first, tmp / 'b.mp3', voice, 0, None, words=False) or 'ok'))
    attempt('8. Cách app 3.2 gọi Edge (tiến trình riêng, như lệnh test)', lambda: tf_tts._edge_cli(first, tmp / 'd.mp3', voice, 0) or 'ok')
    attempt('7. Toàn bộ app synthesize(provider=edge), 2 câu', lambda: tf_tts.synthesize(sents[:2], tmp / 'c.mp3', voice, 0, 'edge')['mode'])
    for folder in (Path.home() / 'Desktop', Path.home() / 'OneDrive' / 'Desktop', ROOT):
        if folder.is_dir():
            out = folder / 'TrendFlow_TTS_check.txt'
            try:
                out.write_text('\n'.join(lines), encoding='utf-8')
                log(f'\nĐã lưu: {out} — gửi file này cho Claude.')
                break
            except OSError:
                continue


def _safe(fn):
    try:
        return fn()
    except Exception as exc:
        return exc


if __name__ == '__main__':
    main()
    if os.name == 'nt':
        input('Nhấn Enter để đóng')
