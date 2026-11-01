"""`trendflow` command: start/stop/status/update/backup/logs.

Usage (Windows PowerShell or Terminal, any folder, after scripts/setup_windows.ps1):
    trendflow            # start (if not running) and open the browser
    trendflow stop       # stop the server
    trendflow status     # version, running?, port, data folder
    trendflow update     # safe Git update (backup → test new version → fast-forward → restart)
    trendflow backup     # back up the database + .env now
    trendflow logs       # last lines of the server log

Standard library only. Code = this Git repository; personal data = DATA_DIR (outside the repository).
Never runs `git reset --hard`, never overwrites uncommitted local changes, never deletes data.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = os.name == 'nt'
PORTS = range(8001, 8021)


def default_data_dir() -> Path:
    return Path(os.getenv('TRENDFLOW_DATA_DIR') or (Path.home() / 'TrendFlowData'))


def say(msg=''):
    print(msg, flush=True)


def fail(msg, code=1):
    print(f'✕ {msg}', file=sys.stderr, flush=True)
    raise SystemExit(code)


def venv_python(root: Path = ROOT) -> Path:
    p = root / '.venv' / ('Scripts/python.exe' if IS_WIN else 'bin/python')
    return p if p.exists() else Path(sys.executable)


# ------------------------------------------------------------------ server state

def run_dir(data: Path) -> Path:
    d = data / 'run'
    d.mkdir(parents=True, exist_ok=True)
    return d


def ping(port: int, timeout=1.5) -> dict | None:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
            return data if data.get('app') == 'TrendFlow' else None
    except Exception:
        return None


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def find_running(data: Path) -> dict | None:
    """Our server from the state file, else any TrendFlow answering on 8001–8020 (e.g. started by the old shortcut)."""
    state = run_dir(data) / 'server.json'
    if state.is_file():
        try:
            st = json.loads(state.read_text(encoding='utf-8'))
            info = ping(st['port'])
            if info:
                return dict(st, **info)
        except (ValueError, KeyError, OSError):
            pass
    for port in PORTS:
        if not port_free(port):
            info = ping(port, timeout=0.6)
            if info:
                return {'port': port, **info}
    return None


def start(root: Path = ROOT, data: Path | None = None, open_browser=True, wait=40) -> dict:
    data = data or default_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    running = find_running(data)
    if running:
        url = f"http://127.0.0.1:{running['port']}/discover"
        say(f"TrendFlow {running.get('version', '')} đang chạy sẵn: {url}")
        if open_browser:
            webbrowser.open(url)
        return running
    port = next((p for p in PORTS if port_free(p)), None)
    if port is None:
        fail('Không còn cổng trống 8001–8020. Đóng bớt ứng dụng hoặc chạy `trendflow stop`.')
    logs = data / 'logs'
    logs.mkdir(exist_ok=True)
    log_path = logs / 'server.log'
    env = dict(os.environ, TRENDFLOW_DATA_DIR=str(data), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    py = venv_python(root)
    cmd = [str(py), '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(port)]
    log = open(log_path, 'a', encoding='utf-8')
    log.write(f'\n===== {datetime.now().isoformat(timespec="seconds")} start {" ".join(cmd)} =====\n')
    log.flush()
    kw = {}
    if IS_WIN:
        kw['creationflags'] = 0x00000200 | 0x08000000   # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kw['start_new_session'] = True
    proc = subprocess.Popen(cmd, cwd=str(root), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
    say(f'Đang khởi động TrendFlow trên cổng {port}…')
    t0 = time.time()
    while time.time() - t0 < wait:
        info = ping(port)
        if info:
            st = {'pid': proc.pid, 'port': port, 'started_at': datetime.now().isoformat(timespec='seconds'),
                  'root': str(root), 'data_dir': str(data)}
            (run_dir(data) / 'server.json').write_text(json.dumps(st), encoding='utf-8')
            url = f'http://127.0.0.1:{port}/discover'
            say(f"✓ TrendFlow {info.get('version', '')} đã chạy: {url}")
            if open_browser:
                webbrowser.open(url)
            return dict(st, **info)
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    tail = log_tail(log_path, 25)
    if proc.poll() is None:
        proc.kill()
    fail('Không khởi động được TrendFlow. Lỗi cuối trong log:\n' + tail + f'\n(Log đầy đủ: {log_path})')


def stop(data: Path | None = None, quiet=False) -> bool:
    data = data or default_data_dir()
    running = find_running(data)
    if not running:
        if not quiet:
            say('TrendFlow không chạy.')
        return False
    pid = running.get('pid')
    if IS_WIN:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    for _ in range(40):
        if not ping(running['port'], timeout=0.5):
            break
        time.sleep(0.25)
    (run_dir(data) / 'server.json').unlink(missing_ok=True)
    if not quiet:
        say(f"✓ Đã dừng TrendFlow (cổng {running['port']}).")
    return True


def log_tail(path: Path, n=30) -> str:
    try:
        return '\n'.join(path.read_text(encoding='utf-8', errors='replace').splitlines()[-n:])
    except OSError:
        return '(chưa có log)'


# ------------------------------------------------------------------ backup

def backup(data: Path | None = None, label='manual') -> Path:
    data = data or default_data_dir()
    target = data / 'backups' / f'{label}_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    target.mkdir(parents=True, exist_ok=True)
    db = data / 'trendflow.db'
    if db.is_file():
        with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as s, sqlite3.connect(target / 'trendflow.db') as d:
            s.backup(d)
        with sqlite3.connect(target / 'trendflow.db') as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                fail('Bản sao lưu database bị lỗi — dừng lại để bảo vệ dữ liệu.')
    if (data / '.env').is_file():
        shutil.copy2(data / '.env', target / '.env')
    return target


# ------------------------------------------------------------------ git update

def git(root: Path, *args, check=True) -> str:
    r = subprocess.run(['git', *args], cwd=str(root), capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and r.returncode:
        raise RuntimeError(f'git {" ".join(args)}: {(r.stderr or r.stdout).strip()[:400]}')
    return r.stdout.strip()


def smoke_test(code_dir: Path, db_copy: Path | None, py: Path) -> tuple[bool, str]:
    """Import the NEW code against a COPY of the database (runs its migration on the copy)."""
    with tempfile.TemporaryDirectory(prefix='tf_smoke_') as td:
        data = Path(td)
        if db_copy and db_copy.is_file():
            shutil.copy2(db_copy, data / 'trendflow.db')
        env = dict(os.environ, TRENDFLOW_DATA_DIR=str(data), AUTO_SCAN='false', PYTHONUTF8='1')
        env.pop('TRENDFLOW_DB', None)
        r = subprocess.run([str(py), '-c', 'import app; print("SMOKE_OK", app.VERSION)'], cwd=str(code_dir), env=env,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        out = (r.stdout + r.stderr)[-1500:]
        return (r.returncode == 0 and 'SMOKE_OK' in r.stdout), out


def update(root: Path = ROOT, data: Path | None = None, assume_yes=False, check_only=False, restart=True,
           remote='origin', branch=None, install_deps=True) -> str:
    data = data or default_data_dir()
    if not (root / '.git').exists():
        fail(f'{root} không phải Git repository. Cài theo hướng dẫn README (git clone) để dùng `trendflow update`.')
    branch = branch or git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
    if branch == 'HEAD':
        fail('Đang ở trạng thái detached HEAD — chạy `git checkout main` rồi cập nhật lại.')
    dirty = [ln for ln in git(root, 'status', '--porcelain', '--untracked-files=no').splitlines() if ln.strip()]
    if dirty:
        fail('Có thay đổi chưa commit trong mã nguồn — KHÔNG cập nhật để tránh ghi đè:\n  ' + '\n  '.join(dirty) +
             '\nHãy commit/stash các thay đổi này (hoặc báo Claude) rồi chạy lại.')
    say('Đang kiểm tra phiên bản mới…')
    last = None
    for attempt in range(4):
        try:
            git(root, 'fetch', remote, branch, '--tags')
            break
        except RuntimeError as exc:
            last = exc
            time.sleep(2 ** attempt)
    else:
        fail(f'Không tải được thông tin cập nhật: {last}')
    local, upstream = git(root, 'rev-parse', 'HEAD'), git(root, 'rev-parse', f'{remote}/{branch}')
    cur_v = (root / 'VERSION').read_text(encoding='utf-8').strip() if (root / 'VERSION').is_file() else '?'
    if local == upstream:
        say(f'✓ Đang dùng bản mới nhất: {cur_v} ({local[:7]}).')
        return 'up-to-date'
    if subprocess.run(['git', 'merge-base', '--is-ancestor', local, upstream], cwd=str(root)).returncode != 0:
        fail('Mã nguồn trên máy có commit riêng không có trên GitHub (lịch sử đã tách nhánh). '
             'Không tự cập nhật — gửi kết quả `git log --oneline -5` cho Claude.')
    try:
        new_v = git(root, 'show', f'{upstream}:VERSION').strip()
    except RuntimeError:
        new_v = '?'
    changes = git(root, 'log', '--oneline', '--no-merges', f'{local}..{upstream}')
    say(f'Phiên bản hiện tại: {cur_v} ({local[:7]})\nPhiên bản mới:     {new_v} ({upstream[:7]})\nThay đổi:\n  ' +
        '\n  '.join(changes.splitlines()[:20]))
    if check_only:
        return 'available'
    if not assume_yes and input('Cập nhật ngay? [y/N] ').strip().lower() not in ('y', 'yes', 'c', 'co', 'có'):
        say('Đã huỷ, không thay đổi gì.')
        return 'cancelled'

    bk = backup(data, label=f'update_{cur_v}_to_{new_v}')
    say(f'✓ Đã sao lưu database + cấu hình: {bk}')
    py = venv_python(root)
    if install_deps and git(root, 'diff', '--name-only', local, upstream, '--', 'requirements.txt'):
        say('Cài thư viện mới…')
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(git(root, 'show', f'{upstream}:requirements.txt'))
        r = subprocess.run([str(py), '-m', 'pip', 'install', '-q', '-r', f.name], capture_output=True, text=True)
        os.unlink(f.name)
        if r.returncode:
            fail('Cài thư viện thất bại — chưa đổi gì trong mã nguồn:\n' + r.stderr[-800:])

    say('Kiểm tra bản mới trên BẢN SAO dữ liệu trước khi kích hoạt…')
    wt = Path(tempfile.mkdtemp(prefix='tf_update_')) / 'code'
    git(root, 'worktree', 'add', '--detach', str(wt), upstream)
    try:
        ok, out = smoke_test(wt, bk / 'trendflow.db', py)
    finally:
        git(root, 'worktree', 'remove', '--force', str(wt), check=False)
        shutil.rmtree(wt.parent, ignore_errors=True)
    if not ok:
        fail('Bản mới KHÔNG qua kiểm tra — giữ nguyên bản đang dùng.\n' + out)
    say('✓ Bản mới chạy được.')

    was_running = bool(find_running(data))
    if was_running and restart:
        stop(data, quiet=True)
    git(root, 'merge', '--ff-only', upstream)
    (data / 'logs').mkdir(parents=True, exist_ok=True)
    with open(data / 'logs' / 'update.log', 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now().isoformat(timespec="seconds")} {cur_v} ({local[:7]}) -> {new_v} ({upstream[:7]}) backup={bk}\n')
    if was_running and restart:
        try:
            start(root, data, open_browser=False)
        except SystemExit:
            say('✕ Bản mới không khởi động được — quay lại bản cũ…')
            git(root, 'reset', '--keep', local)     # tree is clean (checked above): safe, keeps any local files
            start(root, data, open_browser=False)
            fail(f'Đã khôi phục bản {cur_v}. Dữ liệu đã sao lưu tại {bk}.')
    say(f'✓ Đã cập nhật lên {new_v}.')
    return 'updated'


# ------------------------------------------------------------------ status

def status(root: Path = ROOT, data: Path | None = None) -> dict:
    data = data or default_data_dir()
    running = find_running(data)
    commit = ''
    if (root / '.git').exists():
        try:
            commit = git(root, 'rev-parse', '--short', 'HEAD')
        except RuntimeError:
            pass
    v = (root / 'VERSION').read_text(encoding='utf-8').strip() if (root / 'VERSION').is_file() else '?'
    db = data / 'trendflow.db'
    say(f'TrendFlow {v} {("(" + commit + ")") if commit else ""}')
    say(f'Mã nguồn : {root}')
    say(f'Dữ liệu  : {data}' + (f' · database {db.stat().st_size / 1e6:.1f} MB' if db.is_file() else ' · (chưa có database)'))
    say(f"Server   : {'đang chạy — http://127.0.0.1:%d' % running['port'] if running else 'không chạy'}")
    return {'version': v, 'commit': commit, 'running': bool(running), 'port': running and running['port']}


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(prog='trendflow', description='TrendFlow — khởi động, dừng, cập nhật.')
    ap.add_argument('command', nargs='?', default='start',
                    choices=['start', 'open', 'stop', 'restart', 'status', 'update', 'backup', 'logs', 'version'])
    ap.add_argument('-y', '--yes', action='store_true', help='không hỏi xác nhận khi cập nhật')
    ap.add_argument('--check', action='store_true', help='update: chỉ kiểm tra có bản mới không')
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args(argv)
    data = default_data_dir()
    if a.command in ('start', 'open'):
        start(ROOT, data, open_browser=not a.no_browser)
    elif a.command == 'stop':
        stop(data)
    elif a.command == 'restart':
        stop(data, quiet=True)
        start(ROOT, data, open_browser=not a.no_browser)
    elif a.command == 'status':
        status(ROOT, data)
    elif a.command == 'update':
        update(ROOT, data, assume_yes=a.yes, check_only=a.check)
    elif a.command == 'backup':
        say(f'✓ Đã sao lưu: {backup(data)}')
    elif a.command == 'logs':
        say(log_tail(data / 'logs' / 'server.log', 60))
    elif a.command == 'version':
        say((ROOT / 'VERSION').read_text(encoding='utf-8').strip())


if __name__ == '__main__':
    main()
