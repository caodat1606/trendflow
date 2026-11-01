# TrendFlow 3.0 — Cài đặt trên MacBook Pro M3 Pro (18 GB)

## 1. Chuẩn bị (một lần)
1. **Homebrew** (nếu chưa có): https://brew.sh
2. Mở Terminal:
   ```bash
   brew install python@3.12 ffmpeg
   ```
3. **Ollama**: tải ở https://ollama.com/download, mở ứng dụng, rồi:
   ```bash
   ollama pull qwen3:8b
   ```
   Tuỳ chọn (lọc ảnh bằng AI nhìn ảnh, cần thêm ~6 GB):
   ```bash
   ollama pull qwen2.5vl:7b
   ```
4. Tuỳ chọn — giọng dự phòng offline: System Settings → Accessibility → Spoken Content → System Voice →
   Manage Voices → Vietnamese → **Linh**.

## 2. Cài TrendFlow 3.0
Giải nén ZIP vào `~/Downloads`, rồi:
```bash
bash ~/Downloads/TrendFlow_3.0/install_mac.sh
```
Trình cài:
- cài vào **thư mục mới** `~/TrendFlow_3` — bản cũ `~/Downloads/trendflow_m1` **không bị sửa**;
- **chép** database cũ bằng SQLite backup (bản gốc giữ nguyên), chép `.env`, `video_outputs`, `voice_outputs`;
- khi khởi động lần đầu, TrendFlow tự sao lưu DB vào `~/TrendFlow_3/backups/` trước khi thêm bảng mới
  (chỉ thêm bảng/cột, không xoá dữ liệu).

Bản cũ ở chỗ khác? `bash install_mac.sh ~/TrendFlow_3 /đường/dẫn/bản_cũ`

## 3. Mở ứng dụng
Nhấp đúp `~/TrendFlow_3/start_mac.command` (lần đầu: chuột phải → Open). Script tự chọn cổng trống
8001–8020 và mở trình duyệt. Tắt: đóng cửa sổ Terminal hoặc Control+C.

Lần đầu vào **Cài đặt** → bấm **Kiểm tra kết nối** để chắc chắn Mac truy cập được báo, Wikipedia/Wikimedia,
Openverse và Edge TTS.

## 4. Quy trình
1. **Khám phá tin** → **Tạo kịch bản**. AI đọc bài gốc, viết lời thoại, **đo giọng đọc thật** và tự chỉnh để đạt 45–60 s.
2. **Kịch bản**: sửa lời nếu muốn → **Tạo video xem thử**. TrendFlow tự tìm ảnh thật cho **từng câu**, dựng video
   720p và tự kiểm tra (khung hình, âm thanh). Danh sách bên trái cho thấy ảnh nào đi với câu nào.
3. Nếu mọi mục ✓ → **Duyệt → Pending** (nội dung và hình bị khoá).
4. **Pending**: kéo ⠿ hoặc ↑↓ để sắp thứ tự, **Sản xuất hàng loạt**, **Huỷ** từng clip, **Dừng tất cả**, **Xoá khỏi Pending**.
5. **Thành phẩm**: tải MP4 1080p, file "Nguồn & caption" (nguồn tin + ghi công từng ảnh + caption), ảnh kiểm tra.

Không đủ ảnh thật → TrendFlow **không** xuất video và nói rõ câu nào thiếu ảnh (không còn thẻ chữ thay ảnh).

## 5. Quyền dùng ảnh (Cài đặt)
- **open** (mặc định): CC0 / Public domain / CC BY / CC BY-SA từ Wikipedia, Wikimedia Commons, Openverse — có ghi công.
- **strict**: bỏ CC BY-SA.
- **editorial**: thêm ảnh của chính bài báo gốc. Đây là ảnh bản quyền của toà soạn; ghi nguồn **không** tự cho phép
  dùng lại. Chỉ bật khi bạn chấp nhận trách nhiệm pháp lý.

## 6. Quay lại bản cũ (rollback)
Bản cũ không bị đụng tới:
```bash
cd ~/Downloads/trendflow_m1
source .venv/bin/activate
python3 -m uvicorn app:app --host 127.0.0.1 --port 8001
```
Bản sao lưu DB của TrendFlow 3: `~/TrendFlow_3/backups/`. Bản sao lưu code khi cài lại: `~/TrendFlow_Backups/`.

## 7. Kiểm thử trên Mac (tuỳ chọn)
```bash
cd ~/TrendFlow_3 && .venv/bin/pip install -r requirements-dev.txt && brew install espeak-ng
```
```bash
cd ~/TrendFlow_3 && .venv/bin/python -m pytest -q tests
```
```bash
cd ~/TrendFlow_3 && .venv/bin/python tests/e2e_offline.py /tmp/tf_e2e
```

## 8. Xử lý sự cố
| Hiện tượng | Cách xử lý |
|---|---|
| "Không kết nối được Ollama" | Mở ứng dụng Ollama; `ollama list` phải có `qwen3:8b` |
| "Đang dùng giọng dự phòng" / `NoAudioReceived` | Chạy lại `install_mac.sh` (nâng edge-tts ≥ 7.3.1), rồi nhấp đúp `~/TrendFlow_3/diagnose_tts.command` và gửi file kết quả trên Desktop |
| "KHÔNG ĐẠT ảnh thật" | Tin thiếu chủ thể có ảnh. Viết lại câu nêu rõ tên người/địa điểm, bấm **↻ Tìm ảnh lại**, hoặc chọn tin khác |
| Render chậm | Thêm `VIDEO_ENCODER=videotoolbox` vào `~/TrendFlow_3/.env` |
| Cổng bận | `start_mac.command` tự chọn cổng khác |
