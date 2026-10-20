# Auto Video Web — upload video và phụ đề lên R2 bằng một nút

Ngày: 2026-10-20 · Repo: `auto-video` (+ một sửa nhỏ ở `pyramid-upload-to-r2`) · Bổ sung cho `docs/superpowers/specs/2026-10-19-web-ui-design.md`

Ghi chú đọc: mục **[quyết định của người viết spec]** là lựa chọn mặc định chủ dự án chưa duyệt riêng.

## 1. Mục đích

Sau khi web auto-video tạo xong video bài giảng, chủ dự án tự upload **video (HLS) và phụ đề `vi.vtt`** lên Cloudflare R2 **ngay trên máy server** bằng các script có sẵn trong repo `pyramid-upload-to-r2`, rồi import manifest vào backend. Hiện việc này làm bằng tay (sắp lại thư mục, chạy `npm run upload`, `npm run upload:cc`). Mục tiêu: **một nút trên trang web** làm toàn bộ, và trang hiện **đường dẫn manifest trên máy server** để đem import.

## 2. Mục tiêu / ngoài phạm vi / không được xảy ra

**Mục tiêu**
- Output của folder lá đặt tên theo quy ước upload: `<tên folder lá>.mp4` + `vi.vtt`.
- Lúc quét, mỗi bài được kiểm tra "upload được hay không" theo đúng quy tắc của `pyramid-upload-to-r2` và báo lý do bằng tiếng Việt.
- Nút **"Upload lên R2"** cho mỗi batch: tự dựng cây export, chạy script upload video rồi script upload phụ đề, hiện tiến độ, và khi xong hiện **đường dẫn đầy đủ của manifest video và manifest phụ đề**.
- Nút **"Kiểm tra upload (không đẩy lên R2)"** chạy cùng luồng ở chế độ `--dry-run` của các script **[quyết định của người viết spec — để thử an toàn và demo]**.
- **Ai mở trang cũng bấm được** mọi nút (chủ dự án quyết).

**Ngoài phạm vi**
- Upload quiz/tài liệu; tự import manifest vào backend; đăng nhập/phân quyền; chạy upload trên Windows (bản này chạy trên máy Linux hiện tại; Windows để sau).
- Viết lại logic upload/slug/manifest trong Python.

**Không được xảy ra**
- Slug, R2 key hoặc định dạng manifest khác với khi chạy script bằng tay → **phải gọi chính script của `pyramid-upload-to-r2`**, không sao chép logic.
- Sửa, ghi đè hoặc in ra nội dung `.env` / khoá R2 (không hiện trên UI, không ghi vào log của tool).
- Upload bài chưa xong, bài lỗi, hoặc batch có hai bài cùng đích trên R2.
- Hai lần upload chạy chồng nhau.

## 3. Quy ước của `pyramid-upload-to-r2` (đã đọc code)

- Video (`scripts/local-bulk-upload.mjs`, `npm run upload`): nguồn `SOURCE_DIR`, cây **3 tầng** `<Mã môn>/<Bài N_Tên bài>/<T<tiết>[_<phần>]_<Tên tiết>>.mp4`; `parseVideoPath` dùng 3 thành phần cuối, mã môn tra trong `COURSE_MAPPING`/`COURSE_TITLES` (vd. `Toan6` → 601). Mỗi mp4: ffmpeg → HLS 720p (`veryfast`, CRF 23, đoạn 6 s) → `PutObject` cả thư mục lên `videos/courses/<courseSlug>/lessons/<lessonSlug>/<videoSlug>/` (`master.m3u8`). Manifest `output/manifests/bulk-upload-<ts>.json`; lỗi `output/errors/...`. In ra `[i/N] <path>`, `[OK] <videoKey>`, `[ERROR] ...`, cuối cùng `Manifest: <path>`. Có `--dry-run`.
- Phụ đề (`scripts/local-cc-upload.mjs`, `npm run upload:cc`): nguồn `CC_SOURCE_DIR`, cây **4 tầng** `<Mã môn>/<Bài…>/<T…>/vi.vtt`; tên file là mã ngôn ngữ. R2 key `.../<videoSlug>/subtitles/vi.vtt`; manifest `output/cc-manifests/cc-upload-<ts>.json` (kèm nội dung VTT). In `Manifest: <path>`. Có `--dry-run`.
- `local-cc-upload.mjs` đọc `.env` rồi **cho biến môi trường của tiến trình ghi đè** (`{ ...env, ...process.env }`); `local-bulk-upload.mjs` **chỉ đọc `.env`** (`readEnv()` không gộp `process.env`).
- Trên máy này: repo ở `/home/meconlonton/work/pyramid-upload-to-r2`, đã có `node_modules` (`@aws-sdk/client-s3`, `ffmpeg-static`, `ffprobe-static`), `.env` có đủ `R2_*`, `R2_VIDEO_CDN_BASE_URL`, `OUTPUT_DIR`; Node v24.

