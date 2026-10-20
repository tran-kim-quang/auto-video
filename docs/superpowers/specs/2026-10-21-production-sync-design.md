# Auto Video Web — đối chiếu production, lessonOrder đúng và tự import vào backend

Ngày: 2026-10-21 · Repo: `auto-video` · Bổ sung cho `docs/superpowers/specs/2026-10-20-r2-upload-design.md`

Ghi chú đọc: mục **[quyết định của người viết spec]** là lựa chọn mặc định chủ dự án chưa duyệt riêng.

## 1. Mục đích và lý do

Script `pyramid-upload-to-r2/scripts/local-bulk-upload.mjs` suy ra `lessonOrder` từ số "Bài N" trong tên folder. Trên production, `lessons.order` là **vị trí của bài trong khoá** (có chèn "Luyện tập chung", "Bài tập cuối chương"), ví dụ Toán 6 "Bài 19" có `order = 29`. Worker import của backend tìm bài theo `(courseId, order = lessonOrder)` (`pyramid-be/src/jobs/workers/lesson-manifest-import.worker.ts:182-189`) rồi ghi đè tiêu đề/slug/mô tả bài đó, nên manifest gốc sẽ nhắm **nhầm bài** (lần 2026-10-20: order 20 là "Bài 13: Tập hợp các số nguyên"). Lần đó đã phải sửa tay `lessonOrder`, import tay manifest video rồi manifest phụ đề bằng token admin.

Mục tiêu: nút "Upload lên R2" làm trọn quy trình — tra production bằng token admin, chỉ upload bài **đã có trên production** với `lessonOrder` thật, rồi **tự import** manifest video và phụ đề vào backend và đối chiếu kết quả.

## 2. Mục tiêu / ngoài phạm vi / không được xảy ra

**Mục tiêu (chủ dự án duyệt)**
- Ô **"Token admin (production)"** khi bấm "Upload lên R2" và "Kiểm tra upload".
- Tra production (chỉ đọc) theo slug bài; `lessonOrder` lấy từ production.
- Bài **chưa có trên production**: không upload, không vào manifest; video + phụ đề được **chuyển** sang `<thư mục kết quả>/_chua-co-tren-production/<Mã môn>/<Bài…>/…`.
- Sau upload R2: sao lưu → import manifest video → chờ worker → import manifest phụ đề → đối chiếu trước/sau.
- "Kiểm tra upload" báo trước kế hoạch (bài sẽ upload, bài sẽ chuyển sang thư mục chờ, bài sẽ ghi đè video cũ) — không di chuyển file, không import.

**Ngoài phạm vi**
- Tạo bài mới trên production; tự hoàn tác (rollback) khi đối chiếu thấy bất thường; quản lý/đăng nhập admin trong tool (người dùng tự dán token); sửa `pyramid-be` hay script upload.

**Không được xảy ra**
- Import manifest có `lessonOrder` không lấy từ production.
- Token admin bị lưu xuống đĩa (state, log, manifest, export) hoặc trả về trong API của tool, hoặc truyền vào tiến trình `node` upload.
- Import khi tra production thất bại (token sai/hết hạn, mạng lỗi).
- Đổi tên bài/tiết/video trên production ngoài ý muốn (worker ghi đè `period.title` và `lessonVideo.title` bằng `videoTitle`, và `lesson.title/slug` bằng manifest).

## 3. Dữ liệu production cần dùng (đã đọc code `pyramid-be`)

