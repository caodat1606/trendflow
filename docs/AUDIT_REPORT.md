# TrendFlow 2.7 — Báo cáo audit (Claude tiếp quản)

Phạm vi: đọc toàn bộ `app.py` (2084 dòng), `tf_workflow.py`, `tf_visual.py`, `tf_auto.py`,
`tf_evidence.py`, `tf_scene_sync.py`, `tf_tts.py`, `index.html`, `install_mac.sh`,
`README_MAC.md`, `TEST_REPORT.md`; phân tích video `TrendFlow_385_bad_output.mp4`
(ffprobe, trích khung hình, volumedetect/silencedetect).

## 1. Video 385 thực tế

| Đo được | Giá trị | Đánh giá |
|---|---|---|
| Thời lượng | 24,0 s (720×1280, H.264/AAC) | Không đạt 45–60 s |
| Hình | 100% thẻ gradient xanh + vòng tròn, ghi "ĐỒ HOẠ THÔNG TIN • KHÔNG PHẢI ẢNH SỰ KIỆN" | Không có ảnh thật nào |
| Tiêu đề trên khung | Lặp lại 1 câu "Hải Phòng và Quảng Ninh 'đến' Nhật Bản…" cho 6/8 khung | Không đổi theo câu |
| Âm thanh | Khoảng lặng ~1,0 s tại 7,0 s / 13,2 s / 18,6 s / 23,2 s | Dấu hiệu gTTS (dự phòng) ghép từng đoạn, Edge TTS đã thất bại |

## 2. Nguyên nhân gốc (xếp theo mức độ)

### P0 — Không thể có ảnh thật (thiết kế, không phải lỗi vặt)
1. `tf_evidence.verify_scene` chỉ nhận diện thực thể từ **một từ điển cứng ~20 khái niệm**
   (trump, putin, ukraine, refinery, drone_attack…). Mọi tin không thuộc danh sách này
   (VD: Liên hoan phim Tokyo, Hải Phòng, Quảng Ninh) → "Cảnh không ghi rõ nhân vật hoặc địa điểm"
   → **mọi ảnh đều bị loại**. Còn yêu cầu thêm 1 "hành động" trong danh sách EVENT cứng.
2. `licence_auto_ok` chỉ chấp nhận `provider == 'Wikimedia Commons'` và chỉ CC0/PD/CC BY
   → ảnh bài báo gốc (ảnh đúng sự kiện nhất) và CC BY-SA (phần lớn ảnh Commons) đều bị loại.
3. Chỉ tìm **1 truy vấn Commons/cảnh**; không dùng Wikipedia (ảnh đại diện thực thể), Openverse.
4. `tf_scene_sync.sync_scenes` gán `visual = voice` (câu tiếng Việt) khi lời thoại đổi
   → truy vấn tìm ảnh thành câu tiếng Việt dài → Commons trả về rác.
5. `auto_grounded_scene_queries` trả `[]` nếu LLM trả sai số lượng → mất truy vấn tiếng Anh.
6. Kiểm tra pixel bằng `qwen3-vl:4b` là **bắt buộc**; Mac chưa cài model → mọi ảnh bị loại.
7. Bản 2.7 "sửa" bằng cách cho `auto_assign` luôn ghi `passed=1` và dùng thẻ đồ hoạ
   → pipeline xuất MP4 "thành công" nhưng toàn chữ. Đây là cổng chất lượng bị vô hiệu hoá.

### P0 — Thời lượng 16–24 s
8. `ollama_chat` dùng `num_ctx: 4096` trong khi prompt viết kịch bản chứa tới 6.900 ký tự
   bài báo + hướng dẫn (~4.000+ token) và `num_predict` 3.200 → **prompt bị cắt / không còn chỗ
   cho đầu ra** → lời thoại ngắn.
9. Bản Test chấp nhận 3–180 s; không có vòng "đo audio thật → AI viết lại cho đủ độ dài" ở bản nháp
   (chỉ có trong code tự động cũ đã bị vô hiệu hoá).
10. `hook` không được đọc (TTS chỉ đọc `narration`) nhưng prompt bảo không lặp hook → lời thoại càng ngắn.

### P1 — Khớp hình–lời
11. edge-tts ≥ 7 mặc định `boundary="SentenceBoundary"`; code chỉ bắt chunk `WordBoundary`
    → **không bao giờ có mốc thời gian từ**; phụ đề và ranh giới cảnh chia theo số ký tự (ước đoán).
12. Ranh giới cảnh tính theo số từ của `scene.voice`, phụ đề tính theo nhóm ký tự → hai hệ thời gian
    khác nhau, cảnh có thể đổi giữa câu.
13. Bản cuối (`produce_approved`) **chạy lại tìm ảnh** từ đầu → hình bản cuối có thể khác bản Test
    đã duyệt; `approved_hash` không bao gồm hình.