## 4. Thay đổi

### 4.1 Sửa nhỏ ở `pyramid-upload-to-r2` (điều kiện tiên quyết)
`readEnv()` trong `scripts/local-bulk-upload.mjs` trả `{ ...env, ...process.env }` giống `local-cc-upload.mjs`, để tool truyền `SOURCE_DIR` (và `OUTPUT_DIR` khi dry-run) qua biến môi trường mà không sửa `.env`. Để test được: tách thành hàm export `mergeEnv(fileEnv, processEnv)` thuần, `readEnv()` gọi nó; test mới `tests/local-bulk-upload-env.test.mjs` chạy bằng `node --test tests/local-bulk-upload-env.test.mjs` **[quyết định của người viết spec]**. Lưu ý: các script `npm run test:*` hiện có **đã hỏng sẵn** (file test `.mjs` chúng trỏ tới không tồn tại) — không thuộc phạm vi sửa.
- **Nơi commit (chủ dự án quyết):** commit thẳng lên nhánh `quang` đang dùng của `pyramid-upload-to-r2`. Nhánh này đang có thay đổi chưa commit (`README.md`, `.env.example`, `cc_web/*`, `scripts/extract_cc.py`) và file chưa theo dõi (`tests/`, `reference/`, `Subject_holder/`, script quiz): **chỉ stage đúng** `scripts/local-bulk-upload.mjs` và `tests/local-bulk-upload-env.test.mjs`, không đụng các thay đổi khác; không push trừ khi chủ dự án yêu cầu.

### 4.2 Tên output của folder lá
- `<leaf>/output/` gồm `<tên folder lá>.mp4` và `vi.vtt` (thay cho `<stem pptx>.mp4`). Tên folder lá đi qua `safe_relative_path` khi upload nên đã hợp lệ làm tên file.
- Zip tải về dùng tên mới. Các bài đã chạy trước thay đổi giữ nguyên tên cũ (không đổi tên hồi tố) **[quyết định của người viết spec]**; upload R2 chấp nhận cả hai bằng cách lấy video trong `output/` (đúng 1 file `.mp4`).

### 4.3 Danh tính upload của mỗi bài
- Lấy **3 thành phần cuối** của `leaf_relpath`: `<Mã môn>/<Bài…>/<T…>` (giống `parseVideoPath`). Folder gốc upload có thể là mã môn (`Toan6/...`) hoặc một folder bọc ngoài (`Ngay 20-10/Toan6/...`).
- Kiểm tra bằng **chính `parseVideoPath`** của repo upload: tool gọi một helper Node nhỏ (trong auto-video) `import { parseVideoPath } from '<repo>/scripts/local-bulk-upload.mjs'`, đọc danh sách đường dẫn qua stdin, trả JSON `{path: identity|null}` **[quyết định của người viết spec — một nguồn sự thật]**.
- Lý do không upload được (tiếng Việt), hiện cạnh từng bài ở tab Tạo video và Hàng đợi:
  - `Thiếu tầng Mã môn/Bài: cần dạng <Mã môn>/<Bài N_Tên bài>/<T<tiết>_Tên tiết>` (relpath < 3 thành phần)
  - `Mã môn "<x>" không có trong danh sách khoá học` / `Tên bài hoặc tên tiết không đúng dạng "Bài N_…" / "T<số>_…"` (khi `parseVideoPath` trả null; phân biệt hai lý do bằng `getCourseId`)
  - `Trùng đích R2 với bài <relpath khác>` (hai bài cùng `courseSlug/lessonSlug/videoSlug`): **chỉ các bài trùng** bị loại khỏi lượt upload, các bài khác vẫn upload **[quyết định của người viết spec]**.
- Bài có folder bài **không** theo dạng `Bài N_…` (vd. `Ôn tập chương 1`): `parseVideoPath` vẫn chấp nhận với `lessonOrder = null` và script in `[BAT BUOC] … Dien lessonOrder … truoc khi import`. Tool **cho upload** nhưng hiện ⚠️ `Bài không có số thứ tự — cần điền lessonOrder trong manifest trước khi import` cạnh bài và cạnh đường dẫn manifest **[quyết định của người viết spec — giữ đúng hành vi script]**.
- Không chặn việc **tạo video**; chỉ chặn upload.