- Base API mặc định `https://api.pyramid.edu.vn/api/v1` (đã xác nhận `/health` → `{"ok":true,"sha":…,"db":true,"redis":true}`). Mọi phản hồi bọc trong `{"data": …}`.
- `GET /admin/lessons?courseId=<id>&limit=200&page=<n>` (Bearer token, role ADMIN) → `data.items[]`: `id, courseId, title, slug, order, description, durationMinutes, periods[] {id, order, title}, videos[] {id, sectionNo, partNo, slug, durationMinutes}`; `data.total`, `data.totalPages`. Không có `videoKey` trong danh sách.
- `POST /admin/bulk-upload/import-manifest` multipart field `manifest` (≤ 10 MB) → `201 {"data":{"jobId":…,"videoCount":n}}`; xử lý **bất đồng bộ** bởi worker, không có API trạng thái.
- `POST /admin/bulk-upload/import-cc-manifest` multipart field `manifest` → `201 {"data":{"inserted","updated","skipped","total"}}` (đồng bộ); `400` kèm `missingTargets` khi thiếu video đích; `linkUrl` phải bằng `${R2_PUBLIC_CDN_BASE_URL}/${r2Key}` của backend.
- Hành vi worker video (để kiểm tra trước): tìm bài theo `(courseId, order)`; cập nhật `title`, `slug`, `order`, `description = null`; upsert period `(lessonId, order = sectionNo)` với `title = videoTitle`; upsert video `(lessonId, sectionNo, partNo)` với `slug, title, videoKey, durationMinutes`; tính lại `lesson.durationMinutes`.

## 4. Thiết kế

### 4.1 Token và cấu hình
- Ô nhập token kiểu password trên tab Hàng đợi, cạnh hai nút upload; gửi trong body `{"dry_run": …, "admin_token": "…"}`. Token chỉ sống trong bộ nhớ của lượt chạy; **không** ghi vào `uploads.json`, log, manifest, export; **không** đặt vào env của tiến trình `node`; log/lỗi luôn qua hàm che token (thay chuỗi token bằng `***`).
- `--backend-api` / biến `PYRAMID_BACKEND_API`, mặc định `https://api.pyramid.edu.vn/api/v1` **[quyết định của người viết spec]**.
- Token **bắt buộc** cho "Upload lên R2". Với "Kiểm tra upload": có token → tra production và báo kế hoạch; không có → chạy như hiện nay kèm cảnh báo `Chưa tra production (không có token admin)` **[quyết định của người viết spec]**.
- `401/403` từ API → dừng trước mọi thao tác ghi: `Token admin không hợp lệ hoặc đã hết hạn`.

### 4.2 Tra và phân loại (trước khi upload R2)
Với mỗi bài đủ điều kiện upload (như hiện nay: job COMPLETED, danh tính hợp lệ, có đủ file), lấy `courseId`, `lessonSlug`, `sectionNo`, `partNo`, `videoSlug`, `videoTitle`, `lessonTitle` từ `parseVideoPath` (helper Node hiện có) và tra danh sách bài của khoá (cache theo `courseId` trong lượt chạy, đọc hết các trang):
- **Khớp bài [quyết định của người viết spec]:** bài tạo/sửa trong admin có slug `${order}-${slugify(title)}` (`pyramid-be/.../admin-catalog-helper.service.ts:692-695`), khác slug `parseVideoPath` sinh ra (`${N}-bai-${N}-…`). Vì vậy:
  1. khớp `slug == lessonSlug`; nếu không có,
  2. khớp `title == lessonTitle` (so sau `strip()` và chuẩn hoá Unicode NFC) trong cùng khoá.
  - Khớp được đúng 1 bài → dùng bài đó. Nhiều bài → **chặn** (`Nhiều bài trên production trùng tên "<title>"`), không di chuyển file.
  - Không khớp được bằng cả hai cách → nhóm **"chưa có trên production"**.
