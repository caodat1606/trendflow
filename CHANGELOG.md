# Changelog

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
