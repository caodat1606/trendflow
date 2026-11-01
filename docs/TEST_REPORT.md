# TrendFlow 3.0 — Báo cáo kiểm thử

Môi trường kiểm thử: container Linux (4 vCPU, Python 3.13, FFmpeg 6.1), **không phải Mac M3 Pro**.
Mạng của container **chặn** báo (VnExpress, Tuổi Trẻ, BBC…), Wikipedia/Wikimedia Commons, Openverse,
Edge TTS, Google gTTS và kho model Ollama. Vì vậy phần dưới tách rõ ĐÃ KIỂM vs CHƯA KIỂM.

## A. Đã kiểm thử — có bằng chứng

| Hạng mục | Cách kiểm | Kết quả |
|---|---|---|
| Unit + integration | `pytest tests` — 37 test | 37/37 đạt |
| Tách câu tiếng Việt, mỗi từ thuộc đúng 1 cảnh | test số thập phân, "TP. HCM", câu dài | đạt |
| Nhận diện tên riêng (heuristic) | bỏ "Kính"/"Giá" đầu câu, giữ "DSCOVR", "Việt Nam" | đạt |
| Phân loại giấy phép, chính sách strict/open/editorial | CC BY-NC/ND/không rõ bị loại | đạt |
| Chấm điểm ảnh: khớp tên, loại logo, ảnh đại diện Wikipedia | test + E2E | đạt |
| Cổng ảnh thật (thiếu ảnh / slideshow 1 ảnh / ảnh chiếm >35%) | test + E2E kịch bản A | **từ chối, không dựng video** |
| Kiểm tra số liệu so với nguồn | số bịa (5.000) bị chặn | đạt |
| Edge TTS gửi `boundary="WordBoundary"` + căn mốc thời gian | edge-tts giả lập (không có mạng) | đạt |
| Chuỗi dự phòng TTS + mốc thời gian từng câu | Edge lỗi → engine kế tiếp (espeak thật) | đạt |
| Vòng "đo audio thật → AI chỉnh độ dài" | LLM giả lập, audio espeak thật: 1 lần <45 s → lần 2 đạt 45–60 s | đạt |
| Huỷ job đang dựng khung hình | job huỷ, FFmpeg bị kill, không còn file .mp4 dở | đạt |
| Migrate DB 2.7 → 3.0 | DB mô phỏng schema 2.7 có dữ liệu | dữ liệu cũ y nguyên, có backup, nguồn bị tắt vẫn tắt |
| API: duyệt cần đủ cổng, sửa bị khoá sau duyệt, sắp xếp, xoá khỏi Pending, xoá dự án (giữ bài gốc), batch bỏ qua mục không hợp lệ | TestClient | đạt (bắt được 2 lỗi thật và đã sửa: crash khi chưa có fact-check; mục Pending không có hash vẫn được đưa vào sản xuất) |
| **E2E qua HTTP API** (`tests/e2e_offline.py`) | kịch bản A: thiếu ảnh → lỗi rõ ràng, không có MP4, không duyệt được. Kịch bản B: Lưu → Test → cổng → Duyệt → Pending → Sản xuất hàng loạt → Thành phẩm | đạt |
| MP4 cuối thực tế | 1080×1920, H.264/AAC, 50,0 s; âm lượng −17,7 dB; không có khoảng lặng ≥1,5 s | đạt |
| **Hình khớp lời** trên MP4 thật | giải mã khung tại thời điểm đọc mỗi câu, so tương quan với ảnh được gán: 0,97–0,999; với ảnh khác ≤ 0,20 | 8/8 câu đúng |
| Mắt người xem | Claude đã xem contact sheet + khung 1080p đầy đủ + dạng sóng có vạch ranh giới câu | ảnh đúng chủ thể từng câu; phụ đề tiếng Việt đúng dấu; vạch ranh giới câu rơi đúng khoảng nghỉ giữa các câu |
| Bẫy trong bộ ảnh thử | logo Hubble, ảnh ghi "Eileen Collins" nhưng CC BY-NC, ảnh scan tài liệu, ảnh mèo/cà phê | không ảnh nào được chọn |
| Giao diện (Chromium/Playwright) | Editor, Thành phẩm, Cài đặt, Khám phá; Back/Forward | hiển thị đúng, không lỗi JavaScript, Back/Forward đúng URL |
| Trình cài đặt | HOME giả lập có bản 2.7 | cài thư mục mới, chép DB bằng backup API, migrate có backup; cài lại không ghi đè DB/.env; checksum DB cũ không đổi |
| Thời gian dựng | container 4 vCPU | xem thử 720p ≈ 60 s; bản cuối 1080p ≈ 135 s cho video 50 s |