### P1 — TTS
14. Edge lỗi (rỗng/403) → gTTS ghép đoạn có khoảng lặng 1 s; không báo rõ cho người dùng
    rằng đang dùng giọng dự phòng chất lượng thấp. Không có dự phòng offline (macOS có giọng "Linh").

### P1 — Tin tức
15. `FEEDS` chỉ có 12 nguồn **Việt Nam**; mọi nguồn khác bị gán `INTL`. Cài mới → 0 tin quốc tế,
    không thể đạt tỉ lệ 50/50.

### P2 — Kiến trúc / vận hành
16. `tf_workflow.install_workflow` **monkey-patch** module `app` lúc chạy (ghi đè `auto_run_one`,
    `workflow_require_draft`…); khó kiểm soát, dễ gãy khi sửa.
17. Preview chạy trong thread riêng, batch trong thread khác, cùng tranh `VIDEO_LOCK` với timeout 15–20 s
    → lỗi "Trình dựng video đang bận" ngẫu nhiên.
18. Huỷ preview: `cancel_check` chỉ ghi trạng thái, FFmpeg không bị dừng thật trong một số nhánh.
19. Render: ảnh tĩnh, không chuyển động, không chuyển cảnh; tiêu đề lặp; khung chữ chiếm 40% màn hình.
20. Sau khi duyệt, `produce_approved` gọi LLM kiểm chứng lại — LLM thất thường có thể làm hỏng
    bản đã duyệt; kiểm chứng nên làm ở bản nháp (hiển thị cho người duyệt).
21. Không có QA đầu ra: không kiểm tra âm lượng, khoảng lặng, khung hình thực tế có đúng ảnh không.
22. Installer ghi đè code vào thư mục cũ (`trendflow_m1`) → rollback phải chép tay;
    không có script chọn cổng trống (lỗi 8000/8001 đang dùng).
23. Giao diện: Storyboard là ô JSON thô — không tối giản, dễ làm hỏng dữ liệu.

## 3. Hướng sửa (TrendFlow 3.0)

Không vá từng chỗ; viết lại phần lõi sản xuất video, giữ nguyên phần quét tin & database.

| Vấn đề | Giải pháp 3.0 |
|---|---|
| 1–7 | Module `tf_media`: trích thực thể theo **từng câu** (LLM + heuristic, buộc phải có trong lời thoại/bài gốc), tìm nhiều nguồn: ảnh bài gốc (tuỳ chọn), Wikipedia (ảnh đại diện thực thể), Commons, Openverse; phân loại giấy phép; chấm điểm bằng mô tả/tiêu đề/chú thích; loại logo/bản đồ/tài liệu; khử trùng lặp bằng dHash; VLM là **tăng cường**, không bắt buộc. |
| 7 | Cổng ảnh thật: mọi câu phải có ảnh thật, ≥4 ảnh khác nhau, 1 ảnh ≤35% thời lượng. Không đạt → **không dựng**, báo từng câu. Không còn thẻ đồ hoạ thay ảnh. |
| 8–10 | `num_ctx` tự tính theo độ dài prompt; câu đầu là hook được đọc; vòng **đo MP3 thật → AI chỉnh số từ** (tối đa 3 lần) ngay khi tạo kịch bản. |
| 11–13 | Cảnh = câu. TTS trả mốc thời gian từng câu (Edge `WordBoundary` + căn chỉnh, hoặc tổng hợp từng câu). Kế hoạch cảnh (scene plan) lưu DB, có hash; **bản cuối dùng y nguyên plan đã duyệt**. |
| 14 | Edge → gTTS → giọng "Linh" của macOS; hiện rõ đang dùng giọng nào. |
| 15 | Thêm nguồn quốc tế (BBC, Guardian, Al Jazeera, NPR, The Verge, TechCrunch, Variety…). Chỉ **thêm** dòng, không sửa/xoá nguồn của bạn. |
| 16–18 | Một hàng đợi job duy nhất (`tf_jobs`), preview ưu tiên hơn batch; huỷ có checkpoint ở LLM/TTS/tải ảnh/từng khung hình và kill FFmpeg. |
| 19 | Render mới: ảnh full màn hình, Ken Burns mượt, crossfade, phụ đề theo từ đang đọc, credit ảnh nhỏ. |
| 21 | `tf_qa`: ffprobe, thời lượng, âm lượng, khoảng lặng, **trích khung tại giữa mỗi câu và so với ảnh được gán**, xuất contact sheet để người duyệt xem nhanh. |
| 22 | Cài song song vào thư mục mới, **chép bản sao** DB (SQLite backup API), không đụng bản cũ; `start_mac.command` tự chọn cổng trống. |
| 23 | Giao diện tối giản: danh sách câu kèm ảnh thu nhỏ tự động, không có JSON. |
