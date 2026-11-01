# HarvFlow Clips — Thiết kế YouTube URL Import

> Phạm vi: **thiết kế interface + data model** (Phase 0 / 1A). Triển khai đầy đủ thuộc **Phase 2 — Video Import & Transcription**.
> Trạng thái: ĐỀ XUẤT — chờ duyệt.

## 1. Sự thật về quyền truy cập (quyết định toàn bộ thiết kế)

| Muốn lấy | Cách hợp lệ | Giới hạn |
|---|---|---|
| Tên video, kênh, thumbnail | **oEmbed** `https://www.youtube.com/oembed?url=…&format=json` (không cần key) | Chỉ video công khai/không công khai (unlisted) cho nhúng; private hoặc bị xoá → lỗi |
| Thời lượng, trạng thái riêng tư, có phụ đề hay không, giấy phép (Standard / Creative Commons) | **YouTube Data API v3** `videos.list` (part `snippet, contentDetails, status`) với API key (miễn phí, có hạn mức) | Chỉ báo *có* phụ đề hay không, không trả nội dung phụ đề |
| Nội dung phụ đề | Data API `captions.download` qua **OAuth** | Chỉ với video mà tài khoản đăng nhập **có quyền chỉnh sửa** (kênh của chính anh) |
| **File video** | Data API **không** có chức năng tải video. **Điều khoản YouTube cấm tải nội dung bằng cách khác ngoài nút/tính năng YouTube cung cấp.** Chủ kênh tải được video của mình trong **YouTube Studio** (hoặc Google Takeout) | Không có cách hợp lệ để app tự tải video của **kênh khác** |

Hệ quả:
- HarvFlow **không** dùng công cụ tải không chính thức (ví dụ yt-dlp hay các trang "download YouTube").
  HarvFlow cũng **không** vượt đăng nhập, video riêng tư, giới hạn độ tuổi/khu vực hay DRM.
- Trải nghiệm "dán link là xong" đầy đủ nhất chỉ có với **video của kênh anh**.
  Với video kênh khác, app lấy được metadata, còn file video phải là **file gốc** anh có quyền dùng.
  App nói rõ điều này ngay khi dán link, không để anh chờ rồi mới báo lỗi.
- Kiến trúc có `MediaProvider` cắm thêm được.
  Nếu sau này có nguồn được cấp phép (đối tác nội dung, API chính thức mới), chỉ cần thêm provider, không sửa pipeline.

## 2. Trải nghiệm người dùng (Import Studio — giữ phong cách UI TrendFlow)

Hai tab trong màn hình **Nhập video**: **Dán link YouTube** · **Tải video từ máy**.

### 2.1 Tab "Dán link YouTube"

```
[ https://youtu.be/…                         ] [Kiểm tra]
┌────────────────────────────────────────────────────────┐
│ [thumbnail]  Tên video · Kênh · 45:12 · Công khai       │
│              Phụ đề: có (vi, en) · Giấy phép: Standard  │
│  Đây là video của:  (•) Kênh của tôi (đã xác minh ✓)    │
│                     ( ) Kênh khác — tôi có quyền dùng   │
│                         (giấy phép / thoả thuận / CC)   │
│  ☐ Tôi xác nhận có quyền cắt và đăng lại nội dung này  │
│  Video nguồn: (•) Giữ trên Drive  ( ) Chỉ giữ metadata  │
│                                    + thành phẩm         │
│  [Nhập]                                                 │
└────────────────────────────────────────────────────────┘
```

Luồng xử lý sau khi bấm **Nhập**:

1. **Kiểm tra URL** ngay trên máy (không gọi mạng); lấy video ID.
2. **Kiểm tra trùng:** video ID đã có trong workspace thì báo "Video này đã được nhập (dự án X, ngày …)", với 3 lựa chọn: mở dự án cũ / tạo dự án mới dùng lại media / huỷ.
3. **Lấy metadata:** oEmbed, và Data API nếu có key; hiện thumbnail, thời lượng, phụ đề.
4. **Xác nhận quyền** (bắt buộc tick).
   - Chọn "Kênh của tôi": nếu đã kết nối tài khoản YouTube thì app xác minh `channelId` của video trùng với kênh của anh (✓ đã xác minh).
   - Chưa kết nối thì ghi "tự khai".
