# HarvFlow Clips — Phase 0: Audit mã nguồn & Kiến trúc

> Brand: **HarvFlow** · Product: **HarvFlow Clips** · AI Video Repurposing Studio · *Create more. Edit less.*
> Ngày audit: 2026-11-01 · Nguồn audit: `caodat1606/trendflow`, nhánh `main` @ `283d432` (TrendFlow 3.6.0)
> Bổ sung: YouTube URL Import — thiết kế chi tiết ở [`YOUTUBE_IMPORT.md`](YOUTUBE_IMPORT.md).
> Trạng thái: **ĐỀ XUẤT — chờ duyệt.** Chưa sửa hay đổi tên bất kỳ dòng code nào của TrendFlow.

---

## 0. Tóm tắt cho người không đọc code

- TrendFlow hiện là app **làm video tin tức từ bài báo** (đọc RSS → AI viết lời → giọng đọc máy → ghép ảnh).
  HarvFlow Clips là app **cắt video dài thành clip ngắn** (nghe video → chép lời → AI chọn đoạn hay → phụ đề → cắt dọc 9:16).
  Hai việc này khác nhau gần như hoàn toàn ở phần "lõi xử lý".
- **Giữ lại được:**
  - toàn bộ **giao diện** (màu, sidebar, thẻ, nút, thanh %, thông báo);
  - cách chạy **hàng đợi tác vụ** (%, huỷ);
  - lệnh khởi động/cập nhật `trendflow` (sẽ thành `harvflow`);
  - cách **sao lưu database an toàn**;
  - phần kết nối **AI trên máy (Ollama)**;
  - phần **kiểm tra video đầu ra**.
- **Bỏ:**
  - quét tin RSS, đọc bài báo;
  - viết kịch bản tin;
  - giọng đọc máy (Edge/Azure/Gemini TTS);
  - tìm và kiểm tra ảnh;
  - Studio chọn ảnh.
- **Phải viết mới:**
  - nhập video;
  - chép lời bằng Whisper;
  - AI chọn clip;
  - trình sửa clip;
  - phụ đề Việt/Anh/song ngữ;
  - cắt khung dọc theo khuôn mặt;
  - xuất video;
  - lưu dự án trên Google Drive.
- **Đề xuất quan trọng nhất:** làm HarvFlow trong một **repo GitHub riêng** (`harvflow-clips`), khởi tạo từ code TrendFlow.
  TrendFlow vẫn chạy bình thường bằng lệnh `trendflow`, hai app không đụng nhau.

---

## 1. Hệ thống hiện tại (TrendFlow 3.6.0) — đã kiểm tra trên code thật

### 1.1 Kiến trúc

| Lớp | Hiện trạng | File |
|---|---|---|
| Frontend | 1 file SPA, JavaScript thuần, **không có bước build** (không cần Node). Router tự viết bằng `history.pushState`. Một số dòng dài hơn 1.300 ký tự. | `index.html` (278 dòng) |
| Backend | FastAPI + uvicorn, chạy local `127.0.0.1:8001+` | `app.py` (769) |
| Hàng đợi | 1 thread worker **chạy chung tiến trình với web server**; trạng thái job nằm trong SQLite; có %, huỷ, checkpoint | `tf_jobs.py` (619) |
| Database | SQLite WAL; migration chỉ thêm (ADD COLUMN/CREATE IF NOT EXISTS); tự backup bằng SQLite backup API trước khi nâng schema | `tf_db.py` (212) |
| Đường dẫn dữ liệu | Code và dữ liệu đã tách (`TRENDFLOW_DATA_DIR`, mặc định `~/TrendFlowData`) | `tf_paths.py` |
| Launcher/cập nhật | `trendflow start/stop/status/update/backup/logs`. Update: từ chối khi có sửa local, backup, smoke test trong git worktree trên bản sao DB, `--ff-only`, rollback bằng `reset --keep` | `trendflow_cli.py` (365), `bin/` |
| Cài đặt | Windows: `scripts/setup_windows.ps1`. Mac: chỉ còn script kiểu ZIP cũ trong `legacy/` | `scripts/`, `legacy/` |
| AI chữ | Client Ollama (stream, huỷ, sửa JSON hỏng, phát hiện model vision) | `tf_llm.py` (151) |
| Render | Pillow dựng **từng khung hình** trong Python rồi đẩy rawvideo qua pipe vào FFmpeg (libx264) | `tf_render.py` (402) |
| QA video | ffprobe, độ to, khoảng lặng, so audio MP4 với MP3, so khung hình với ảnh mong đợi, contact sheet | `tf_qa.py` (168) |
| Test | pytest: 85 test (core, jobs, CLI, studio) + `e2e_offline.py`. Cần `espeak-ng` + `ffmpeg` | `tests/` |

