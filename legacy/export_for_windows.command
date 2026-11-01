#!/usr/bin/env bash
# Đóng gói TOÀN BỘ dữ liệu TrendFlow trên Mac thành 1 file ZIP trên Desktop để chuyển sang Windows.
# Không sửa, không xoá gì trên Mac. Database được chép bằng SQLite backup API (bản chép nhất quán).
# Dùng: bash ~/TrendFlow_3/export_for_windows.command   [thư_mục_TrendFlow]
set -euo pipefail
SRC="${1:-$(cd "$(dirname "$0")" && pwd)}"
[[ -f "$SRC/trendflow.db" ]] || { echo "Không thấy $SRC/trendflow.db"; exit 1; }
if pgrep -f "uvicorn app:app" >/dev/null 2>&1; then
  echo "TrendFlow đang chạy. Hãy tắt (Control+C trong cửa sổ đang chạy) rồi chạy lại lệnh này."; exit 1
fi
PY="$SRC/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
OUTDIR="${EXPORT_DIR:-$HOME/Desktop}"; mkdir -p "$OUTDIR"
OUT="$OUTDIR/TrendFlow_data_$(date +%Y%m%d_%H%M).zip"
"$PY" - "$SRC" "$OUT" <<'PYEXPORT'
import sqlite3, sys, tempfile, zipfile, hashlib, json
from pathlib import Path
src, out = Path(sys.argv[1]), Path(sys.argv[2])
with tempfile.TemporaryDirectory() as td:
    db = Path(td) / 'trendflow.db'
    with sqlite3.connect(f'file:{src / "trendflow.db"}?mode=ro', uri=True) as s, sqlite3.connect(db) as d:
        s.backup(d)
    with sqlite3.connect(db) as d:
        ok = d.execute('PRAGMA integrity_check').fetchone()[0]
        counts = {t: d.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                  for t in ('trends', 'script_projects', 'workflow_items') }
    if ok != 'ok':
        raise SystemExit('Database lỗi: ' + ok)
    files = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        z.write(db, 'data/trendflow.db')
        if (src / '.env').is_file():
            z.write(src / '.env', 'data/.env')
        for folder in ('media_library', 'voice_outputs', 'video_outputs', 'backups'):
            for f in sorted((src / folder).rglob('*')) if (src / folder).is_dir() else []:
                if f.is_file() and not f.name.startswith('.') and '.partial' not in f.name:
                    # MP4/MP3/JPG are already compressed
                    z.write(f, f'data/{folder}/{f.relative_to(src / folder).as_posix()}',
                            compress_type=zipfile.ZIP_STORED if f.suffix.lower() in ('.mp4', '.mp3', '.jpg', '.png') else zipfile.ZIP_DEFLATED)
                    files += 1
        z.writestr('data/EXPORT_INFO.json', json.dumps({'rows': counts, 'files': files}, ensure_ascii=False))
    with zipfile.ZipFile(out) as z:
        bad = z.testzip()
    if bad:
        raise SystemExit('ZIP lỗi ở ' + bad)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    print(f'Database OK · tin: {counts["trends"]}, kịch bản: {counts["script_projects"]} · {files} file media')
    print(f'Kích thước: {out.stat().st_size / 1e6:.1f} MB · SHA256 (16 ký tự đầu): {sha}')
PYEXPORT
echo "✓ Đã tạo: $OUT"
echo "Chép file này sang Windows (USB / Google Drive / OneDrive). Dữ liệu trên Mac vẫn giữ nguyên."