5. **Phụ đề:**
   - Kênh của anh, đã xác minh: tải phụ đề chính thức (`captions.download`) làm **bản tham khảo** cho Whisper và bản dịch.
   - Video khác: ghi "không lấy được phụ đề hợp lệ"; transcript sẽ do Whisper tạo từ audio.
6. **Media**, theo thứ tự ưu tiên:
   1. **Đã có trên máy/Drive:** khớp video ID trong `sources/`, hoặc khớp thời lượng ±1 s với file anh chỉ định → dùng lại, không tải lại.
   2. **Kênh của anh:**
      - Nút **"Lấy bản tải từ YouTube Studio"** mở đúng trang video trong Studio. Anh bấm Tải xuống (tính năng chính thức của YouTube).
      - HarvFlow **theo dõi thư mục Downloads**, tự nhận file mới có thời lượng khớp video (ffprobe) và tự nhập.
      - Không phải "Tải lên" thủ công.
      - Lưu ý: bản tải từ Studio có thể có chất lượng thấp hơn file gốc lúc anh dựng. Có file gốc thì nên dùng file gốc.
   3. **Video kênh khác:** hiện lý do rõ ràng ("YouTube không cho phép ứng dụng tải video của kênh khác") + nút **"Chọn file gốc"** (chuyển sang tab Tải video từ máy, giữ nguyên metadata đã lấy).
7. **Nhập vào pipeline:**
   - File được chép vào cache local, đo lại bằng ffprobe.
   - Nếu chọn "Giữ trên Drive" thì publish vào workspace.
   - Sau đó đi vào **cùng pipeline** với file local: transcribe → chọn clip → phụ đề → cắt khung → xuất.

### 2.2 Thông báo lỗi (luôn kèm phương án "Chọn file gốc")

| Mã | Khi nào | Thông báo |
|---|---|---|
| `invalid_url` | Không phải link YouTube / không có ID 11 ký tự | "Link không phải video YouTube. Ví dụ hợp lệ: youtube.com/watch?v=…, youtu.be/…, youtube.com/shorts/…" |
| `playlist_only` | Link playlist không có `v=` | "Đây là playlist — dán link từng video" |
| `not_found` | oEmbed/API báo không tồn tại | "Video không tồn tại hoặc đã bị xoá" |
| `private_or_restricted` | Private, giới hạn tuổi/khu vực, không cho nhúng | "Video riêng tư hoặc bị giới hạn — HarvFlow không truy cập. Nếu là video của anh, hãy chọn file gốc" |
| `no_captions` | Không có phụ đề / không có quyền tải | Chỉ là thông tin, không chặn: "Sẽ tạo transcript bằng Whisper" |
| `media_unavailable` | Không có đường hợp lệ để lấy file | "Cần file video gốc để xử lý" + nút |
| `rights_not_confirmed` | Chưa tick xác nhận quyền | Nút Nhập bị khoá |
| `network_error` | Mất mạng khi lấy metadata hoặc tải phụ đề | "Mất kết nối — thử lại"; phần đã lấy được giữ nguyên, không tạo dự án dở |
| `download_incomplete` | File trong Downloads chưa tải xong (`.crdownload`, `.part`, kích thước đang tăng, ffprobe lỗi) | Đợi; chỉ nhập khi file ổn định và đọc được |

## 3. Data model

### 3.1 Manifest dự án (`project.json`, schemaVersion 1) — trường `source`

