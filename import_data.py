"""Nhập ZIP dữ liệu (tạo bởi export_for_windows.command) vào thư mục TrendFlow. Chạy được trên Windows/Mac.
Không bao giờ ghi đè database đã có. Dùng: python import_data.py <TrendFlow_data_xxx.zip> <thư_mục_TrendFlow>"""
import json
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path, PurePosixPath


def main(zip_path: str, target: str) -> int:
    zp, tgt = Path(zip_path), Path(target)
    tgt.mkdir(parents=True, exist_ok=True)
    if (tgt / 'trendflow.db').exists():
        print(f'Đã có database tại {tgt / "trendflow.db"} — KHÔNG nhập đè. (Muốn nhập lại: đổi tên file đó trước.)')
        return 0
    with zipfile.ZipFile(zp) as z:
        bad = z.testzip()
        if bad:
            print('ZIP bị lỗi ở', bad)
            return 1
        names = [n for n in z.namelist() if n.startswith('data/') and not n.endswith('/')]
        if 'data/trendflow.db' not in names:
            print('ZIP không phải dữ liệu TrendFlow (thiếu data/trendflow.db).')
            return 1
        for n in names:
            rel = PurePosixPath(n).relative_to('data')
            if '..' in rel.parts or rel.is_absolute():
                continue
            dest = tgt.joinpath(*rel.parts)
            if rel.name == 'EXPORT_INFO.json' or (dest.exists() and rel.parts[0] != 'trendflow.db'):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, open(dest, 'wb') as out:
                shutil.copyfileobj(src, out)
        info = json.loads(z.read('data/EXPORT_INFO.json')) if 'data/EXPORT_INFO.json' in names else {}
    with sqlite3.connect(tgt / 'trendflow.db') as db:
        ok = db.execute('PRAGMA integrity_check').fetchone()[0]
        rows = db.execute('SELECT COUNT(*) FROM script_projects').fetchone()[0]
    if ok != 'ok':
        print('Database sau khi nhập bị lỗi:', ok)
        return 1
    expected = (info.get('rows') or {}).get('script_projects')
    print(f'✓ Đã nhập dữ liệu: {rows} kịch bản' + (f' (Mac: {expected})' if expected is not None else '')
          + f', {len(names) - 1} file.')
    return 0 if expected in (None, rows) else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
