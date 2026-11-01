# Changelog

## 3.6.0 — Visual Studio: chọn ảnh thủ công theo đoạn

- **Mặc định chuyển sang chế độ "Tự chọn (Studio)"** trong trang biên tập kịch bản:
  - Lời thoại tự chia thành 5–10 đoạn, dùng mốc thời gian thật của giọng Nam Minh (nút "🎙 Tạo giọng") hoặc ước tính nếu chưa có giọng.
  - Gộp, tách, chia lại N đoạn; giữ ảnh đã chọn khi có thể.
  - Từ khoá gợi ý theo tên riêng trong câu; nút "✦ Gợi ý từ khoá" dùng Ollama nếu có, không bắt buộc.
  - Mỗi đoạn chọn 1–3 ảnh: đổi thứ tự, bấm vào ảnh để đặt điểm focus giữ chủ thể trong khung 9:16, sửa credit.
- **Nguồn ảnh không scrape:**
  - Ảnh bài gốc và báo cùng sự kiện.
  - Openverse, Wikimedia Commons (API).
  - Pexels (key miễn phí).
  - Google Images qua SerpAPI (key).
  - Tải ảnh từ máy, dán link ảnh.
- **Render dùng đúng ảnh đã chọn**, cắt ảnh tại mốc thời gian từ trong đoạn. QA kiểm tra từng đoạn hiện đúng ảnh.
  - Đổi ảnh sau khi xem thử thì video xem thử cũ bị ẩn và phải xem thử lại mới duyệt được.
- Gemini/AI nhìn ảnh **không còn bắt buộc**; chế độ "Tự động (AI)" cũ vẫn chọn được trong trang biên tập hoặc Cài đặt.
- Bỏ đọc trang kết quả Bing Images (scraping) ở chế độ tự động; chỉ dùng SerpAPI nếu có key.
- Database: thêm bảng `visual_selections` (chỉ thêm; tự sao lưu trước khi nâng cấp).
- Cài đặt: chế độ chọn ảnh, Pexels API key.

## 3.5.0 — Phase 1: Developer Experience

- Repo Git chuẩn: `.gitignore` chặn `.env`, DB, media, venv, cache.
- Code và dữ liệu tách riêng:
  - Biến `TRENDFLOW_DATA_DIR` (mặc định `~/TrendFlowData`).
  - Module `tf_paths.py`.
  - API `/api/ping`.
- Lệnh `trendflow` dùng được ở mọi thư mục:
  - `start`, `stop`, `restart`, `status`, `update`, `backup`, `logs`, `version`.
  - Hai bản `bin/trendflow.cmd` và `bin/trendflow`.
- `trendflow update`:
  - Từ chối khi có thay đổi chưa commit hoặc lịch sử lệch.
  - Backup trước khi cập nhật.
  - Smoke test trên bản sao DB.
  - Chỉ `--ff-only`, tự rollback bằng `reset --keep`.
  - Không bao giờ dùng `reset --hard`.
- `scripts/setup_windows.ps1`:
  - Tạo venv.
  - Đặt PATH và `TRENDFLOW_DATA_DIR`.
  - Tạo shortcut.
  - Chép dữ liệu từ bản ZIP cũ, không ghi đè.
- Script cài/chạy cũ dạng ZIP được chuyển vào `legacy/`.
- Thêm test `tests/test_cli.py`: update, dirty tree, bản lỗi, lịch sử lệch, check-only, backup, server thật, migrate.

## 3.4.0

- Chọn ảnh theo chủ thể từng câu (A = ảnh bài gốc, B = ảnh tìm trên web), có AI xác nhận.
- Phụ đề nhỏ hơn, tối đa 2 dòng.
