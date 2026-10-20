# Production Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Upload lên R2" tra production bằng token admin, chỉ upload bài đã có trên production với `lessonOrder`/slug/tên thật, chuyển bài chưa có sang `_chua-co-tren-production/`, rồi tự import manifest video + phụ đề vào backend và đối chiếu trước/sau.

**Architecture:** `web/production_api.py` (client HTTP urllib tới API admin), `web/production_sync.py` (hàm thuần: phân loại, manifest import-ready, điều kiện chờ, đối chiếu), mở rộng `output_location.py` (đường dẫn chờ + di chuyển), `UploadService` điều phối (tra đồng bộ khi khởi động, chạy nền upload → import → đối chiếu). Token chỉ truyền qua tham số hàm, không lưu.

**Tech Stack:** Python ≥ 3.12 (urllib, không thêm dependency), FastAPI, pytest, HTML/JS thuần.

**Spec:** `docs/superpowers/specs/2026-10-21-production-sync-design.md` (đọc kèm; bổ sung cho `2026-10-20-r2-upload-design.md`).

## Global Constraints

- Token admin: không ghi vào `state/*.json`, log, manifest, export, response API; không đặt vào env của tiến trình `node`; mọi chuỗi lỗi/log đi qua hàm che token (`***`).
- Không gọi API production thật trong test; dùng server HTTP nội bộ (`ThreadingHTTPServer`) như `tests/test_voicestudio_client.py`.
- Chuỗi tiếng Việt dùng nguyên văn từ spec/plan.
- Base API mặc định `https://api.pyramid.edu.vn/api/v1`; phản hồi backend bọc `{"data": …}`.
- Không thêm dependency runtime. Toàn bộ test pass: `PYTHONPATH=<worktree> /home/meconlonton/work/auto-video/.venv/bin/python -m pytest -q`.

## Review Focus

1. Tên bài/tiết có dấu tiếng Việt dạng NFD trong manifest so với NFC trên production → vẫn khớp (test Task 3).
2. API trả nhiều trang (`totalPages > 1`) → đọc đủ mọi trang (test Task 1).
3. Token xuất hiện trong thông báo lỗi HTTP (vd. server phản hồi lại header) → bị che (test Task 1, Task 5).
4. Server tool khởi động lại giữa lúc đang chờ worker → lượt chạy `interrupted`, không import phụ đề (test Task 5).
5. Di chuyển file sang `_chua-co-tren-production/` khi đích đã có file cũ → ghi đè, không lỗi; job cũ không có `output_root` → không di chuyển (test Task 4).

---

### Task 1: `web/production_api.py`

**Interfaces:**
```python
DEFAULT_BACKEND_API = "https://api.pyramid.edu.vn/api/v1"
class ProductionApiError(RuntimeError):
    detail: Any            # phần JSON lỗi của backend nếu có (vd. missingTargets)
class ProductionAuthError(ProductionApiError): ...   # message "Token admin không hợp lệ hoặc đã hết hạn"
def redact_token(text: str, token: str) -> str       # thay token bằng "***"
class ProductionApi:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0, opener=urllib.request.urlopen) -> None
    def list_lessons(self, course_id: int) -> list[dict[str, Any]]   # GET /admin/lessons?courseId=&limit=200&page=n, mọi trang, trả items
    def import_video_manifest(self, path: Path) -> str               # POST /admin/bulk-upload/import-manifest, multipart field "manifest"; trả data.jobId
    def import_cc_manifest(self, path: Path) -> dict[str, Any]       # POST /admin/bulk-upload/import-cc-manifest; trả data
```
401/403 → `ProductionAuthError`; status khác 2xx → `ProductionApiError(f"Backend trả HTTP {code}: {thông điệp}")` với `detail` = JSON body; lỗi mạng → `ProductionApiError("Không kết nối được backend production: …")`. Mọi message qua `redact_token`.

