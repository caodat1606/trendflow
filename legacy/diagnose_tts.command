#!/usr/bin/env bash
# Chẩn đoán Edge TTS. Nhấp đúp hoặc: bash ~/TrendFlow_3/diagnose_tts.command
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
OUT="$HOME/Desktop/TrendFlow_TTS_check.txt"
{
echo "=== TrendFlow TTS check $(date) ==="
echo "macOS: $(sw_vers -productVersion 2>/dev/null)"
echo "edge-tts: $($PY -c 'import edge_tts;print(edge_tts.__version__)' 2>&1)"
echo "Giờ máy (UTC): $(date -u '+%Y-%m-%d %H:%M:%S')"
echo "Giờ Internet: $(curl -sI -m 8 https://www.microsoft.com | grep -i '^date:')"
echo "--- DNS speech.platform.bing.com:"; (dscacheutil -q host -a name speech.platform.bing.com 2>/dev/null | grep ip_address) || echo "không phân giải được"
echo "--- Proxy/VPN:"; scutil --nwi 2>/dev/null | grep -iE "utun|ipsec|ppp" | head -3 || true
for V in vi-VN-NamMinhNeural en-US-GuyNeural; do
  echo "--- Thử giọng $V:"
  rm -f /tmp/tf_tts_test.mp3
  $PY -m edge_tts --voice "$V" --text "Xin chào, đây là bài kiểm tra giọng đọc." --write-media /tmp/tf_tts_test.mp3 2>&1 | tail -3
  if [[ -s /tmp/tf_tts_test.mp3 ]]; then echo "OK: $(wc -c < /tmp/tf_tts_test.mp3) byte"; else echo "THẤT BẠI"; fi
done
echo "--- Danh sách giọng tiếng Việt:"; $PY -m edge_tts --list-voices 2>&1 | grep -i "vi-VN" | head -5
} 2>&1 | tee "$OUT"
echo; echo "Đã lưu kết quả vào: $OUT  — gửi file này cho Claude."
[[ -s /tmp/tf_tts_test.mp3 ]] && afplay /tmp/tf_tts_test.mp3 2>/dev/null
read -r -p "Nhấn Enter để đóng"