Dữ liệu E2E: bài viết thử (tổng hợp sự kiện lịch sử có thật, gắn nhãn "dữ liệu thử"), ảnh thật public-domain/CC0
từ bộ mẫu scikit-image với metadata trung thực, giọng espeak-ng (giọng máy, chỉ để thử — Edge bị chặn).

## B. CHƯA kiểm thử — cần chạy trên Mac

1. **Ollama/Qwen thật**: viết kịch bản, chỉnh độ dài, trích thực thể theo câu (prompt chưa chạy với qwen3:8b thật).
2. **Edge TTS thật** (Nam Minh/Hoài My) và việc căn mốc WordBoundary trên giọng thật; gTTS thật; giọng macOS "Linh".
3. **Tìm ảnh thật** trên Wikipedia/Commons/Openverse cho tin thật; tỉ lệ tin qua được cổng ảnh thật.
4. Đọc bài báo gốc thật (bộ chọn HTML cho VnExpress/Tuổi Trẻ/Thanh Niên/BBC/Guardian…).
5. Model nhìn ảnh (qwen2.5vl) — bước tuỳ chọn.
6. Tốc độ và nhiệt trên M3 Pro 18 GB; `VIDEO_ENCODER=videotoolbox`.
7. Trình cài trên macOS thật (`cp -c` APFS clone, `say`, Homebrew).
8. Quét RSS quốc tế mới thêm (chưa xác nhận từng feed còn hoạt động).

## C. Rủi ro đã biết
- Tin không có thực thể chụp được (VD: chính sách thuế chung chung) có thể không qua cổng ảnh → hệ thống báo
  "không đạt", không xuất video. Đây là hành vi cố ý.
- Ảnh Wikimedia là **ảnh tư liệu** về người/địa điểm/sự kiện, không phải lúc nào cũng là ảnh của đúng sự kiện mới.
  Ảnh đúng sự kiện thường chỉ có trong bài báo gốc (bản quyền toà soạn) — chỉ dùng khi bạn chọn chế độ "editorial".
- Ollama chưa chạy → trích thực thể dùng heuristic (chỉ tên viết hoa), chất lượng chọn ảnh thấp hơn.

## D. Bản 3.0.1 — sửa theo video thật #76 (Netflix/PUBG) do người dùng chạy trên Mac
Phân tích video 76: 45,0 s nhưng có 11 khoảng lặng 0,7–1,2 s (≈10 s im lặng, ~22%), giọng gTTS vì Edge lỗi
`NoAudioReceived`; lời thoại toàn câu cụt; 5/11 câu dùng ảnh "chủ thể chính" (Netflix/Donald Glover cho câu về
PUBG/Krafton), câu "7 kỷ lục Guinness" khớp nhầm ảnh sự kiện kỷ lục Guinness ở Dublin.

| Sửa | Đã kiểm |
|---|---|
| Cắt khoảng lặng đầu/cuối từng câu, nghỉ 0,30 s sau dấu chấm | test: audio có đệm 0,5 s mỗi đầu → tổng ngắt nghỉ < 1,2 s cho 3 câu; E2E: 2% |
| Edge lỗi cả bài → thử Edge từng câu trước khi rơi về gTTS | code path; chưa có Edge thật |
| edge-tts ≥ 7.3.1 (Microsoft đổi endpoint; 7.3.x dùng máy chủ mới), installer luôn nâng cấp | đã đọc diff 7.2.3→7.3.1; chưa kết nối thật |
| Ngày "30-10" đọc thành "30 tháng 10" (chỉ phần đọc, phụ đề giữ nguyên) | test |
| Prompt: văn nói liền mạch, cấm chuỗi câu cụt | chưa chạy với Qwen thật |
| Tên phụ trong câu (Guinness) không đủ để chọn ảnh | test |
| Câu có chủ thể riêng nhưng không có ảnh → ảnh minh hoạ chung (có nhãn "Ảnh minh hoạ") thay vì ảnh chủ thể khác | test điểm & nhãn; luồng tìm thật cần Mac + Ollama |
| QA mới: tỉ lệ ngắt nghỉ (cảnh báo nếu > 18%) | E2E |
| `diagnose_tts.command`: kiểm tra phiên bản, giờ hệ thống, DNS, VPN, thử giọng vi/en | cú pháp; chạy thật trên Mac |