- [ ] **Step 1: Test thất bại** (server stub): `list_lessons` gửi `Authorization: Bearer <token>`, ghép 2 trang (`totalPages: 2`) (Review Focus 2); 401 → `ProductionAuthError` đúng chuỗi; 400 `{"message":"…","missingTargets":[…]}` từ import CC → `ProductionApiError.detail["missingTargets"]`; multipart có field `manifest` đúng bytes; stub trả lỗi chứa chuỗi token → message không chứa token (Review Focus 3).
- [ ] **Step 2–4:** FAIL → cài đặt → PASS + toàn bộ suite.
- [ ] **Step 5:** log `feat: add production admin API client`

---

### Task 2: Mở rộng danh tính upload

**Files:** `video_workflow/web/upload_identity.py`; test `tests/test_web_upload_identity.py`.

- `UploadIdentity` thêm `section_no: int`, `part_no: int`, `lesson_title: str` (lấy từ `sectionNo`, `partNo`, `lessonTitle` của `parseVideoPath`; helper Node đã trả các trường này). Cập nhật mọi chỗ tạo `UploadIdentity` (kể cả test giả).

- [ ] **Step 1: Test thất bại:** identity của `Toan6/Bài 19_X/T48_Y` có `section_no == 48`, `part_no == 1`, `lesson_title == "Bài 19: X"`; `T48_2_Y` → `part_no == 2`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** log `feat: keep section, part and lesson title in upload identity`

---

### Task 3: `web/production_sync.py` (hàm thuần)

**Interfaces:**
```python
@dataclass(frozen=True, slots=True)
class SyncTarget:
    relpath: str; identity: UploadIdentity; item: ExportItem
@dataclass(frozen=True, slots=True)
class ReadyTarget:
    target: SyncTarget
    lesson: dict[str, Any]        # bài production đã khớp
    overwrite: bool               # đã có video cùng (sectionNo, partNo)
    warnings: list[str]
@dataclass(frozen=True, slots=True)
class SyncPlan:
    ready: list[ReadyTarget]
    not_on_production: list[SyncTarget]
    blocked: list[tuple[SyncTarget, str]]
def norm(text: str | None) -> str                         # NFC + strip
def plan_production_sync(targets: Sequence[SyncTarget], lessons_by_course: Mapping[int, list[dict[str, Any]]]) -> SyncPlan
def import_ready_video_manifest(manifest: dict[str, Any], plan: SyncPlan) -> dict[str, Any]
def import_ready_cc_manifest(manifest: dict[str, Any], plan: SyncPlan, uploaded_triples: set[str]) -> dict[str, Any]
def worker_observed(lessons_by_course: Mapping[int, list[dict]], plan: SyncPlan, video_manifest: dict) -> bool
def unconfirmed_targets(before: Mapping[int, list[dict]], after: Mapping[int, list[dict]], plan: SyncPlan, video_manifest: dict) -> list[str]
def verify_changes(before: Mapping[int, list[dict]], after: Mapping[int, list[dict]], plan: SyncPlan, video_manifest: dict) -> list[str]
```
Quy tắc (spec §4.2, §4.6) và **chuỗi chính xác**:
- Khớp bài: `norm(slug) == norm(lesson_slug)`; nếu không có thì `norm(title) == norm(lesson_title)`; >1 kết quả → blocked `f'Nhiều bài trên production trùng tên "{title}"'`; 0 → `not_on_production`.
- Blocked (theo thứ tự): `description` khác rỗng → `"Bài có mô tả trên production — import sẽ xoá mô tả"`; (partNo == 1 và section không có phần khác trong targets) period `order == section_no` có `title ≠ video_title` → `f'Tên tiết khác production: "{video_title}" ≠ "{period_title}"'`; video cùng `(section_no, part_no)` có `slug ≠ video_slug` → `f'Slug video khác production: "{video_slug}" ≠ "{production_slug}"'`.
- Nhiều phần cùng section hoặc `part_no > 1` → warning `f"Tên tiết {section_no} sẽ đổi thành tên của phần xử lý sau cùng"`.
- `overwrite = True` khi có video cùng `(section_no, part_no)`.
- `import_ready_video_manifest`: chỉ giữ mục `relativePath == f"{triple}.mp4"` của ready target, thay `lessonOrder`, `lessonSlug`, `lessonTitle` bằng giá trị bài production; giữ nguyên trường khác.
- `import_ready_cc_manifest`: chỉ giữ subtitle của triple trong `uploaded_triples`, thay `lessonSlug` bằng slug production; giữ `r2Key`, `linkUrl`.
- `worker_observed`: mọi mục manifest import-ready có video `(sectionNo, partNo)` trong bài production (khớp theo slug production) với `durationMinutes == manifest durationMinutes`.
- `unconfirmed_targets`: relpath các mục có `durationMinutes` trước == sau == manifest.
- `verify_changes`: so theo các trường spec §4.6.6; thay đổi được phép như spec; trả danh sách chuỗi mô tả khác biệt (vd. `f"Bài {order} {title}: {field} {old!r} → {new!r}"`).