- **Tìm thấy** → manifest import-ready dùng **`lessonOrder`, `lessonSlug`, `lessonTitle` của production** (để worker không đổi slug/tên bài); manifest phụ đề import-ready cũng thay `lessonSlug` bằng slug production (import CC khớp theo slug). `videoKey`, `r2Key`, `linkUrl` giữ nguyên. Chặn bài (không upload, không chuyển thư mục, báo lý do) nếu:
  - bài production có `description` khác rỗng → `Bài có mô tả trên production — import sẽ xoá mô tả` (worker luôn đặt `description = null`) **[quyết định của người viết spec]**
  - `partNo == 1` và section không có phần khác trong manifest: có period `order == sectionNo` với `title ≠ videoTitle` → `Tên tiết khác production: "<manifest>" ≠ "<production>"`. Video nhiều phần (`partNo > 1` hoặc nhiều phần cùng section): bỏ kiểm tra tên tiết, thêm cảnh báo `Tên tiết <sectionNo> sẽ đổi thành tên của phần xử lý sau cùng` **[quyết định của người viết spec]**
  - có video cùng `(sectionNo, partNo)` với `slug ≠ videoSlug` → `Slug video khác production: …`
- Bài tìm thấy và đã có video cùng `(sectionNo, partNo)` → gắn cờ **"ghi đè video đang có trên web"**.
- Hai bài trong lượt cùng `(courseId, lessonSlug, sectionNo, partNo)` → giữ quy tắc trùng đích hiện có.

### 4.3 Bài chưa có trên production
- Chỉ khi chạy **thật**: di chuyển `<T…>.mp4` và `<T…>/vi.vtt` từ vị trí trong `output_root` sang `<output_root>/_chua-co-tren-production/<3 tầng cuối giống vị trí cũ>` (tạo thư mục; `os.replace`; đích đã có thì ghi đè). Cập nhật `video_path`/`vtt_path` của job tương ứng để tải/xem vẫn chạy.
- Job cũ không có `output_root` (output trong `<leaf>/output/`): **không di chuyển**, chỉ loại khỏi lượt upload và báo `Bài chưa có trên production (không di chuyển vì job cũ)` **[quyết định của người viết spec]**.
- **Lần chạy sau [quyết định của người viết spec]:** job có file nằm trong `_chua-co-tren-production/` vẫn được tra lại production. Vẫn chưa có → bỏ qua (không di chuyển lần nữa), liệt kê là "đang chờ". Đã có trên production → di chuyển **về** vị trí chuẩn trong `output_root`, cập nhật job, rồi upload/import như bài thường. Đây là cách phục hồi sau khi bài được tạo trên production.

### 4.4 Xác nhận ghi đè
Khi có bài gắn cờ ghi đè, UI hỏi xác nhận trước khi bắt đầu (`{n} bài sẽ GHI ĐÈ video đang có trên web: … Tiếp tục?`). Backend tool nhận `"confirm_overwrite": true`; thiếu cờ mà có bài ghi đè → 409 với danh sách bài **[quyết định của người viết spec]**. Bước tra production chạy trong request khởi động (đồng bộ) để trả 409/401 ngay.

### 4.5 Upload R2
Như hiện nay, chỉ cho nhóm "đã có trên production" không bị chặn.

### 4.6 Import vào backend (chỉ chạy thật, sau khi bước video R2 có `Manifest:`)
1. **Sao lưu:** lưu phản hồi `GET /admin/lessons?courseId=…` (mọi trang) của các khoá liên quan vào `<export_dir>/production-before.json`.
2. **Manifest import-ready:** đọc manifest video do script tạo; chỉ giữ các mục đã upload thành công; thay `lessonOrder` bằng order production của bài khớp `(courseId, lessonSlug)`; ghi `<export_dir>/import/bulk-upload-import.json` (giữ nguyên các trường khác). Manifest gốc trong repo upload không đổi.
3. `POST import-manifest` với file trên → lưu `jobId`.
4. **Chờ worker [quyết định của người viết spec]:** backend không có API trạng thái cho job video, danh sách bài không có `videoKey`/`updatedAt` của video, và `durationMinutes` là số phút nguyên — khi **ghi đè** video có cùng số phút thì không quan sát được worker đã chạy. Vì vậy:
   - Mỗi 5 s đọc lại danh sách bài; điều kiện "quan sát được": mọi mục có video `(slug production, sectionNo, partNo)` với `durationMinutes == manifest.durationMinutes`.
   - Luôn chờ **tối thiểu 60 s** sau khi gửi manifest, kể cả khi điều kiện đã đúng ngay từ đầu; quá 10 phút mà điều kiện chưa đúng → `failed` `Worker chưa xử lý xong manifest video sau 10 phút` (không import phụ đề).
   - Mục nào thời lượng **trước và sau giống nhau** thì đánh dấu `chưa xác nhận được worker đã cập nhật` trong kết quả; UI hiện rõ dòng này (không coi là lỗi).