## E. Bản 3.1.0 — ảnh THẬT của tin (lựa chọn B của người dùng)
Lý do: video 76 thật ghép ảnh BLACKPINK (sự kiện "PUBG Mobile x BLACKPINK" trên Wikimedia) vào tin Netflix/PUBG,
và cầu Cổng Vàng vào câu về Netflix — chọn ảnh theo từ khoá không đảm bảo ảnh thuộc đúng tin.

| Thay đổi | Đã kiểm |
|---|---|
| Mặc định chỉ lấy ảnh trong **bài gốc + bài của báo khác cùng sự kiện** (±4 ngày, chung từ khoá/tên riêng) | E2E kịch bản C: ảnh từ VnExpress + Tuổi Trẻ; bài không liên quan (có ảnh bẫy) bị loại; test unit: cùng báo / khác chuyện bị loại |
| Ghép ảnh–câu theo **chú thích ảnh**, còn lại theo thứ tự trong bài | E2E: 8/8 câu khớp chú thích; Claude xem contact sheet: đúng chủ thể từng câu |
| Không chèn ảnh "lấp chỗ" chủ thể khác giữa câu dài | phát hiện khi xem contact sheet (Eileen Collins chen vào câu Hubble) → đã sửa, kiểm lại bằng mắt |
| Credit lấy từ chính bài lấy ảnh ("Ảnh: VnExpress") — không lấy từ cache | test + E2E (lỗi credit "Wikimedia" do cache đã bắt được và sửa) |
| Lọc ảnh trùng giữa các báo (dHash), bỏ logo/avatar/ảnh nhỏ | E2E (ảnh đại diện trùng của Tuổi Trẻ bị bỏ) |
| Không có ảnh / chỉ 1 ảnh → không xuất video | test |
| Tách tên ở dấu phẩy ("PUBG, Donald Glover") | test |
| Hiển thị **%** tiến độ (Kịch bản, Pending, danh sách), logo về trang chủ | Playwright: thấy "35%", bấm logo → /discover |
| Windows: font Arial/Segoe, xoá file khi Windows đang khoá không làm hỏng job | font chưa chạy thật trên Windows |

Chưa kiểm: đọc HTML thật của từng báo (VnExpress/Tuổi Trẻ/Thanh Niên/BBC…) để lấy ảnh + chú thích — cần chạy trên máy người dùng.

## F. Bản 3.1.1 — theo video 76 trên Windows
| Thay đổi | Đã kiểm |
|---|---|
| Ảnh og:image (chú thích = tiêu đề bài) không còn được tính là "khớp chú thích" — trước đó 1 ảnh chiếm 62% | E2E: đúng ảnh 8/8 câu |
| Cân bằng: ưu tiên ảnh ít dùng; 1 ảnh tối đa ~40% nếu có ảnh khác khớp | E2E |
| Thêm bài báo cùng sự kiện qua Bing News RSS (vi + en), chỉ lấy ảnh trong các bài đó | test parser + so khớp sự kiện; **chưa chạy với Bing thật** |
| Prompt cấm câu sáo rỗng, mỗi câu một thông tin mới | chưa chạy với Qwen thật |
| Edge: thử lại 3 lần (0 / 2,5 / 6 s) mỗi yêu cầu; gTTS dự phòng đọc nhanh hơn 15% | code; Edge thật chưa xác nhận |
| `diagnose_tts.bat` / `diagnose_tts.py` (Windows + Mac): so sánh 7 cách gọi Edge | chạy thử được (Edge bị chặn ở môi trường test) |
| Windows: bỏ log rác WinError 10054; file video/ảnh nhận HEAD (hết 405) | test HEAD |

