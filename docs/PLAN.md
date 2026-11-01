# Kế hoạch: Manual Visual Selection Studio

## Audit nhanh (3.4.0)

**Giữ lại**

- `tf_tts`: Edge Nam Minh tổng hợp theo từng câu, có mốc thời gian từng từ.
- `tf_render`: plan gồm segments → shots → media; Ken Burns, crossfade, phụ đề.
- `tf_jobs`: hàng đợi, nút huỷ, hiện %.
- `tf_qa`, cổng duyệt (approval hash).

**Bỏ hoặc chuyển thành tuỳ chọn**

- `tf_imagesearch` scrape Bing Images, vi phạm yêu cầu không scraping.
  Bing Search API cũng đã ngừng từ 11/08/2025.
- `tf_verify` (Gemini/Ollama Vision) không còn bắt buộc; chỉ còn trong chế độ Auto tuỳ chọn.

## Phase 1 — Developer Experience ✅ (3.5.0)

Xem `CHANGELOG.md`.

## Phase 2 — Visual Studio UI

- AI chia lời thoại thành 5–10 đoạn, dựa trên câu và mốc thời gian thật của file voice.
  Có thể gộp hoặc tách đoạn.
- Mỗi đoạn có:
  - Lời thoại và thời lượng.
  - Từ khoá gợi ý do AI viết, sửa được.
  - Ô tìm ảnh, lưới kết quả.
  - 1–3 ảnh đã chọn: kéo đổi thứ tự, crop/focus 9:16, ghi credit.
- Nguồn ảnh, đúng điều khoản dịch vụ:
  1. Ảnh của bài gốc và các bài cùng sự kiện, lấy từ og:image và ảnh trong bài, có credit.
  2. Openverse API, Wikimedia Commons API (miễn phí, có giấy phép).
  3. Tuỳ chọn có key: Pexels, Brave Image Search, SerpAPI.
  4. Tải ảnh từ máy, hoặc dán URL ảnh.
- Lưu lựa chọn vào DB (migration chỉ thêm, không sửa/xoá dữ liệu cũ).
  Không bắt buộc Gemini.

## Phase 3 — Render

- Plan lấy trực tiếp từ lựa chọn trong Studio, chia thời lượng theo voice thật.
- Preview đoạn đơn lẻ nhanh; render final qua hàng đợi hiện có.
- Đoạn thiếu ảnh → chặn duyệt, báo rõ đoạn nào.

## Phase 4 — QA

- Test API, test DB migration, e2e offline với fixture ảnh chọn tay.
- Kiểm tra khung hình khớp từng đoạn và audio khớp MP3.
- Chạy thử trên Windows.
