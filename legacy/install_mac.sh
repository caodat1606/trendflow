#!/usr/bin/env bash
# TrendFlow 3.0 installer for macOS (Apple Silicon).
# SAFE BY DESIGN:
#  - Installs into a NEW folder (default ~/TrendFlow_3). Your old install is never modified.
#  - Your old database is COPIED with SQLite's backup API (consistent copy), never moved or edited.
#  - Old videos/audio are copied with APFS clones (instant, no extra disk space) when possible.
#  - Re-running only replaces program files; data (trendflow.db, .env, media) is never overwritten.
# Usage: bash install_mac.sh [TARGET_DIR] [OLD_INSTALL_DIR]
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$HOME/TrendFlow_3}"
OLD="${2:-$HOME/Downloads/trendflow_m1}"
BACKUPS="$HOME/TrendFlow_Backups"
STAMP="$(date +%Y%m%d_%H%M%S)"
CODE_FILES=(app.py index.html tf_article.py tf_verify.py tf_imagesearch.py tf_db.py tf_jobs.py tf_llm.py tf_media.py tf_news.py tf_qa.py tf_render.py tf_script.py tf_text.py tf_tts.py
            requirements.txt requirements-dev.txt start_mac.command diagnose_tts.command export_for_windows.command import_data.py free_port.py diagnose_tts.py diagnose_tts.bat test_tts_pipeline.py test_tts_pipeline.bat start_windows.bat install_windows.ps1 README_WINDOWS.md README_MAC.md AUDIT_REPORT.md TEST_REPORT.md .env.example)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

say_step(){ printf '\n\033[1m▶ %s\033[0m\n' "$1"; }
fail(){ printf '\n\033[31m✕ %s\033[0m\n' "$1"; exit 1; }

say_step "Kiểm tra gói cài đặt"
for f in "${CODE_FILES[@]}"; do [[ -f "$SRC/$f" ]] || fail "Thiếu file trong gói cài: $f"; done
[[ "$SRC" != "$TARGET" ]] || fail "Hãy chạy trình cài từ thư mục giải nén, không phải từ thư mục đích."

say_step "Kiểm tra công cụ hệ thống"
PY=""
for c in python3.12 python3.13 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)'; then PY="$(command -v $c)"; break; fi
done
[[ -n "$PY" ]] || fail "Cần Python ≥ 3.10. Cài: brew install python@3.12  (Homebrew: https://brew.sh)"
echo "Python: $PY ($("$PY" --version))"
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  if command -v brew >/dev/null; then
    read -r -p "Chưa có FFmpeg. Cài bằng Homebrew ngay? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] && brew install ffmpeg || fail "Cần FFmpeg: brew install ffmpeg"
  else fail "Cần FFmpeg. Cài Homebrew (https://brew.sh) rồi: brew install ffmpeg"; fi
fi
echo "FFmpeg: $(ffmpeg -version | head -1)"


say_step "Cài chương trình vào $TARGET"
mkdir -p "$TARGET"
if [[ -f "$TARGET/app.py" ]]; then
  mkdir -p "$BACKUPS/code_$STAMP"
  for f in "${CODE_FILES[@]}"; do [[ -f "$TARGET/$f" ]] && cp -p "$TARGET/$f" "$BACKUPS/code_$STAMP/$f"; done
  echo "Đã sao lưu code cũ của TrendFlow 3 vào $BACKUPS/code_$STAMP"
fi
for f in "${CODE_FILES[@]}"; do cp -p "$SRC/$f" "$TARGET/$f"; done
mkdir -p "$TARGET/tests"; cp -Rp "$SRC/tests/." "$TARGET/tests/"
chmod +x "$TARGET/start_mac.command" "$TARGET/diagnose_tts.command" "$TARGET/export_for_windows.command"

say_step "Chép dữ liệu từ bản cũ (bản cũ giữ nguyên)"
if [[ -f "$TARGET/trendflow.db" ]]; then
  echo "Đã có database tại $TARGET/trendflow.db — KHÔNG ghi đè."
elif [[ -f "$OLD/trendflow.db" ]]; then
  "$PY" - "$OLD/trendflow.db" "$TARGET/trendflow.db" <<'PYDB'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(f'file:{src}?mode=ro', uri=True) as s, sqlite3.connect(dst) as d:
    s.backup(d)
with sqlite3.connect(dst) as d:
    ok = d.execute('PRAGMA integrity_check').fetchone()[0]
if ok != 'ok':
    raise SystemExit('Bản sao database lỗi: ' + ok)
print('Đã chép database (integrity_check = ok). Bản gốc ở bản cũ không bị thay đổi.')
PYDB
  [[ -f "$OLD/.env" && ! -f "$TARGET/.env" ]] && cp -p "$OLD/.env" "$TARGET/.env" && echo "Đã chép .env"
  for d in video_outputs voice_outputs; do
    if [[ -d "$OLD/$d" && ! -e "$TARGET/$d" ]]; then
      cp -cRp "$OLD/$d" "$TARGET/$d" 2>/dev/null || cp -Rp "$OLD/$d" "$TARGET/$d"
      echo "Đã chép $d (APFS clone nếu có thể)"
    fi
  done
else
  echo "Không thấy bản cũ ở $OLD — TrendFlow 3 sẽ tạo database mới."
fi
[[ -f "$TARGET/.env" ]] || cp "$TARGET/.env.example" "$TARGET/.env"

say_step "Môi trường Python (.venv)"
[[ -x "$TARGET/.venv/bin/python" ]] || "$PY" -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/python" -m pip install -q --upgrade pip
"$TARGET/.venv/bin/python" -m pip install -q -r "$TARGET/requirements.txt"
"$TARGET/.venv/bin/python" -m pip install -q --upgrade "edge-tts>=7.3.1,<8"
"$TARGET/.venv/bin/python" -c "import fastapi, edge_tts, PIL, feedparser, bs4, gtts; print('Thư viện Python: OK')"

say_step "Kiểm tra Ollama (AI chạy trên máy)"
if command -v ollama >/dev/null; then
  MODELS="$(ollama list 2>/dev/null || true)"
  grep -q "qwen3:8b" <<<"$MODELS" && echo "✓ qwen3:8b đã có" || echo "! Cần model viết: ollama pull qwen3:8b   (~5 GB)"
  grep -Eq "qwen2.5vl|qwen3-vl" <<<"$MODELS" && echo "✓ Có model nhìn ảnh" || echo "· Tuỳ chọn (lọc ảnh tốt hơn): ollama pull qwen2.5vl:7b   (~6 GB)"
else
  echo "! Chưa có Ollama. Tải tại https://ollama.com/download rồi chạy: ollama pull qwen3:8b"
fi
if say -v '?' 2>/dev/null | grep -q '^Linh '; then echo "✓ Có giọng dự phòng macOS 'Linh'"; else echo "· Tuỳ chọn: System Settings → Accessibility → Spoken Content → System Voice → Manage Voices → Vietnamese (Linh)"; fi

printf '\n\033[32m✓ Đã cài TrendFlow 3.0 vào %s\033[0m\n' "$TARGET"
echo "Mở: nhấp đúp $TARGET/start_mac.command  (hoặc: bash \"$TARGET/start_mac.command\")"
echo "Bản cũ vẫn ở $OLD và chạy được như trước nếu cần quay lại."