### 4.4 Cây export
Mỗi lần bấm upload/kiểm tra tạo `video_and_cc/exports/<batch-id>/<YYYYmmdd-HHMMSS>/`:
```text
video/<Mã môn>/<Bài…>/<T…>.mp4          (hard link tới output nếu cùng ổ, không thì copy)
cc/<Mã môn>/<Bài…>/<T…>/vi.vtt
```
Chỉ gồm job **COMPLETED** có danh tính hợp lệ và có đủ `.mp4` + `vi.vtt`. Không có bài nào → nút bị khoá.

### 4.5 Chạy upload
- Tiến trình nền (thread) chạy tuần tự trong `cwd=<repo>`:
  1. `node scripts/local-bulk-upload.mjs [--dry-run]` với env `SOURCE_DIR=<export>/video`
  2. `node scripts/local-cc-upload.mjs [--dry-run]` với env `CC_SOURCE_DIR=<export>/cc`
- **Lỗi một phần [quyết định của người viết spec]:** cả hai script đặt exit code 1 khi chỉ một file lỗi nhưng vẫn in `Manifest:`. Vì vậy:
  - Không có dòng `Manifest:` ở bước 1 → lỗi toàn cục, **không** chạy bước 2, lượt = `failed`.
  - Có `Manifest:` → đọc manifest video, lấy các `relativePath` đã thành công; **chỉ dựng `cc/` cho đúng các bài đó** (dựng cây `cc/` sau bước 1, không dựng trước) rồi chạy bước 2. Không bao giờ upload phụ đề cho video chưa lên R2.
  - Trạng thái lượt: `succeeded` (mọi bài ok cả video lẫn phụ đề), `partial` (có bài lỗi), `failed` (không bài nào lên).
- **Dry-run không được ghi manifest cạnh manifest thật [quyết định của người viết spec]:** khi `--dry-run`, truyền thêm env `OUTPUT_DIR=<export>/dry-run-output` để manifest/errors nằm trong thư mục export; UI ghi rõ "(kiểm tra — KHÔNG import)" cạnh đường dẫn và không có nút Sao chép.
- Toàn bộ stdout/stderr ghi vào `video_and_cc/logs/upload-<batch-id>-<ts>.log`. **Không** truyền/ghi khoá R2 (chỉ kế thừa `.env` mà script tự đọc).
- Tiến độ: đọc dòng `[i/N]`, `[OK]`, `[ERROR]` để hiện "Video 2/3", "Phụ đề 1/3", số lỗi.
- Kết quả: lấy đường dẫn từ dòng `Manifest: <path>` của mỗi script (tuyệt đối hoá theo `cwd`). Lỗi từng file: đường dẫn file errors nếu script in ra, hoặc trích dòng `[ERROR]`.
- Toàn server chỉ một lượt upload tại một thời điểm; bấm khi đang chạy → 409 `Đang có một lượt upload khác chạy` **[quyết định của người viết spec]**.
- Upload lại cùng batch: ghi đè key cũ trên R2, tạo manifest mới (chủ dự án chấp nhận hành vi của script).
- HLS chạy trên CPU song song với render của các job khác — chấp nhận **[quyết định của người viết spec]**.

### 4.6 Trạng thái lưu
`state/uploads.json`: mỗi lượt `{id, batch_id, dry_run, status: running|succeeded|failed|interrupted, started_at, finished_at, video_total, video_ok, cc_total, cc_ok, video_manifest, cc_manifest, log_path, errors: [str]}`. Server khởi động lại khi đang chạy → `interrupted`.

### 4.7 Cấu hình
- `--upload-repo` (mặc định: biến môi trường `PYRAMID_UPLOAD_REPO`, không có thì `None`) **[quyết định của người viết spec]**. `run-web.sh` trên máy này truyền `/home/meconlonton/work/pyramid-upload-to-r2`.
- `/api/status` thêm `upload_ready: bool` + `upload_reason` (repo không tồn tại / thiếu `node` trên PATH / thiếu `node_modules` / thiếu `.env`). Không đọc giá trị trong `.env`, chỉ kiểm tra file tồn tại.

### 4.8 Giao diện (tab Hàng đợi, đầu mỗi batch)
```text
Batch Toan6 (upload 16:08) · 3/3 xong · [⬇ Tải tất cả video (.zip)] [Kiểm tra upload] [⬆ Upload lên R2]
  Upload lần cuối: ✅ 16:52 · video 3/3 · phụ đề 3/3
  Manifest video:   /home/meconlonton/work/pyramid-upload-to-r2/output/manifests/bulk-upload-….json   [Sao chép]
  Manifest phụ đề:  /home/meconlonton/work/pyramid-upload-to-r2/output/cc-manifests/cc-upload-….json [Sao chép]
  Log: /home/meconlonton/video_and_cc/logs/upload-….log
```
- Bấm "Upload lên R2" → hộp xác nhận `Upload {n} video và {n} phụ đề lên R2?` **[quyết định của người viết spec]**.
- Đang chạy: `Đang chuyển HLS & upload video 2/3…` → `Đang upload phụ đề 1/3…`.
- Bài không upload được: biểu tượng ⚠️ + lý do §4.3; nút khoá kèm lý do khi `upload_ready` sai hoặc không có bài hợp lệ.
- Lượt dry-run ghi rõ "(kiểm tra, chưa đẩy lên R2)".