- [ ] **Step 1: Test thất bại:** khớp slug; khớp tên khi slug khác (`29-bai-19-…`); trùng tên → blocked; không khớp → not_on_production; 3 lý do chặn đúng chuỗi; tên NFD trong manifest vẫn khớp production NFC (Review Focus 1); cảnh báo nhiều phần; manifest import-ready chỉ đổi 3 trường (so với dict gốc); CC import-ready đổi `lessonSlug`, giữ `r2Key`; `worker_observed` true/false; `unconfirmed_targets`; `verify_changes` rỗng với thay đổi được phép, có dòng khi bài khác đổi `title`, khi bài đích đổi `slug`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** log `feat: plan production sync and build import-ready manifests`

---

### Task 4: Thư mục chờ + di chuyển

**Files:** `video_workflow/web/output_location.py`, `video_workflow/web/state.py`, `video_workflow/web/scheduler.py`; tests `tests/test_web_output_location.py`, `tests/test_web_scheduler.py`.

**Interfaces:**
```python
PARKED_DIR = "_chua-co-tren-production"
def parked_paths(output_root: Path, leaf_relpath: str) -> tuple[Path, Path]     # output_root/PARKED_DIR/<output_relative_parts>
def is_parked(output_root: Path, video_path: Path) -> bool
def move_output_files(video_src: Path, vtt_src: Path, video_dest: Path, vtt_dest: Path) -> None   # mkdir; os.replace; ghi đè đích
JobScheduler.update_output_paths(job_id: str, video_path: str, vtt_path: str) -> WebJob   # lưu store; không đổi status
```
- [ ] **Step 1: Test thất bại:** `parked_paths(Path("/o"), "Ngay/Toan6/Bài 1_X/T1_Y")` == `(Path("/o/_chua-co-tren-production/Toan6/Bài 1_X/T1_Y.mp4"), Path("/o/_chua-co-tren-production/Toan6/Bài 1_X/T1_Y/vi.vtt"))`; `move_output_files` khi đích có file cũ → ghi đè (Review Focus 5); `update_output_paths` lưu bền qua `WebStore`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** log `feat: park lessons that are not on production`

---

### Task 5: Điều phối trong `UploadService`

**Files:** `video_workflow/web/upload_service.py`, `video_workflow/web/state.py` (`UploadRunRecord`); test `tests/test_web_upload_service.py`.

**Interfaces:**
```python
class AdminTokenRequiredError(ValueError): ...          # "Cần token admin để upload lên R2"
class OverwriteConfirmationRequired(RuntimeError):
    overwrite: list[str]                                 # relpaths
UploadService.__init__(..., production_api_factory: Callable[[str, str], ProductionApi] = ProductionApi,
                       backend_api: Callable[[], str] = lambda: DEFAULT_BACKEND_API,
                       sleep=time.sleep, monotonic=time.monotonic,
                       min_wait_seconds: float = 60, max_wait_seconds: float = 600, poll_seconds: float = 5)
UploadService.start(batch_id: str, *, dry_run: bool, admin_token: str | None = None, confirm_overwrite: bool = False) -> UploadRunRecord
```
`UploadRunRecord` thêm (mặc định để đọc record cũ): `production_checked: bool = False`, `skipped_not_on_production: list[dict] = []` (`{"relpath","moved_to"}`), `blocked: list[dict] = []` (`{"relpath","reason"}`), `overwrite: list[str] = []`, `warnings: list[str] = []`, `import_manifest: str | None = None`, `import_job_id: str | None = None`, `cc_import_result: dict | None = None`, `unconfirmed: list[str] = []`, `verify_differences: list[str] = []`, `backup_path: str | None = None`. Status thêm `needs_review`; phase thêm `production_check`, `import_video`, `wait_worker`, `import_cc`, `verify`.