```json
"source": {
  "kind": "youtube",                       // "local" | "youtube"
  "youtube": {
    "videoId": "dQw4w9WgXcQ",
    "url": "https://youtu.be/dQw4w9WgXcQ?si=…",          // đúng như anh dán
    "canonicalUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "title": "…", "channelTitle": "…", "channelId": "UC…",
    "thumbnailUrl": "https://i.ytimg.com/vi/…/hqdefault.jpg",
    "durationSec": 2712, "privacy": "public", "license": "youtube|creativeCommon",
    "captions": {"available": true, "languages": ["vi"], "fetched": true, "path": "analysis/captions.youtube.vi.json"},
    "metadataFetchedAt": "2026-11-01T15:30:00Z", "metadataVia": "oembed|data_api"
  },
  "rights": {
    "basis": "own_channel|licensed|permission|creative_commons|other",
    "verified": true,                       // true chỉ khi OAuth xác minh channelId
    "note": "…", "confirmedAt": "…", "confirmedOnDevice": "HARVEY-MAC"
  },
  "media": {
    "retention": "workspace|metadata_only",
    "assetId": "sha256:9f2c…",             // định danh nội dung, dùng để dùng lại media
    "workspacePath": "sources/sha256-9f2c…/original.mp4",   // null nếu metadata_only
    "origin": "youtube_studio_download|original_file|local_upload",
    "probe": {"duration": 2712.4, "w": 1920, "h": 1080, "fps": 29.97, "rotation": 0, "vcodec": "h264", "acodec": "aac"},
    "size": 2147483648
  }
}
```

- Với file local thì `kind = "local"`, không có khối `youtube`; `rights` và `media` dùng chung cấu trúc.
- **Không bao giờ** ghi đường dẫn tuyệt đối hay đường dẫn cache local vào manifest, vì manifest dùng chung cho 2 máy.

### 3.2 Thư viện nguồn dùng chung trong workspace (tránh trùng video 2–6 GB)

```
HarvFlow/sources/sha256-9f2c…/original.mp4
HarvFlow/sources/sha256-9f2c…/source.json     // assetId, size, probe, youtubeVideoId?, các projectId đang dùng
HarvFlow/sources/index.json                   // youtubeVideoId → assetId (tra trùng nhanh)
```

Thiết kế này thay cho đề xuất cũ "chép video vào từng thư mục dự án": nhiều dự án dùng chung một video gốc, không phải chép lại.

### 3.3 Dữ liệu chỉ ở máy local (SQLite `harvflow.db`)

```
media_cache(asset_id PK, local_path, size, sha256, probe_json, last_used_at, pinned)
import_sessions(id PK, kind, video_id, state, error_code, detail, created_at, updated_at, device_id)
watch_downloads(id PK, import_session_id, folder, expected_duration, started_at, matched_path)
```

- `media_cache` dọn theo dung lượng (LRU). Không xoá file đang được job dùng.
- `import_sessions` giữ trạng thái nhập dở, máy tắt giữa chừng vẫn tiếp tục được.

### 3.4 Interface (Python)

```python
class SourceResolver(Protocol):                 # nhận diện link/file → thông tin nguồn
    def parse(self, text: str) -> ParsedSource | ImportError: ...          # thuần, không gọi mạng
    def fetch_metadata(self, src: ParsedSource) -> SourceMetadata: ...      # oEmbed / Data API
    def captions(self, src, auth: YouTubeAuth | None) -> CaptionResult: ... # chỉ đường chính thức

class MediaProvider(Protocol):                  # các cách HỢP LỆ để có file video
    name: str
    def can_provide(self, meta: SourceMetadata, rights: Rights) -> Availability: ...  # yes/no + lý do
    def acquire(self, meta, dest_dir: Path, progress, cancel) -> AcquiredMedia: ...

# Provider có sẵn ở Phase 2:
#   ExistingAssetProvider      – asset đã có trong sources/ hoặc cache local (dùng lại)
#   LocalFileProvider          – file anh chọn (tab Tải video từ máy)
#   StudioDownloadWatcher      – theo dõi Downloads, nhận bản tải chính thức từ YouTube Studio
# Không có provider nào tải trực tiếp từ YouTube.
```

Bộ phân tích URL (thuần, test được 100% offline) chấp nhận:
- `youtube.com/watch?v=ID` (cả `m.` và `music.`), `youtu.be/ID`;
- `youtube.com/shorts/ID`, `/live/ID`, `/embed/ID`, `/v/ID`, `youtube-nocookie.com/embed/ID`;
- tham số thừa (`t=`, `si=`, `list=`, `feature=`), có hoặc không có `https://`/`www.`, khoảng trắng đầu/cuối.

ID hợp lệ: đúng 11 ký tự `[A-Za-z0-9_-]`.

## 4. Lưu trữ & chuyển máy