### 1.2 Phụ thuộc hiện tại

`fastapi, uvicorn, feedparser, requests, python-dotenv, edge-tts, gTTS, Pillow, beautifulsoup4`.
Hệ thống: Python 3.12/3.13, FFmpeg; Ollama là tuỳ chọn. Không cần Node, không cần GPU.

### 1.3 Tương thích Windows / macOS

| Hạng mục | Windows 11 | macOS (M3 Pro) |
|---|---|---|
| Server + UI | ✅ đã chạy thật trên máy anh (`trendflow`, 3.5.0) | ✅ đã chạy thật trước đây (bản ZIP 3.x) |
| Lệnh `trendflow` | ✅ `bin/trendflow.cmd` + PATH | ⚠️ có `bin/trendflow` (bash) nhưng **chưa có script cài cho Mac** theo kiểu Git |
| Khởi chạy nền | `creationflags` DETACHED + `taskkill /T` | `start_new_session` + `killpg` |
| Hack đường dẫn | — | `app.py` tự thêm `/opt/homebrew/bin` vào PATH |
| Mã hoá file script | `.ps1` phải UTF-8 BOM; `.cmd` ASCII + CRLF (đã xử lý) | — |

### 1.4 Nợ kỹ thuật (ảnh hưởng tới HarvFlow)

1. **Worker chạy trong tiến trình web.** Spec yêu cầu tách xử lý khỏi UI/API.
   Render video dài sẽ chiếm CPU/GPU và RAM; nếu worker chết thì web chết theo.
2. **Khi khởi động lại, job đang chạy bị đánh dấu `cancelled`** (`tf_db.py:187`), không phải `interrupted`, và không ghi máy nào đang chạy job đó.
   Sai so với yêu cầu "không báo sai trạng thái job".
3. **Biến đường dẫn đọc từ môi trường lúc import module** (`DB_PATH`, `MEDIA_DIR`…).
   Vì vậy test phải xoá module khỏi `sys.modules` để nạp lại — dễ vỡ.
4. **Render bằng Pillow từng khung** chỉ hợp với ảnh tĩnh.
   Cắt clip từ video 1080p/4K cần FFmpeg tự xử lý (crop/scale/subtitles) và nên dùng encoder phần cứng (NVENC / VideoToolbox).
5. **`index.html` một khối**, chuỗi tiếng Việt và tên "TrendFlow" viết cứng (18 file có chữ TrendFlow), chưa có cơ chế đổi brand hay i18n.
6. **SQLite vừa là dữ liệu vừa là hàng đợi.** Không được đặt trên Google Drive (Drive không phải hệ file có khoá).
7. **PowerShell/CMD chưa có CI.** Mỗi thay đổi script Windows chỉ được kiểm khi anh chạy thật.

---

## 2. Đánh giá tái sử dụng

### 2.1 Giao diện — giữ lại

| Thành phần | Quyết định |
|---|---|
| CSS tokens (`--bg --panel --panel2 --line --muted --text --accent #ffd640 --ok --bad --warn`), font hệ thống, dark theme | **Giữ nguyên**, tách ra `static/app.css` |
| Bố cục `aside` sidebar 210px + `main`, `nav` có badge đếm, brand chữ + chữ nhấn vàng (`Trend<i>Flow</i>` → `Harv<i>Flow</i>`) | **Giữ**, đổi mục menu |
| `.card .btn .btn.pri .badge .item .list .grid2 .notice .gates/.gate .empty .opt` | **Giữ** |
| Hộp tác vụ `jobBox` (tên tác vụ, %, thanh tiến trình, nút ■ Dừng, chi tiết lỗi) | **Giữ**, thêm trạng thái *Bị gián đoạn* và *Thử lại* |
| `toast()`, `api()`, `poll()`, router `go()/route()`, cảnh báo "có thay đổi chưa lưu" | **Giữ** |
| Danh sách kéo-thả đổi thứ tự (Pending) | **Giữ** cho hàng đợi xuất clip |
| Trang Cài đặt (thẻ trạng thái máy ✓/!, nút kiểm tra) | **Giữ**, đổi nội dung (GPU, Whisper, Drive) |
| Studio (thẻ đoạn, lưới thumbnail, điểm focus bấm trên ảnh) | **Dùng lại ý tưởng** cho chỉnh khung crop thủ công |

Menu mới: **Dự án · Clip đề xuất · Xuất video · Cài đặt**.
"Clip đề xuất" và "Xuất video" nằm trong từng dự án; ở cấp app thì chỉ cần **Dự án**, **Hàng đợi xuất** và **Cài đặt**.