Luồng `start` (đồng bộ, trong lock): candidates hiện có + job có file đang nằm trong thư mục chờ (`is_parked`) → nếu không `dry_run` mà thiếu token → `AdminTokenRequiredError`; có token → `list_lessons` cho mọi `course_id` (lỗi `ProductionAuthError`/`ProductionApiError` ném thẳng ra) → `plan_production_sync`; không `dry_run` và có overwrite mà `confirm_overwrite` sai → `OverwriteConfirmationRequired`; **chỉ khi chạy thật**: bài not_on_production chưa nằm ở thư mục chờ và job có `output_root` → `move_output_files` sang `parked_paths` + `update_output_paths`; bài parked nay đã ready → di chuyển **về** `output_paths` + `update_output_paths`; bài parked vẫn not_on_production → không di chuyển, ghi `moved_to` = vị trí hiện tại. Upload chỉ ready targets. Không token và dry_run → giữ hành vi cũ, `warnings` có `"Chưa tra production (không có token admin)"`.

Thread nền (chạy thật, sau `run_r2_upload` có `video_manifest`): backup `production-before.json` → ghi `export_dir/import/bulk-upload-import.json` (import-ready) → `import_video_manifest` → chờ (`min_wait_seconds`, poll `poll_seconds`, tới `max_wait_seconds`; quá hạn → `failed` `"Worker chưa xử lý xong manifest video sau 10 phút"`) → ghi `export_dir/import/cc-upload-import.json` → `import_cc_manifest` (lỗi → `failed`, lưu `detail` vào errors) → `production-after.json` → `unconfirmed_targets`, `verify_changes` (có khác biệt → `needs_review`). `stop()` trong lúc chờ → `interrupted`, không import phụ đề (Review Focus 4). Token chỉ nằm trong biến cục bộ/closure; mọi lỗi qua `redact_token`.

- [ ] **Step 1: Test thất bại** (ProductionApi giả + `run_r2_upload` giả + `sleep`/`monotonic` giả): phân loại và di chuyển đúng; dry-run không di chuyển, không import; thiếu token khi chạy thật → `AdminTokenRequiredError`; overwrite chưa xác nhận → `OverwriteConfirmationRequired` liệt kê relpath; luồng import đầy đủ ghi 4 file trong export_dir và gọi API đúng thứ tự; timeout chờ → `failed`, không gọi import CC; `verify_changes` khác rỗng → `needs_review`; record trong `uploads.json` và file log **không chứa token** (đặt token `"tok-SECRET"` và grep); parked bài nay đã có → di chuyển về và upload; stop khi đang chờ → `interrupted`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS + toàn bộ suite.
- [ ] **Step 5:** log `feat: sync uploads with production and import manifests`

---

### Task 6: Cấu hình + API

**Files:** `video_workflow/web/config.py` (`parse_args`), `video_workflow/web/__main__.py`, `video_workflow/web/app.py`; tests `tests/test_web_config.py`, `tests/test_web_app.py`.

- `--backend-api` mặc định `os.environ.get("PYRAMID_BACKEND_API") or DEFAULT_BACKEND_API`; truyền vào `UploadService`.
- `UploadRequest`: `dry_run: StrictBool`, `admin_token: str | None = None`, `confirm_overwrite: StrictBool = False`.
- Map lỗi: `AdminTokenRequiredError` → 400; `ProductionAuthError` → 401 `"Token admin không hợp lệ hoặc đã hết hạn"`; `ProductionApiError` → 502 với message; `OverwriteConfirmationRequired` → 409 `{"detail": {"message": f"{n} bài sẽ GHI ĐÈ video đang có trên web", "overwrite": [...]}}`. Response không bao giờ chứa token.

