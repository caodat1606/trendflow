> **Từ bản 3.5.0:** cài và cập nhật qua Git bằng lệnh `trendflow` (xem `README.md` ở thư mục gốc). Hướng dẫn ZIP bên dưới chỉ còn để tham khảo; các script cũ nằm trong `legacy/`.

# TrendFlow — chuyển từ Mac sang Windows 11

## A. Trên Mac (khoảng 5 phút)
1. Tắt TrendFlow: vào cửa sổ Terminal đang chạy → `Control + C`.
2. Đóng gói dữ liệu (database, ảnh, giọng, video) thành 1 file ZIP trên Desktop — không sửa gì trên Mac:
   `bash ~/Downloads/TrendFlow_3.0/export_for_windows.command ~/TrendFlow_3`
3. Chép 2 file sang Windows (USB, Google Drive, OneDrive…): `TrendFlow_data_<ngày>.zip` (Desktop Mac) và `TrendFlow_3.0.x.zip` (bản cài).

## B. Trên Windows 11
1. Để cả 2 file ZIP vào thư mục **Downloads**.
2. Chuột phải `TrendFlow_3.0.x.zip` → **Extract All…** → Extract (tạo `Downloads\TrendFlow_3.0`).
3. Mở **PowerShell** (Start → gõ PowerShell) và chạy:
   `powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\TrendFlow_3.0\install_windows.ps1"`
   - Nếu báo vừa cài Python hoặc FFmpeg: đóng PowerShell, mở cửa sổ mới, chạy lại đúng lệnh trên.
   - Trình cài tự tìm `TrendFlow_data_*.zip` trong Downloads/Desktop và nhập vào `C:\Users\<tên>\TrendFlow_3`.
4. Ollama: `winget install -e --id Ollama.Ollama`, mở Ollama, rồi `ollama pull qwen3:8b`.
5. Mở TrendFlow: nhấp đúp lối tắt **TrendFlow** trên Desktop (cửa sổ đen phải để mở trong lúc dùng).
6. Kiểm tra: **Cài đặt** → mọi dòng ✓, bấm **Kiểm tra kết nối**. **Thành phẩm/Kịch bản** phải thấy dự án cũ.

## Lưu ý
- Windows Defender/SmartScreen có thể hỏi khi chạy file tải về: chọn **More info → Run anyway** (chỉ với file TrendFlow).
- Giọng dự phòng macOS "Linh" không có trên Windows; Edge Neural vẫn là giọng chính.
- Dữ liệu trên Mac vẫn còn nguyên; chỉ nên làm việc ở MỘT máy để tránh hai database khác nhau.

## Cập nhật bản mới trên Windows
1. Tắt TrendFlow (đóng cửa sổ đen).
2. Giải nén ZIP mới vào Downloads (ghi đè thư mục `TrendFlow_3.0` cũ trong Downloads).
3. Chạy lại: `powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\TrendFlow_3.0\install_windows.ps1"`
   Trình cài chỉ thay code (có sao lưu code cũ), KHÔNG đụng database/ảnh/video.

## Giọng Nam Minh ổn định bằng Azure Speech (miễn phí 500.000 ký tự/tháng)
1. Vào https://portal.azure.com, đăng nhập tài khoản Microsoft (lần đầu Azure yêu cầu thẻ để xác minh; gói Free F0 không tính tiền).
2. **Create a resource** → tìm **Speech** (Azure AI services → Speech service) → **Create**.
3. Resource group: tạo mới `trendflow` · Region: **Southeast Asia** · Name: `trendflow-voice` · Pricing tier: **Free F0**.
4. **Review + create** → **Create** → **Go to resource** → **Keys and Endpoint** → copy **KEY 1**; ghi lại Location/Region (`southeastasia`).
5. TrendFlow → **Cài đặt** → dán key, region `southeastasia` → **Lưu cài đặt** → **♫ Nghe thử giọng**.

## Giọng Gemini (Google AI) — giọng khác Nam Minh, key dễ lấy
1. Vào https://aistudio.google.com, đăng nhập Google → **Get API key** → **Create API key** → copy.
2. TrendFlow → **Cài đặt** → dán vào **Gemini API key**, chọn giọng (Charon = nam, hợp tin tức) → **Lưu cài đặt** → **♫ Nghe thử giọng**.
3. Muốn chỉ dùng Gemini: **Dịch vụ giọng đọc** → "Chỉ Gemini TTS".
Gói miễn phí giới hạn số lần gọi/phút và /ngày; app tự chờ và thử lại khi bị giới hạn (lỗi 429).

## AI kiểm tra ảnh (bắt buộc từ bản 3.3)
Mọi ảnh (cả ảnh bài gốc lẫn ảnh tìm trên web) phải được AI xác nhận đúng nội dung tin. Chọn 1 trong 2:
- **Gemini (nhanh, khuyên dùng):** nhập Gemini API key trong Cài đặt (lấy ở https://aistudio.google.com → Get API key).
- **AI trên máy (miễn phí, chậm hơn, cần ~6 GB):** `ollama pull qwen2.5vl:7b` (máy yếu: `ollama pull qwen2.5vl:3b`).
Tìm ảnh web mặc định qua Bing Images; muốn dùng Google Images thì nhập SerpAPI key (serpapi.com).