### 2.2 Backend — giữ / sửa / bỏ

| Module | Quyết định | Ghi chú |
|---|---|---|
| `trendflow_cli.py` | **Giữ → `harvflow_cli.py`** | Thêm `setup`, `doctor`, `worker`; launcher cũng khởi động worker |
| `tf_db.py` (migrate an toàn, backup) | **Giữ khung, đổi schema** | Chỉ dùng cho DB **local** (index + jobs) |
| `tf_jobs.py` (queue, cancel, progress, checkpoint) | **Sửa nhiều** | Tách thành tiến trình worker; thêm `device_id`, `interrupted`, `retry`, khoá chống trùng |
| `tf_paths.py` | **Viết lại** | Thư mục local theo chuẩn OS + đường dẫn workspace Drive |
| `tf_llm.py` | **Giữ** | Dùng cho chọn clip và dịch |
| `tf_verify.py` (client Gemini, xử lý 429/quota) | **Giữ phần client** | Làm provider LLM tuỳ chọn (dịch chất lượng cao) |
| `tf_qa.py` | **Giữ một phần** | ffprobe, đo loudness, contact sheet, so audio; thêm kiểm tra A/V sync, phụ đề, kích thước |
| `tf_text.py` | **Giữ một phần** | Chuẩn hoá tiếng Việt, tách câu |
| `tf_render.py` | **Giữ phần chữ** | Font, ngắt dòng, kiểu karaoke → làm preset phụ đề; phần dựng khung thay bằng FFmpeg filter |
| `scripts/setup_windows.ps1`, `migrate_data.py` | **Giữ khung** | Viết thêm `setup_mac.sh` |
| `tf_news, tf_article, tf_media, tf_imagesearch, tf_script, tf_tts, tf_studio, diagnose_tts, test_tts_pipeline, import_data, legacy/` | **Bỏ khỏi HarvFlow** | Vẫn còn nguyên trong repo TrendFlow |
| Test: `test_cli.py` | **Giữ** (đổi tên lệnh) | Các test còn lại thuộc tính năng bị bỏ |

Ước lượng: khoảng 30–35% code hiện tại dùng lại được (UI, hạ tầng, CLI); khoảng 65–70% là tính năng tin tức bị bỏ.

---

## 3. Quyết định repo & Git

| Phương án | Ưu | Nhược |
|---|---|---|
| **A. Repo riêng `caodat1606/harvflow-clips` (khuyến nghị)**, khởi tạo từ lịch sử TrendFlow | TrendFlow không bị ảnh hưởng (`trendflow update` vẫn theo `main` cũ); phụ thuộc khác nhau (Whisper, OpenCV) không làm nặng TrendFlow; sau này bán thì repo sạch | Anh phải tạo thêm 1 repo + cấp quyền cho Claude |
| B. Nhánh dài hạn `harvflow` trong repo `trendflow` | Không cần repo mới | `main` của TrendFlow và HarvFlow lẫn lộn; dễ lỡ tay merge xoá tính năng tin tức; repo mang tên cũ |

Với phương án A:

1. T đẩy lịch sử TrendFlow sang repo mới (giữ lịch sử commit).
2. Tạo `main` cho HarvFlow.
3. Mọi phase làm trên nhánh `phase-N/...` rồi mở PR.

Code TrendFlow, dữ liệu `~/TrendFlowData` và lệnh `trendflow` **không bị đụng tới**.

---

## 4. Kiến trúc đề xuất cho HarvFlow Clips

### 4.1 Tổng quan

```
           Trình duyệt (UI giữ phong cách TrendFlow)
                         │ HTTP localhost
        ┌────────────────▼────────────────┐
        │  harvflow server (FastAPI)      │  ← chỉ API + đọc/ghi dự án, không xử lý nặng
        └───────┬───────────────┬─────────┘
                │ SQLite local  │ Storage API (trừu tượng)
        ┌───────▼──────┐  ┌─────▼──────────────────────────┐
        │ harvflow     │  │ Workspace = thư mục Google     │
        │ worker       │──▶ Drive (manifest JSON, video,   │
        │ (tiến trình  │  │ transcript, clip, phụ đề, MP4) │
        │ riêng)       │  └────────────────────────────────┘
        └──┬─────┬─────┘
   Whisper │     │ FFmpeg (NVENC / VideoToolbox / x264) · OpenCV (khuôn mặt) · Ollama/Gemini
```

- **Server** và **worker** là 2 tiến trình. `harvflow` bật cả hai; tắt UI không làm hỏng job đang chạy.
  Nếu worker chết, server vẫn sống và báo đúng trạng thái.
