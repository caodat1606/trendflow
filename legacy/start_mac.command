#!/usr/bin/env bash
# Double-click to start TrendFlow. Picks a free port (8001–8020) and opens the browser.
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
[[ -x .venv/bin/python ]] || { echo "Chưa cài. Chạy install_mac.sh trước."; read -r -p "Nhấn Enter để đóng"; exit 1; }
PORT=""
for p in $(seq 8001 8020); do
  if ! lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then PORT=$p; break; fi
done
[[ -n "$PORT" ]] || { echo "Không còn cổng trống 8001–8020."; exit 1; }
if ! curl -s -m 2 http://127.0.0.1:11434/api/version >/dev/null; then
  echo "! Ollama chưa chạy — mở ứng dụng Ollama để AI viết kịch bản (vẫn xem được tin & video cũ)."
  open -a Ollama 2>/dev/null || true
fi
echo "TrendFlow: http://127.0.0.1:$PORT  (đóng cửa sổ này hoặc Control+C để tắt)"
( sleep 2; open "http://127.0.0.1:$PORT/discover" ) &
exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