### 4.9 Thư mục kết quả do người dùng nhập (bổ sung 2026-10-20, chủ dự án duyệt)

Thay cho vị trí cố định `<leaf>/output/` trong `uploads/`:

- Tab Tạo video có ô **"Thư mục lưu kết quả (trên máy server)"** phía trên nút tạo; điền sẵn thư mục dùng lần trước. Người dùng nhập trước khi bấm tạo; mỗi job nhớ thư mục kết quả của nó.
- Kiểm tra khi bấm tạo: cho phép `~` (mở rộng thành thư mục home của tiến trình server); sau đó phải là đường dẫn tuyệt đối; chưa có thì tự tạo; phải ghi được; không được nằm trong `uploads/`, `work/`, `tts-cache/`, `state/`, `logs/`, `exports/` của thư mục làm việc. Sai → lỗi tiếng Việt, không tạo job.
- **Cách sắp xếp (để upload tiện nhất):** thư mục kết quả dùng được trực tiếp làm **cả** `SOURCE_DIR` lẫn `CC_SOURCE_DIR` của `pyramid-upload-to-r2`:
  ```text
  <thư mục kết quả>/Toan6/Bài 19_…/T48_Hình thang cân.mp4      ← script video: 3 tầng cuối
  <thư mục kết quả>/Toan6/Bài 19_…/T48_Hình thang cân/vi.vtt   ← script CC: đúng 4 tầng
  ```
  - Vị trí lấy **3 tầng cuối** của `leaf_relpath` (bỏ folder bọc ngoài, vì script CC đòi đúng 4 tầng). Bài có ít hơn 3 tầng: dùng nguyên `leaf_relpath` (vẫn lưu được, upload báo lỗi như §4.3).
  - Không tạo `<leaf>/output/` trong `uploads/` cho job mới.
  - Ghi đè từng file một cách nguyên tử (ghi file tạm cùng thư mục rồi `os.replace`); **chỉ** thay `<T…>.mp4` và `<T…>/vi.vtt`, không xoá/thay cả thư mục `<T…>/` (sau này có thể chứa quiz `.json`).
  - Hai bài (cùng lượt bấm, hoặc với job đang chờ/chạy) trỏ tới **cùng vị trí** → từ chối: `Trùng vị trí lưu với bài <relpath khác>`.
- Nút tải MP4/zip, xem phụ đề và upload R2 đọc file từ vị trí mới (job lưu cả đường dẫn video và phụ đề). Job cũ (trước thay đổi) vẫn đọc từ `<leaf>/output/`.
- Hàng đợi hiện `Lưu tại: <đường dẫn video>` cho bài đã xong.
- Nút "Upload lên R2" vẫn chỉ upload các bài của batch đang bấm (qua cây export), không upload cả thư mục kết quả.

## 5. Để builder tự quyết
Tên module, định dạng API JSON, cách đọc tiến độ từ stdout, hard link hay copy, bố cục chi tiết các dòng kết quả.

## 6. Nghiệm thu
1. Test tự động: danh tính upload (3 thành phần cuối, các lý do lỗi, trùng đích), dựng cây export (chỉ job xong, đúng tên), chạy upload với `node` giả (thứ tự 2 bước, bước 1 lỗi thì dừng, đọc `Manifest:`, ghi log, không lộ biến `R2_*`), khoá một lượt upload, khôi phục `interrupted`, tên output mới. Test hiện có vẫn pass.
2. `pyramid-upload-to-r2`: `node --test tests/local-bulk-upload-env.test.mjs` pass (`process.env` ghi đè `.env`); `npm run dry-run` vẫn chạy như trước.
3. Thủ công trên máy này: upload một batch đúng 3 tầng `Toan6/Bài …/T…_…` (2 bài) → tạo video → **Kiểm tra upload** (dry-run) hiện 2 đường dẫn manifest hợp lệ, nội dung manifest khớp khi chạy script bằng tay. Upload thật chỉ chạy khi chủ dự án đồng ý, với một bài.
4. Batch `test gen/T48_…` (thiếu tầng) → hiện lý do và nút upload bị khoá; tạo video vẫn được.