- **SQLite chỉ ở local:** chỉ mục dự án (cache để tìm/sắp xếp nhanh) và bảng jobs của *máy này*.
  Nguồn sự thật của dự án là **manifest trên Drive**.
- **Storage interface** (`storage/base.py`), dự kiến các hàm sau:

  | Hàm | Việc |
  |---|---|
  | `read_manifest`, `write_manifest(expected_revision)` | Đọc/ghi manifest, kiểm tra revision |
  | `put_file`, `publish_export` | Ghi file, đẩy file xuất đã kiểm tra |
  | `availability(path)` | Có trên máy / chỉ trên cloud / thiếu |
  | `acquire_lease`, `release_lease` | Giữ quyền sửa dự án cho một máy |

  Bản MVP là `LocalFolderStorage` (thư mục Drive for desktop). Sau này thay bằng S3/R2 + Postgres mà không sửa logic nghiệp vụ.

### 4.2 Workspace trên Google Drive

```
<Google Drive>/HarvFlow/                     ← anh chọn 1 lần trong Cài đặt
  workspace.json                             (schemaVersion, workspaceId)
  projects/
    podcast-tap-12--7f3a/                    (slug + id ngắn, đổi tên dự án không đổi thư mục)
      project.json                           ← MANIFEST (xem 4.3)
      project.lease.json                     ← máy nào đang mở để sửa (khoá mềm)
      (video nguồn KHÔNG nằm ở đây — xem sources/ bên dưới)
      analysis/transcript.json               (Whisper: segments + words, kèm hash nguồn + model)
      analysis/faces.json                    (đường đi khuôn mặt theo thời gian — dùng lại trên máy kia)
      clips/candidates.json                  (AI đề xuất, giữ lịch sử từng lần chạy)
      subtitles/<clipId>.vi.json | .en.json  (phụ đề đã sửa)
      exports/<clipId>__vi__20261101-1530.mp4  + .report.json (kết quả QA)
      .history/project.r0042.json            (bản sao manifest cũ, giữ 20 bản gần nhất)
  sources/                                   ← video nguồn dùng chung, mỗi video lưu 1 lần (định danh sha256)
    sha256-9f2c…/original.mp4 + source.json
    index.json                               (YouTube video ID → asset, để phát hiện video đã nhập)
```
Video nguồn chỉ được giữ trên Drive khi anh chọn **"Giữ trên Drive"**. Nếu chọn **"Chỉ giữ metadata + thành phẩm"** thì video chỉ nằm trong cache local; máy khác cần nhập lại khi muốn render lại (xem `YOUTUBE_IMPORT.md` §4).