- **Giữ trên Drive:** máy kia mở dự án là có video (sau khi Drive đồng bộ hoặc bấm "Tải về máy" nếu file chỉ ở cloud). Mọi thao tác dùng được.
- **Chỉ giữ metadata + thành phẩm:** video chỉ nằm trong cache của máy đã nhập.
  - Máy kia vẫn mở dự án, xem/sửa transcript, clip, phụ đề và xem thành phẩm.
  - Nhưng **sửa biên clip, đổi khung crop, render lại** thì cần nhập media lại. App hiện cảnh báo ngay lúc chọn và một banner trong dự án:
    "Video nguồn không có trên máy này — Nhập lại (link YouTube hoặc file gốc)".
  - Khi nhập lại, app kiểm tra `assetId` (sha256), hoặc ít nhất thời lượng + kích thước khung, để chắc là **đúng video cũ** trước khi cho render.
    Như vậy mốc thời gian clip và phụ đề vẫn khớp.
- Không tự động đẩy mọi video nguồn lên Drive. Mặc định là lựa chọn anh đặt trong Cài đặt, đổi được cho từng dự án.

## 5. Cấu hình cần (đều tuỳ chọn)

| Cấu hình | Dùng để | Không có thì |
|---|---|---|
| YouTube Data API key (Google Cloud, miễn phí) | Thời lượng, trạng thái, có phụ đề, giấy phép | Chỉ có tên, kênh, thumbnail (oEmbed); thời lượng đo sau khi có file |
| Kết nối tài khoản YouTube (OAuth, quyền chỉ đọc + phụ đề) | Xác minh "kênh của tôi", tải phụ đề chính thức | "Kênh của tôi" là tự khai; không có phụ đề chính thức |

Key và token lưu ở `.env`/kho bí mật local của từng máy, **không** đưa lên Drive hay Git.
Đăng nhập OAuth làm một lần trên mỗi máy.

## 6. Kế hoạch kiểm thử (Phase 2)

| Trường hợp | Cách test |
|---|---|
| URL hợp lệ, rút gọn (`youtu.be`), Shorts, live, embed, có `t=`/`si=`/`list=` | Unit test bộ phân tích (offline) |
| URL sai định dạng, ID 10/12 ký tự, link kênh, link playlist | Unit test → đúng mã lỗi |
| Video private / không tồn tại / không cho nhúng | Test với HTTP giả lập (401/403/404 của oEmbed và Data API) + **1 lần thử thật** với video private của anh |
| Video không có phụ đề | Giả lập `contentDetails.caption=false` → không chặn, chuyển sang Whisper |
| Video đã nhập | Nhập 2 lần cùng ID → báo trùng, dùng lại asset, không chép thêm |
| Media chưa có sẵn | Kênh khác → `media_unavailable` + nút chọn file; dự án "chỉ metadata" mở trên máy khác → banner nhập lại |
| Mất mạng giữa chừng | Giả lập timeout/ngắt kết nối khi lấy metadata và phụ đề → không tạo dự án dở, thử lại tiếp tục được |
| File Downloads chưa tải xong | `.crdownload` / kích thước đang tăng → chưa nhập; xong mới nhập |
| Chuyển máy Mac ↔ Windows | Checklist thật: nhập trên Mac (giữ trên Drive) → mở trên Windows, render; lặp lại với "chỉ metadata" → Windows phải hiện banner nhập lại và kiểm tra đúng video |

Giới hạn: máy phát triển của t không có tài khoản YouTube của anh, nên OAuth, phụ đề thật và tải từ Studio **chỉ kiểm chứng được trên máy anh**. Mỗi phase sẽ có checklist kèm theo.

## 7. Ảnh hưởng tới roadmap

- **Phase 1A (nền tảng):** chỉ thêm `source.kind`, `rights`, `media.retention`, `assetId` vào schema manifest, thư mục `sources/` dùng chung, và khai báo interface `SourceResolver`/`MediaProvider`.
  Không gọi mạng, không UI YouTube. **Không làm chậm phần nền tảng 2 máy.**
- **Phase 2 (nhập & chép lời):** tab "Dán link YouTube", phân tích URL, oEmbed/Data API, xác nhận quyền, OAuth + phụ đề kênh mình, theo dõi Downloads, dùng lại asset, toàn bộ test ở mục 6.
