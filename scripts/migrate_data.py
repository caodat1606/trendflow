"""Copy personal data from an old ZIP install (e.g. C:\\Users\\<you>\\TrendFlow_3) into the data folder.
COPIES only (the old folder stays as a fallback). Never overwrites an existing database in the target.
Usage: python scripts/migrate_data.py <old_install_folder> <data_folder>"""
import shutil
import sqlite3
import sys
from pathlib import Path


def main(old: str, data: str) -> int:
    src, dst = Path(old), Path(data)
    dst.mkdir(parents=True, exist_ok=True)
    if not (src / 'trendflow.db').is_file():
        print(f'Không thấy database cũ ở {src} — bỏ qua bước chuyển dữ liệu.')
        return 0
    if (dst / 'trendflow.db').exists():
        print(f'Đã có database ở {dst} — KHÔNG ghi đè. (Muốn chuyển lại: đổi tên file đó trước.)')
        return 0
    with sqlite3.connect(f'file:{src / "trendflow.db"}?mode=ro', uri=True) as s, sqlite3.connect(dst / 'trendflow.db') as d:
        s.backup(d)
    with sqlite3.connect(dst / 'trendflow.db') as c:
        ok = c.execute('PRAGMA integrity_check').fetchone()[0]
        n = c.execute('SELECT COUNT(*) FROM script_projects').fetchone()[0]
    if ok != 'ok':
        print('Bản sao database lỗi:', ok)
        return 1
    if (src / '.env').is_file() and not (dst / '.env').exists():
        shutil.copy2(src / '.env', dst / '.env')
    copied = 0
    for folder in ('media_library', 'voice_outputs', 'video_outputs', 'backups'):
        if (src / folder).is_dir():
            for f in (src / folder).rglob('*'):
                if f.is_file():
                    t = dst / folder / f.relative_to(src / folder)
                    if not t.exists():
                        t.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, t)
                        copied += 1
    print(f'✓ Đã chép dữ liệu: {n} kịch bản, {copied} file. Thư mục cũ {src} giữ nguyên.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