Ở local (không đồng bộ):
- Windows: `%LOCALAPPDATA%\HarvFlow\`
- macOS: `~/Library/Application Support/HarvFlow/`

Gồm:
- `config.json`: đường dẫn workspace, `deviceId`, tên máy;
- `.env`: key bí mật;
- `harvflow.db`: index + jobs;
- `cache/`: audio WAV 16 kHz, proxy video nhẹ để xem trước, thumbnail;
- `models/`: trọng số Whisper;
- `render/`: file tạm;
- `logs/`.

### 4.3 Manifest & toàn vẹn dữ liệu

`project.json` (rút gọn):

```json
{
  "schemaVersion": 1, "projectId": "7f3a…", "revision": 42,
  "updatedAt": "2026-11-01T15:30:12Z", "updatedBy": {"deviceId": "win-…", "deviceName": "HARVEY-PC"},
  "title": "Podcast tập 12",
  "source": {"kind": "local|youtube", "youtube": {"videoId": "…", "canonicalUrl": "…", "…": "…"},
             "rights": {"basis": "own_channel|licensed|…", "verified": false},
             "media": {"retention": "workspace|metadata_only", "assetId": "sha256:…", "workspacePath": "sources/sha256-…/original.mp4",
                       "probe": {"duration": 2712.4, "w": 1920, "h": 1080, "fps": 29.97, "rotation": 0, "vcodec": "h264", "acodec": "aac"}}},
  "transcript": {"path": "analysis/transcript.json", "language": "vi", "model": "large-v3-turbo", "sourceHash": "…", "edited": true},
  "clips": [{"id": "c1", "status": "accepted", "start": 312.40, "end": 371.85, "title": "…",
             "crop": {"mode": "auto", "manualKeyframes": []}, "subtitle": {"mode": "bilingual", "style": "bold-yellow"}}],
  "exports": [{"clipId": "c1", "file": "exports/…mp4", "mode": "vi", "validated": true, "at": "…"}]
}
```

Quy tắc ghi:

1. **Ghi nguyên tử:** ghi `project.json.tmp-<device>` → `fsync` → `os.replace` thành `project.json`.
   Trước khi thay, chép bản cũ vào `.history/`.
2. **Kiểm tra revision:** mỗi lần lưu, đọc lại `revision` trên đĩa.
   Nếu khác bản đang mở thì **dừng lưu, báo xung đột**, cho anh chọn: giữ bản trên đĩa / ghi đè có xác nhận / lưu thành bản sao.
   Không bao giờ ghi đè im lặng.
3. **Phát hiện bản xung đột của Drive:** các file kiểu `project (1).json` hay `project.json (Conflict…)` → cảnh báo.
4. **Phục hồi:** nếu `project.json` hỏng hoặc dở dang (JSON lỗi) thì tự lấy bản `.history` mới nhất hợp lệ và báo rõ.
5. **Migration schema:** `schemaVersion` + hàm nâng cấp từng bước; luôn sao lưu trước khi nâng cấp.
6. **Đường dẫn tương đối** trong manifest, vì ổ `G:\My Drive\…` trên Windows và `~/Library/CloudStorage/GoogleDrive-…/My Drive/…` trên Mac khác nhau.

### 4.4 Một máy sửa tại một thời điểm (MVP)

- Khi mở dự án để sửa, app ghi `project.lease.json` gồm `deviceId`, `deviceName`, `openedAt` và `heartbeatAt` (cập nhật mỗi 60 giây).
- Máy kia mở dự án mà lease còn mới thì chỉ được **xem**, kèm thông báo: "Đang mở trên HARVEY-MAC lúc 15:30".
  Có nút "Tôi chắc máy kia đã đóng — lấy quyền sửa", hỏi xác nhận trước.
- Khi đóng dự án đúng cách, lease bị xoá.
  Lease bị bỏ lại (máy tắt đột ngột) thì sau 10 phút không có heartbeat app sẽ coi là cũ, nhưng **vẫn hỏi xác nhận**,
  vì Drive có thể chưa kịp đồng bộ file heartbeat.
- **Không tin Drive đã đồng bộ chỉ vì đã lưu xong ở local.**
  - Drive for desktop không có API công khai báo trạng thái từng file, nên app ghi "Đã lưu trên máy này lúc …".
  - Trước khi chuyển máy, nút **"Chuẩn bị chuyển máy"** hiện checklist:
    1. Đóng dự án (nhả lease).
    2. Kiểm tra biểu tượng Drive trên thanh tác vụ hoặc menu bar không còn "Đang đồng bộ".
    3. Đợi file lớn tải xong.
  - Máy kia mở ra so `revision` và `updatedAt` với lần cuối nó thấy. Nếu thấy bản *cũ hơn* thì báo
    "Có thể Drive chưa đồng bộ xong — đợi thêm".

### 4.5 Phát hiện file chỉ có trên cloud

Drive for desktop mặc định ở chế độ **Stream**: file hiện ra nhưng chưa tải về.
- **Windows:** đọc thuộc tính file. Nếu có `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` (0x400000), `OFFLINE` hoặc `RECALL_ON_OPEN`, file đang **chỉ trên cloud**.
- **macOS (File Provider):** file chưa tải có cờ `SF_DATALESS` trong `st_flags`.
- Cả hai: so kích thước với manifest và đọc thử header bằng ffprobe.

Nếu video nguồn chưa có trên máy, app chặn xử lý và hiện nút **"Tải về máy"**.
Nút này đọc tuần tự file để Drive tải về, có % và huỷ được.
Khuyến nghị đặt riêng thư mục `HarvFlow` là **"Available offline / Có sẵn ngoại tuyến"** trên cả 2 máy.
Cả hai cờ trên là hành vi của Windows/macOS, **sẽ được kiểm chứng trên máy thật của anh** ở Phase 1, vì máy của t không có Google Drive.

### 4.6 Job & thiết bị

- **Trạng thái:** `queued → running → completed | failed | cancelled | interrupted`.
- **Chủ sở hữu:** mỗi job ghi `deviceId` + `pid` của worker + `heartbeatAt`.
  Worker khởi động lại mà thấy job `running` của chính máy mình nhưng pid đã chết thì chuyển sang **`interrupted`**, không phải `cancelled`, kèm nút **Thử lại**.
- **Chống trùng:** khoá theo khoá logic, ví dụ `(projectId, "transcribe", sourceHash, model)` hay `(clipId, renderHash)`.
  Đã có kết quả hợp lệ thì dùng lại, không chạy lại.
- **Không di chuyển giữa máy:** jobs nằm trong DB local.
  Manifest chỉ ghi kết quả đã xong (transcript, export đã kiểm tra).
  Máy kia thấy "Clip c1 đang render trên HARVEY-PC (chưa xong)" qua lease, chứ không thấy một job giả "đang chạy".
- **Checkpoint:**
  - Transcribe theo từng khúc audio khoảng 10 phút, lưu từng phần, chạy lại thì làm tiếp.
  - Render theo từng clip.

### 4.7 Lõi xử lý theo từng module

| Module | Công nghệ đề xuất | Windows (RTX 4070) | macOS (M3 Pro) | Dự phòng |
|---|---|---|---|---|
| B. Nhập video từ link YouTube | oEmbed + YouTube Data API (metadata), OAuth `captions.download` (phụ đề kênh mình), theo dõi Downloads nhận bản tải chính thức từ YouTube Studio — **không tải trực tiếp từ YouTube** | — | — | "Chọn file gốc" — chi tiết: `YOUTUBE_IMPORT.md` |
| B. Nhập video | `ffprobe` JSON: thời lượng, codec, độ phân giải, fps, xoay (`side_data`/`rotate`), có audio | — | — | Từ chối file không đọc được, file 0 byte, file chỉ trên cloud |
| C. Chép lời | **faster-whisper** (CTranslate2) trên Windows; **mlx-whisper** (Apple MLX, chạy GPU Apple) trên Mac; cùng giao diện `Transcriber` | CUDA float16, `large-v3-turbo` mặc định, `large-v3` khi cần chính xác | MLX `large-v3-turbo` | CPU int8 (`faster-whisper`, model `small`/`medium`) |
| C. Tiền xử lý audio | FFmpeg tách mono 16 kHz, `loudnorm`; VAD (Silero, có sẵn trong faster-whisper) để bỏ đoạn im lặng dài, giảm "bịa chữ" | | | |
| D. Chọn clip | LLM đọc transcript theo cửa sổ có chồng lấn → ứng viên → chấm điểm → loại trùng ý (so tương đồng nội dung) → cắt ở ranh giới câu | Ollama `qwen3:8b` local | Ollama `qwen3:8b` | Gemini (key anh đã có, tuỳ chọn) |
| F. Dịch phụ đề | LLM dịch **theo từng câu có mã**, giữ thời gian gốc; anh sửa tay được | Ollama | Ollama | Gemini cho bản dịch tự nhiên hơn |
| F. Phụ đề | Mô hình phụ đề JSON (nguồn sự thật) → xuất **ASS** → FFmpeg `subtitles` (libass) với **font đi kèm repo** (ví dụ Be Vietnam Pro, giấy phép OFL) để 2 máy hiển thị giống hệt | | | Render chữ bằng Pillow nếu thiếu libass |
| G. Cắt khung 9:16 | **OpenCV YuNet** (`cv2.FaceDetectorYN`, model khoảng 300 KB) dò mặt khoảng 5 khung/giây → bám vết → làm mượt (vùng chết + giới hạn tốc độ) → đường crop. 2 người: chế độ "chia đôi màn hình" hoặc theo người nói (chọn tay) | CPU, đủ nhanh | CPU | Crop giữa; chỉnh tay theo mốc thời gian |
| H. Render | FFmpeg: `trim → crop (theo đường crop) → scale 1080×1920 → subtitles → encode` | `h264_nvenc` | `h264_videotoolbox` | `libx264` |
| H. Kiểm tra đầu ra | ffprobe (codec, 1080×1920, thời lượng = clip ± 0,1 s), audio không câm, so tương quan audio với nguồn, contact sheet | | | |

Ghi chú:
- **CTranslate2/faster-whisper không chạy GPU Apple**, nên Mac cần backend MLX riêng.
  Cả hai backend phải xuất **cùng định dạng transcript**, để Mac chép lời xong thì Windows dùng lại được, và ngược lại.
- **Tiếng Việt:** sẽ đo thử `large-v3-turbo` so với `large-v3` (và **PhoWhisper** của VinAI nếu cần) trên audio thật của anh ở Phase 2,
  chọn bản có lỗi ít nhất. Không khẳng định trước.
- **Điểm "editorial score"** được hiển thị là *đánh giá biên tập của AI*, không phải xác suất viral.

### 4.8 Lệnh & cài đặt

| Lệnh | Việc |
|---|---|
| `harvflow` | Bật server + worker (nếu chưa chạy), mở trình duyệt. **Không cài lại gì.** |
| `harvflow stop` / `status` / `logs` | Tắt / trạng thái / log |
| `harvflow update` | Như `trendflow update` (backup, smoke test, `--ff-only`, rollback) |
| `harvflow doctor` | Kiểm tra Python, FFmpeg/FFprobe + libass + encoder phần cứng, GPU backend (CUDA/MLX), model Whisper, Ollama, đường dẫn workspace, Drive |
| `harvflow setup` | Tải model, cấu hình workspace lần đầu |
| `scripts/setup_windows.ps1`, `scripts/setup_mac.sh` | Cài lần đầu cho mỗi máy (venv, phụ thuộc theo nền tảng, PATH) |

Phụ thuộc theo nền tảng (`requirements-win.txt` / `requirements-mac.txt`):
- chung: `faster-whisper`, `opencv-python-headless`;
- Mac thêm: `mlx-whisper`.

**Python 3.12 cố định** trên cả hai máy, vì các gói AI/GPU hỗ trợ ổn định nhất ở bản này.

### 4.9 Cấu trúc repo đề xuất

```
harvflow-clips/
  harvflow/                 # gói Python
    app.py                  # FastAPI: chỉ route + gọi service
    brand.py                # tên/brand/tagline/màu — 1 chỗ duy nhất
    config.py paths.py      # cấu hình, thư mục local theo OS
    storage/ base.py local_folder.py manifest.py lease.py availability.py
    projects/ service.py schema.py migrations.py
    media/ probe.py audio.py
    transcribe/ base.py faster_whisper_backend.py mlx_backend.py
    clips/ discovery.py dedupe.py
    subtitles/ model.py translate.py ass.py styles.py
    reframe/ faces.py track.py
    render/ ffmpeg.py encoders.py validate.py
    jobs/ db.py queue.py worker.py
    llm/ ollama.py gemini.py
  web/ index.html static/app.css static/js/*.js  static/fonts/
  bin/ harvflow harvflow.cmd
  scripts/ setup_windows.ps1 setup_mac.sh
  tests/ unit/ integration/ fixtures/(video mẫu ngắn tự tạo bằng FFmpeg + audio có giấy phép)
  docs/
```

---

## 5. Rủi ro & cách giảm

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Drive chưa đồng bộ xong mà đã mở trên máy kia → mất sửa đổi | Cao | Revision + lease + checklist chuyển máy; không ghi đè im lặng; `.history` |
| Video 45–60 phút (2–6 GB) ở chế độ Stream → máy kia phải tải lại | Trung bình | Đặt thư mục HarvFlow "Available offline"; app báo % tải |
| Whisper tiếng Việt sai tên riêng / "bịa" khi im lặng | Cao | VAD, `condition_on_previous_text=False`, ngưỡng no-speech; transcript sửa được; đánh giá trên audio thật |
| LLM 8B chọn clip nhạt / dịch cứng | Trung bình | Prompt theo tiêu chí biên tập, loại trùng ý; tuỳ chọn Gemini; anh luôn duyệt tay |
| Crop giật hoặc mất mặt khi 2 người | Trung bình | Làm mượt + vùng chết; layout chia đôi; chỉnh tay; dự phòng crop giữa |
| Font/libass khác nhau giữa 2 máy | Thấp | Font kèm repo + `fontsdir`; `doctor` kiểm tra libass |
| Mac 18 GB chạy Whisper large + Ollama cùng lúc → thiếu RAM | Trung bình | Worker chạy tuần tự từng job nặng; giải phóng model sau khi dùng |
| **T không có Windows, Mac, GPU hay Google Drive** để test | Cao | Test tự động trên Linux (CPU, model nhỏ, video mẫu); mỗi phase có **checklist kiểm thử thật** cho anh trên 2 máy; không tuyên bố "chạy được" khi chưa có bằng chứng |
| Bản quyền video nguồn khi đăng kênh | Cao (pháp lý) | Phase 7 chỉ dùng nội dung anh có quyền; app ghi nguồn trong manifest |

---

## 6. Kế hoạch triển khai & tiêu chí nghiệm thu

| Phase | Nội dung | Nghiệm thu (bằng chứng cụ thể) |
|---|---|---|
| **1A. Nền tảng** | Repo mới; brand cấu hình được; `harvflow` + setup Win/Mac + `doctor`; worker tách tiến trình; Storage + manifest (gồm trường `source.kind / rights / media.retention / assetId` cho YouTube) + lease + revision + `.history`; thư mục `sources/` dùng chung; **khai báo** interface `SourceResolver` / `MediaProvider` (chưa gọi mạng) | Test tự động: ghi nguyên tử, xung đột revision, phục hồi file hỏng, lease, migration schema. `harvflow doctor` chạy được trên 2 máy |
| **1B. Thư viện dự án** | UI giữ phong cách TrendFlow, brand HarvFlow; tạo/mở/đổi tên/nhân bản/lưu trữ/tìm/sắp xếp; trạng thái dự án; checklist chuyển máy | **Anh thử thật:** tạo dự án trên Mac → Drive đồng bộ → mở trên Windows thấy đúng; mở cùng lúc trên 2 máy → máy sau chỉ xem được; sửa lệch revision → báo xung đột |
| **2. Nhập & chép lời** | Import Studio 2 tab: **Dán link YouTube** (phân tích URL, metadata/thumbnail, xác nhận quyền, phụ đề chính thức nếu là kênh mình, theo dõi bản tải từ YouTube Studio, phát hiện video đã nhập, giữ trên Drive hoặc chỉ metadata) và **Tải video từ máy**; ffprobe, từ chối file lỗi / chỉ-trên-cloud / tải dở; Whisper 2 backend; cache; sửa transcript | Toàn bộ test ở `YOUTUBE_IMPORT.md` §6; video thật 10 phút: chép lời xong, mở lại không chạy lại; chép lời trên Mac → Windows dùng lại; đo tốc độ + lỗi tiếng Việt ghi vào báo cáo |
| **3. Chọn clip** | 5–10 ứng viên 30–90 giây đủ trường; loại trùng; xem thử, nhận/bỏ, chỉnh biên | Trên 2–3 video thật: anh chấm tỉ lệ clip dùng được; không có 2 clip cùng ý |
| **4. Phụ đề** | Việt / Anh / song ngữ; sửa, style, preview; thời gian đúng khi đổi biên clip | Sửa phụ đề trên máy A → máy B thấy đúng; preview khớp bản xuất |
| **5. Cắt khung & xuất** | Crop theo mặt, chỉnh tay, render NVENC/VideoToolbox/x264, xuất hàng loạt, huỷ, thử lại, kiểm tra đầu ra, file tạm local rồi mới đưa lên Drive | MP4 1080×1920 H.264/AAC, âm thanh gốc, phụ đề đúng, khung ổn định; file dở không bao giờ xuất hiện trong `exports/` |
| **6. QA** | Ma trận test spec mục 10 (10/30/60 phút, 1–2 người, Việt/Anh, fps/độ phân giải, ngắt giữa chừng, thiếu file, xung đột) | Báo cáo đầy đủ, ghi cả lỗi |
| **7. Kiểm chứng kênh** | 20–30 clip, đo thời gian và tỉ lệ duyệt | Số liệu thật |

Mỗi phase kết thúc bằng: PR + báo cáo theo đúng mẫu mục 12 của spec + **checklist anh tự thử trên 2 máy**. Không tự sang phase sau.

---

## 7. Cần anh quyết định trước khi bắt đầu Phase 1

1. **Repo riêng `harvflow-clips`** (khuyến nghị) hay nhánh trong repo `trendflow`?
2. **Video nguồn mặc định:** "Giữ trên Drive" (2 máy đều render được, tốn dung lượng — anh có 5 TB) hay "Chỉ giữ metadata + thành phẩm"? Đổi được cho từng dự án.
   Khuyến nghị: **Giữ trên Drive**, lưu 1 lần trong `sources/` dùng chung.
3. **Dịch và chọn clip:** chỉ AI trên máy (Ollama — miễn phí, chậm hơn, dịch kém tự nhiên hơn) hay cho phép **Gemini** khi có key (nhanh và tốt hơn, có thể tốn phí khi vượt mức miễn phí)?
   Khuyến nghị: Ollama mặc định, Gemini tuỳ chọn.
4. Đồng ý **bỏ hẳn** tính năng tin tức và giọng đọc máy khỏi HarvFlow (vẫn giữ trong TrendFlow)?
5. **YouTube Import:** đồng ý giới hạn ở `YOUTUBE_IMPORT.md` §1 — app **không tự tải video từ YouTube**; video kênh của anh lấy qua bản tải chính thức từ YouTube Studio, video kênh khác cần file gốc?

## 8. Giới hạn của bản audit này

- Đã đọc toàn bộ code thật trong repo; số dòng, phụ thuộc và cấu trúc ở trên lấy từ repo, không phỏng đoán.
- Môi trường của t là Linux 4 nhân, **không GPU**, không có Windows/macOS/Google Drive.
  Những nhận định về CUDA, MLX, NVENC, VideoToolbox và cờ file của Drive dựa trên tài liệu, chưa đo trên máy anh.
  Phase 1–2 có bước kiểm chứng thật trên 2 máy.
- Chưa benchmark Whisper tiếng Việt; số liệu tốc độ và độ chính xác sẽ có ở Phase 2.