## G. Bản 3.2.0 — giọng Nam Minh bắt buộc; loại ảnh "chuyện khác"
| Thay đổi | Đã kiểm |
|---|---|
| Edge gọi qua tiến trình riêng `python -m edge_tts` (đúng đường lệnh test đã chạy được trên Mac), từng câu, thử lại 3 lần; dự phòng gọi trong tiến trình | test với CLI giả lập; **Edge thật chưa xác nhận** |
| Azure Speech (API chính thức, cùng giọng Nam Minh) khi có key | test request/SSML giả lập; **chưa gọi Azure thật** |
| Mặc định KHÔNG BAO GIỜ rơi xuống gTTS/giọng máy; báo lỗi rõ + hướng dẫn | test |
| Nút "Nghe thử giọng" trong Cài đặt | cú pháp JS |
| Ảnh có chú thích chỉ nói về người/tác phẩm ngoài tin (Spider-Man, Naruto…) bị loại; ảnh bài phụ không dùng để "lấp chỗ" | E2E: ảnh bẫy bị loại với lý do rõ ràng |

## H. Bản 3.2.1 — thêm Gemini TTS
- Edge vẫn lỗi trên Windows của người dùng kể cả khi gọi kiểu dòng lệnh → nhiều khả năng Microsoft chặn endpoint miễn phí từ mạng/máy này.
- Thêm Gemini TTS (API Google AI Studio): tự thử model `gemini-3.1-flash-tts-preview` → `gemini-2.5-flash-preview-tts` → `gemini-2.5-flash-tts`, PCM 24 kHz → WAV, tự chờ khi 429. Test với API giả lập; **chưa gọi Gemini thật** (máy test bị chặn), chất lượng tiếng Việt cần nghe thử.
- Edge lỗi một lần → bỏ qua Edge 30 phút nếu có Azure/Gemini (đỡ mất ~10 s mỗi video).

## I. Bản 3.2.2 — sửa lỗi giọng Edge (sau khi diagnose_tts trên Windows của người dùng: 8/8 PASS)
Nguyên nhân tìm được trong code (không phải do Microsoft):
1. `current_voice()` chỉ so lời thoại + tên giọng, KHÔNG so nhà cung cấp → file gTTS cũ bị dùng lại mãi.
2. `_single()` chỉ giữ lỗi của lần thử cuối (lỗi CLI bị che), không log theo câu.
3. `_EDGE_DOWN_UNTIL` (3.2.1) tắt Edge 30 phút sau 1 lỗi; chế độ "Tự động" có thể chuyển sang Gemini (giọng khác).
4. Khi tạo giọng mới thất bại, giao diện vẫn hiện audio/video cũ như kết quả hiện tại.
5. Không huỷ được trong lúc chờ thử lại / lúc tiến trình CLI đang chạy.

| Sửa | Đã kiểm |
|---|---|
| Mỗi câu: tối đa 4 vòng (chờ 0/2/5/10 s), mỗi vòng thử in-process rồi CLI; timeout 45 s/lần; log từng lần (câu, vòng, cách, thời gian, lỗi) | test unit (lỗi rồi thành công; log đúng thứ tự) |
| Không còn đánh dấu Edge hỏng sau 1 lần lỗi | test |
| "Tự động" = chỉ giọng Nam Minh/Hoài My (Edge, Azure nếu có key); Gemini chỉ khi chọn rõ; gTTS chỉ khi bật | test |
| Audio chỉ được dùng lại khi khớp lời thoại + giọng + nhà cung cấp + cấu hình; nếu không, tạo mới | test + pipeline test |
| Audio/video cũ bị ẩn (kèm lý do) thay vì hiển thị như kết quả hiện tại; cổng duyệt chặn | test API |
| Huỷ: kiểm tra trong lúc chờ (0,2 s) và kill tiến trình CLI | test: huỷ < 1,5 s; pipeline test: dừng 0,5 s sau khi bấm |
| QA mới `audio_is_current_voice`: âm thanh MP4 khớp đúng MP3 mới (tương quan đường bao âm lượng ≥ 0,9) | E2E + pipeline test: 1.000 |
| `test_tts_pipeline.py/.bat`: chạy quy trình thật trên BẢN SAO dữ liệu, ghi LOG.txt + result.json + MP3/MP4 ra Desktop | chạy trong sandbox: 22/22 PASS (Edge CLI được thay bằng espeak vì sandbox chặn Edge; DB nguồn không đổi) |