5. `POST import-cc-manifest` với manifest phụ đề do script tạo (chỉ các mục ứng với video đã import). `400` → lưu `missingTargets`/thông điệp vào lỗi lượt chạy.
6. **Đối chiếu:** đọc lại → `<export_dir>/production-after.json`; so với bản trước theo từng bài, **chỉ trên các trường** [quyết định của người viết spec]: bài `id, title, slug, order, description, durationMinutes`; period `order, title`; video `id, sectionNo, partNo, slug, durationMinutes`. Bỏ qua trường thay đổi do phụ đề/học sinh (`subtitles`, `transcriptSegmentCount`, `aiIndexStatus`, `pendingAiIndexJob`, `progressCount`, `documents`…). Thay đổi **được phép** ở bài đích: `durationMinutes` của bài và của video đích, video/period mới đúng `(sectionNo, partNo)` của manifest, tên tiết theo cảnh báo nhiều phần. Mọi khác biệt khác → `needs_review` + danh sách khác biệt; không tự hoàn tác.

### 4.7 Trạng thái và giao diện
- `UploadRunRecord` thêm: `production_checked: bool`, `skipped_not_on_production: list[{relpath, moved_to}]`, `blocked: list[{relpath, reason}]`, `overwrite: list[relpath]`, `import_manifest: str|None`, `import_job_id: str|None`, `cc_import_result: dict|None`, `verify_differences: list[str]`, `backup_path: str|None`; status thêm `needs_review`; phase thêm `production_check`, `import_video`, `wait_worker`, `import_cc`, `verify`.
- Hàng đợi, khu vực upload của batch: ô token, hai nút như cũ; khi chạy hiện phase tiếng Việt (`Đang tra production…`, `Đang import manifest video…`, `Đang chờ backend xử lý…`, `Đang import phụ đề…`, `Đang đối chiếu…`); kết quả liệt kê: đã import (n), chuyển sang thư mục chờ (đường dẫn), bị chặn (lý do), ghi đè (danh sách), kết quả phụ đề (`inserted/updated/skipped`), đường dẫn manifest import-ready, sao lưu, và cảnh báo `needs_review` nếu có.

## 5. Để builder tự quyết
Cấu trúc module (vd. `web/production_api.py`, `web/production_sync.py`), client HTTP (dùng `httpx` đã có trong dev? — nếu cần thêm dependency runtime thì dùng `urllib` như `voicestudio_client.py`), chi tiết bố cục UI.

## 6. Nghiệm thu
1. Test tự động với API production giả (server HTTP nội bộ trong test): phân loại 4 nhóm, chặn đúng 3 lý do, `lessonOrder` lấy từ production, di chuyển file + cập nhật job, 401 dừng trước upload, 409 ghi đè khi thiếu xác nhận, chờ worker (xong/timeout), import phụ đề `400 missingTargets`, đối chiếu phát hiện thay đổi ngoài danh sách → `needs_review`, token không xuất hiện trong state/log/manifest/env của `node`.
2. Thủ công trên máy này với production thật, **chỉ "Kiểm tra upload"** (không upload/không import): batch 3 bài Toán 6 T47–T49 → kế hoạch báo 3 bài đã có trên production, `lessonOrder` 29/29/30, cả 3 gắn cờ ghi đè; một bài mẫu slug không tồn tại → báo "chưa có trên production". Upload/import thật chỉ khi chủ dự án yêu cầu.
