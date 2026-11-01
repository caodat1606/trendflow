"""Tests for the `trendflow` command (start/stop/status/update/backup) and data migration."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
import trendflow_cli as cli  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')


def sh(cwd, *args):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, env=GIT_ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def make_db(path: Path, rows=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE script_projects(id INTEGER PRIMARY KEY, title TEXT)')
        db.executemany('INSERT INTO script_projects(title) VALUES(?)', [(f'p{i}',) for i in range(rows)])


@pytest.fixture()
def repos(tmp_path):
    """origin (bare) <- dev (Claude's side) ; install = the user's clone."""
    origin, dev, install = tmp_path / 'origin.git', tmp_path / 'dev', tmp_path / 'install'
    sh(tmp_path, 'git', 'init', '--bare', '-b', 'main', str(origin))
    sh(tmp_path, 'git', 'clone', str(origin), str(dev))
    sh(dev, 'git', 'checkout', '-b', 'main')

    def release(version, app_src='import os\nVERSION = open("VERSION").read().strip()\n'):
        (dev / 'VERSION').write_text(version + '\n')
        (dev / 'app.py').write_text(app_src)
        (dev / 'requirements.txt').write_text('# none\n')
        sh(dev, 'git', 'add', '-A')
        sh(dev, 'git', 'commit', '-m', f'release {version}')
        sh(dev, 'git', 'push', '-u', 'origin', 'main')
    release('1.0.0')
    sh(tmp_path, 'git', 'clone', str(origin), str(install))
    data = tmp_path / 'data'
    make_db(data / 'trendflow.db')
    (data / '.env').write_text('GEMINI_API_KEY=secret\n')
    return {'dev': dev, 'install': install, 'data': data, 'release': release}


def test_update_fast_forwards_with_backup(repos):
    repos['release']('1.1.0')
    assert cli.update(repos['install'], repos['data'], assume_yes=True, install_deps=False) == 'updated'
    assert (repos['install'] / 'VERSION').read_text().strip() == '1.1.0'
    backups = list((repos['data'] / 'backups').iterdir())
    assert len(backups) == 1 and (backups[0] / 'trendflow.db').is_file() and (backups[0] / '.env').is_file()
    with sqlite3.connect(repos['data'] / 'trendflow.db') as db:      # user data untouched
        assert db.execute('SELECT COUNT(*) FROM script_projects').fetchone()[0] == 3
    assert cli.update(repos['install'], repos['data'], assume_yes=True, install_deps=False) == 'up-to-date'


def test_update_refuses_uncommitted_local_changes(repos):
    repos['release']('1.1.0')
    (repos['install'] / 'app.py').write_text('# my local edit\n')
    with pytest.raises(SystemExit):
        cli.update(repos['install'], repos['data'], assume_yes=True, install_deps=False)
    assert (repos['install'] / 'app.py').read_text() == '# my local edit\n'       # never overwritten
    assert (repos['install'] / 'VERSION').read_text().strip() == '1.0.0'


def test_broken_new_version_is_not_activated(repos):
    repos['release']('2.0.0', app_src='raise RuntimeError("broken release")\n')
    with pytest.raises(SystemExit):
        cli.update(repos['install'], repos['data'], assume_yes=True, install_deps=False)
    assert (repos['install'] / 'VERSION').read_text().strip() == '1.0.0'          # old version kept
    assert 'tf_update_' not in sh(repos['install'], 'git', 'worktree', 'list')       # temp worktree cleaned


def test_diverged_history_is_refused(repos):
    (repos['install'] / 'note.txt').write_text('x')
    sh(repos['install'], 'git', 'add', 'note.txt')
    sh(repos['install'], 'git', 'commit', '-m', 'local commit')
    repos['release']('1.1.0')
    with pytest.raises(SystemExit):
        cli.update(repos['install'], repos['data'], assume_yes=True, install_deps=False)
    assert (repos['install'] / 'VERSION').read_text().strip() == '1.0.0'


def test_check_only_reports_without_changing(repos, capsys):
    repos['release']('1.2.0')
    assert cli.update(repos['install'], repos['data'], check_only=True) == 'available'
    out = capsys.readouterr().out
    assert '1.0.0' in out and '1.2.0' in out
    assert (repos['install'] / 'VERSION').read_text().strip() == '1.0.0'


def test_backup_command(tmp_path):
    make_db(tmp_path / 'trendflow.db')
    b = cli.backup(tmp_path)
    with sqlite3.connect(b / 'trendflow.db') as db:
        assert db.execute('SELECT COUNT(*) FROM script_projects').fetchone()[0] == 3


def test_start_status_stop_real_server(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTO_SCAN', 'false')
    monkeypatch.setenv('OLLAMA_URL', 'http://127.0.0.1:9')
    info = cli.start(APP, tmp_path, open_browser=False, wait=60)
    try:
        assert info['app'] == 'TrendFlow' and info['data_dir'] == str(tmp_path)
        assert (tmp_path / 'trendflow.db').is_file()                         # database created in the DATA folder
        again = cli.start(APP, tmp_path, open_browser=False)                 # second call: no duplicate server
        assert again['port'] == info['port']
        assert cli.status(APP, tmp_path)['running']
    finally:
        assert cli.stop(tmp_path)
    assert cli.find_running(tmp_path) is None


def test_migrate_old_zip_install_copies_and_never_overwrites(tmp_path):
    old, data = tmp_path / 'TrendFlow_3', tmp_path / 'TrendFlowData'
    make_db(old / 'trendflow.db', rows=5)
    (old / 'video_outputs').mkdir()
    (old / 'video_outputs' / 'a.mp4').write_bytes(b'v')
    (old / '.env').write_text('K=1')
    py = sys.executable
    out = subprocess.run([py, str(APP / 'scripts' / 'migrate_data.py'), str(old), str(data)], capture_output=True, text=True)
    assert out.returncode == 0 and '5 kịch bản' in out.stdout
    assert (data / 'video_outputs' / 'a.mp4').is_file() and (old / 'trendflow.db').is_file()
    (old / 'trendflow.db').unlink()
    make_db(old / 'trendflow.db', rows=1)                                  # second run must not overwrite
    out = subprocess.run([py, str(APP / 'scripts' / 'migrate_data.py'), str(old), str(data)], capture_output=True, text=True)
    assert 'KHÔNG ghi đè' in out.stdout
    with sqlite3.connect(data / 'trendflow.db') as db:
        assert db.execute('SELECT COUNT(*) FROM script_projects').fetchone()[0] == 5