**Chưa có kết quả thật với Edge trên Windows của người dùng** — cần chạy `test_tts_pipeline.bat` và gửi LOG.txt + result.json.

### Kết quả THẬT trên Windows 11 của người dùng (31/10/2026 23:57, Python 3.12.10, edge-tts 7.3.1, dự án 76)
- `test_tts_pipeline.bat`: **22/22 PASS**. Giọng: Edge `vi-VN-NamMinhNeural`, 59,9 s cho 216 từ, 11/11 câu có tiếng (−15…−17 dB).
- Nguyên nhân gốc được log xác nhận: Edge **từ chối ngẫu nhiên một số câu** (`NoAudioReceived` ở câu 9 và 10, cả in-process lẫn CLI), rồi nhận ở lần thử lại. Code cũ bỏ cuộc → rơi gTTS → audio gTTS bị dùng lại. Retry từng câu đã xử lý (câu 9 qua ở vòng 2/CLI, câu 10 ở vòng 1/CLI).
- Video xem thử dùng đúng MP3 mới (Claude tự đo lại trên file người dùng gửi: tương quan âm thanh 1.000, cùng −16,1 dB); QA khung hình đạt; huỷ khi đang tạo audio dừng sau 6,5 s, không ghi đè audio đang dùng.
- Còn tồn tại (ngoài phạm vi TTS): ảnh Spider-Man/Naruto (credit Tuổi Trẻ) vẫn xuất hiện ở câu 1, 5, 11.

## J. Bản 3.3.0 — ảnh: A (bài gốc) + B (tìm web), MỌI ảnh phải qua AI kiểm tra
- Bỏ lấy ảnh từ bài "cùng sự kiện" (nguồn của ảnh lạc đề). Nhóm A = ảnh trong bài gốc; nhóm B = tìm ảnh trên web
  (Bing Images mặc định; Google Images qua SerpAPI nếu có key — Google Custom Search API đã đóng với người dùng mới).
- `tf_verify.py`: AI nhìn ảnh (Gemini nếu có key, không thì Ollama `qwen2.5vl`) trả relevant/score/ảnh thể hiện gì/hợp câu nào.
  Chỉ nhận khi relevant=true và điểm ≥ 6. Không có AI nhìn ảnh → báo lỗi kèm cách cài, KHÔNG dùng ảnh chưa kiểm.
- Ghép: mỗi câu chọn ảnh AI gắn với câu đó, điểm cao nhất, ít lặp; bằng điểm ưu tiên ảnh bài gốc (A/B).
- Cổng: mọi ảnh dùng phải `verdict.relevant`; < 3 ảnh đạt → không xuất video.
- Đã kiểm (sandbox, phán quyết AI giả lập từ file vì sandbox không có Gemini/VLM): ảnh bẫy trong chính bài gốc bị loại;
  web trả 3 ảnh → 1 đạt, 2 bị loại; 8/8 câu đúng ảnh; 62 test; `test_tts_pipeline` 23/23.
- **Chưa kiểm thật**: Bing Images thật, Gemini vision thật, qwen2.5vl thật (độ chính xác và tốc độ trên máy người dùng).

## K. Bản 3.3.1 — sau lần chạy thật: Gemini ReadTimeout (60 s) làm hỏng cả job
- Lỗi mạng/timeout/429/5xx khi AI kiểm 1 ảnh = tạm thời: thử lại 4 lần (chờ 0/3/8/15 s, timeout đọc 90 s); vẫn lỗi → **bỏ đúng ảnh đó** (không dùng khi chưa kiểm), kiểm tiếp ảnh khác. Chỉ dừng job khi AI không phản hồi 3 ảnh liên tiếp (báo rõ).
- Gemini: tắt "thinking" (`thinkingBudget: 0`) cho câu hỏi có/không → nhanh hơn; tự bỏ tuỳ chọn nếu model không hỗ trợ.
- Sửa: ảnh trong cache bỏ qua kiểm tra kích thước tối thiểu.
- Script test: tự tạo lại thư mục kết quả nếu bị xoá giữa chừng.
- Đã kiểm: 65 test (timeout rồi thành công; 1 ảnh lỗi → bỏ ảnh đó, video vẫn đạt; 3 lỗi liên tiếp → dừng rõ ràng); E2E; test_tts_pipeline 23/23 (sandbox).

