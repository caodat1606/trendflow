# TrendFlow

Tạo video tin tức tiếng Việt 9:16 (45–60 giây) với giọng Edge **vi-VN-NamMinhNeural**, phụ đề karaoke và ảnh thật.

## Cài lần đầu (Windows 11)

```powershell
winget install -e --id Git.Git
git clone https://github.com/<owner>/trendflow.git $HOME\TrendFlow
powershell -ExecutionPolicy Bypass -File $HOME\TrendFlow\scripts\setup_windows.ps1
```

Mở **PowerShell mới** rồi gõ `trendflow`.
Script cài đặt tự chép dữ liệu cũ từ `%USERPROFILE%\TrendFlow_3` sang `%USERPROFILE%\TrendFlowData` và không bao giờ ghi đè database đã có.

## Lệnh

| Lệnh | Việc |
|---|---|
| `trendflow` | Bật server (nếu chưa chạy) và mở trình duyệt |
| `trendflow stop` | Tắt server |
| `trendflow restart` | Khởi động lại |
| `trendflow status` | Đang chạy? cổng, phiên bản, thư mục dữ liệu |
| `trendflow update` | Cập nhật từ GitHub (có backup, smoke test, tự lùi lại nếu lỗi) |
| `trendflow update --check` | Chỉ xem có bản mới không |
| `trendflow backup` | Sao lưu database + .env |
| `trendflow logs` | Xem log server |
| `trendflow version` | Phiên bản |

## Code và dữ liệu tách riêng

- Code: `%USERPROFILE%\TrendFlow` (Git repo).
- Dữ liệu: `%USERPROFILE%\TrendFlowData` (biến `TRENDFLOW_DATA_DIR`), gồm:
  - `trendflow.db`, `.env`
  - `media_library/`, `voice_outputs/`, `video_outputs/`
  - `backups/`, `logs/`

Dữ liệu **không bao giờ** nằm trong Git (xem `.gitignore`).

## `trendflow update` làm gì

1. Từ chối nếu có thay đổi local chưa commit, hoặc nếu lịch sử bị lệch.
   Không bao giờ dùng `reset --hard`.
2. `git fetch` và hiện danh sách thay đổi.
3. Sao lưu DB + `.env` vào `TrendFlowData\backups\update_<thời gian>`.
4. Smoke test bản mới trong một worktree tạm, chạy trên **bản sao** của DB.
   Lỗi thì dừng, bản đang dùng giữ nguyên.
5. `pip install` chỉ khi `requirements.txt` đổi; sau đó `git merge --ff-only`.
6. Khởi động lại server. Nếu không lên được, tự quay về commit cũ bằng `git reset --keep`.

Log nằm ở `TrendFlowData\logs\update.log`.

## Quy trình phát triển

Claude sửa code trên branch riêng, mở Pull Request. Bạn duyệt và merge vào `main`, rồi chạy `trendflow update`.

## Dev / test

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q
python tests/e2e_offline.py
```

Tài liệu khác nằm trong `docs/`: `PLAN.md`, `AUDIT_REPORT.md`, `TEST_REPORT.md`, `README_WINDOWS.md`.