- [ ] **Step 1: Test thất bại:** 4 mã lỗi trên; `parse_args([])` có `backend_api == "https://api.pyramid.edu.vn/api/v1"`; response thành công không chứa giá trị token gửi lên.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5:** log `feat: accept admin token and overwrite confirmation in upload API`

---

### Task 7: Giao diện + README

**Files:** `video_workflow/web/static/index.html`, `app.js`, `styles.css`, `README.md`; test `tests/test_web_app.py`.

- Khu vực upload mỗi batch: `<input type="password" placeholder="Token admin (production)">` dùng chung cho 2 nút, không lưu vào `localStorage`.
- Bấm Upload: gửi `{dry_run:false, admin_token}`; 409 có `overwrite` → `confirm(\`${n} bài sẽ GHI ĐÈ video đang có trên web:\n${list}\nTiếp tục?\`)` → gửi lại với `confirm_overwrite:true`; 401/400/502 → toast `detail`.
- Nhãn phase: `production_check` "Đang tra production…", `import_video` "Đang import manifest video…", `wait_worker` "Đang chờ backend xử lý…", `import_cc` "Đang import phụ đề…", `verify` "Đang đối chiếu…".
- Kết quả: số bài đã import; `Chuyển sang thư mục chờ: <moved_to>`; `Bị chặn: <relpath> — <reason>`; `Ghi đè: <relpath>`; `Phụ đề: thêm {inserted}, cập nhật {updated}, bỏ qua {skipped}`; `Chưa xác nhận được worker đã cập nhật: <relpath>`; `Manifest import: <path>`, `Sao lưu production: <path>`; trạng thái `needs_review` → khung cảnh báo `Cần kiểm tra: có thay đổi ngoài danh sách` + danh sách khác biệt.
- README: mục "Đồng bộ production": token, `--backend-api`, thư mục `_chua-co-tren-production`, các bước import tự động, ý nghĩa `needs_review`.

- [ ] **Step 1: Test thất bại:** `index.html`/`app.js` chứa `Token admin (production)`, `GHI ĐÈ`, `Đang chờ backend xử lý…`, `Cần kiểm tra`; `app.js` không chứa `localStorage.setItem` với token.
- [ ] **Step 2–4:** FAIL → cài đặt → `node --check` + PASS toàn bộ.
- [ ] **Step 5:** log `feat: add production sync controls to the queue`

---

### Task 8: Nghiệm thu với API production giả (Codex) — production thật do Claude chạy riêng

- [ ] **Step 1:** Toàn bộ test pass.
- [ ] **Step 2:** Viết script `/tmp/sync-accept/fake_backend.py` (không commit) giả lập `GET /admin/lessons`, `POST import-manifest` (cập nhật `durationMinutes` sau 2 s), `POST import-cc-manifest`; chạy server tool trên cổng 8090 với `--root /tmp/sync-accept/root --backend-api http://127.0.0.1:<port>/api/v1` và `PYRAMID_UPLOAD_REPO=/home/meconlonton/work/pyramid-upload-to-r2`; **không** đụng server 8080/VoiceStudio 3900; **không** upload thật lên R2. Chỉ chạy "Kiểm tra upload" (dry-run) qua API với fake backend và một batch có job COMPLETED giả lập (copy output có sẵn từ `/home/meconlonton/gen_video`), xác nhận kế hoạch báo đúng: bài có trên production, bài không có, bài ghi đè, bài bị chặn. Luồng import đầy đủ (chạy thật) đã được test tự động ở Task 5.
- [ ] **Step 3:** Ghi `docs/superpowers/plans/2026-10-21-production-sync-acceptance.md` (chỉ kết quả thật), log `docs: record production sync acceptance`. Tắt server 8090 và fake backend đúng PID.