## L. Bản 3.3.2 — sau lần chạy thật thứ 2 (Gemini free tier: 429 liên tục, ~3 phút/ảnh)
- Giọng (thật, Windows): 11/11 câu Edge Nam Minh, 0 lần thử lại, 10 s. ✅
- Gemini: **gộp 6 ảnh/1 request** (24 lượt kiểm → ~4 request), giãn nhịp ≥ 7 s/request, chờ đúng `retryDelay` khi 429,
  nhận diện **hết hạn mức trong ngày** → dừng ngay kèm hướng dẫn (AI trên máy hoặc bật thanh toán).
- Job dừng giữa chừng vẫn lưu nhật ký chọn ảnh + lý do từng ảnh bị loại (trước đây mất).
- Script test: không còn báo nhầm kế hoạch ảnh CŨ (bản 3.1) như kết quả mới.
- Đã kiểm: 67 test (1 request cho 3 ảnh; hết quota ngày; retryDelay 23 s); E2E; test_tts_pipeline 23/23 (sandbox).

## M. Bản 3.3.3 — chọn AI kiểm ảnh từng lần + tự chuyển sang AI trên máy
- Lần chạy thật: Gemini báo hết hạn mức ngày → job dừng dù người dùng đã cài AI trên máy. Đã sửa:
  - "Tự động" = Gemini → AI trên máy. Gemini hết hạn mức / từ chối key / không phản hồi → **chuyển sang AI trên máy và kiểm lại đúng các ảnh đang dở**; ghi "⇄ … chuyển sang …" vào nhật ký. Hết hạn mức ngày → 3 giờ sau "Tự động" mới thử Gemini lại.
  - Menu chọn AI kiểm ảnh ngay trên trang kịch bản (Tự động / Gemini / AI trên máy), lựa chọn chưa cài bị khoá.
  - Nhận model trên máy theo họ (qwen2.5vl:latest, qwen2.5vl:7b, qwen3-vl…, llama3.2-vision, gemma3, minicpm-v, llava).
- Đã kiểm: 71 test (chuyển Gemini→máy với cùng ảnh; nhận diện model; tham số verifier tới job); E2E; giao diện (Playwright, không lỗi JS).
- Chưa kiểm thật: tốc độ/độ chính xác qwen2.5vl trên máy người dùng.

## N. Bản 3.4.0 — chọn ảnh theo CHỦ THỂ từng câu (theo feedback 6,5/10)
Feedback: câu về Donald Glover / Stephen Glover / quá trình sản xuất vẫn hiện ảnh quảng bá/gameplay PUBG; phụ đề hơi to.
- Mỗi câu xác định chủ thể chính (+1 chủ thể phụ) trước; tìm ảnh web RIÊNG cho từng chủ thể (người trước).
- AI trả về ảnh thể hiện chủ thể nào + loại ảnh. NGƯỜI chỉ được xác nhận khi ảnh được tìm theo tên đó, trang/chú thích
  ghi tên, và AI xác nhận là ảnh người thật — không nhận diện bằng khuôn mặt; ghi rõ "xác nhận qua nguồn ảnh ghi tên".
- Gán: chủ thể chính → chủ thể phụ → ảnh liên quan rộng (ghi rõ "liên quan rộng — chưa có ảnh X", cảnh báo ở cổng duyệt).
- Phụ đề: chữ nhỏ hơn ~15%, tối đa 2 dòng, đặt thấp hơn (≈73% chiều cao) để không che chủ thể.
- Đã kiểm: 72 test, gồm test tái hiện tin PUBG (Donald Glover → ảnh tìm theo tên; Stephen Glover không có ảnh → "liên quan rộng"
  có ghi chú; PUBG → ảnh game; Netflix → ảnh Netflix; ảnh rác bị loại); E2E (câu Apollo 11 không có ảnh → chủ thể phụ Mặt Trăng);
  test_tts_pipeline 23/23 (sandbox); Claude xem khung hình phụ đề mới.
- Chưa kiểm thật: chất lượng tìm ảnh theo tên trên Bing thật + AI thật (Gemini/qwen2.5vl) với tin PUBG.
