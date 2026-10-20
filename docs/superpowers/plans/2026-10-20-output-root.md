# Output Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Người dùng nhập thư mục lưu kết quả (trên máy server) trước khi tạo video; kết quả được xếp theo đúng cây `<Mã môn>/<Bài…>/<T…>.mp4` + `<T…>/vi.vtt` để thư mục đó dùng thẳng làm `SOURCE_DIR` và `CC_SOURCE_DIR` của `pyramid-upload-to-r2`.

**Architecture:** Module mới `video_workflow/web/output_location.py` (kiểm tra thư mục, tính vị trí, ghi file nguyên tử). `WebJob` thêm `output_root`, `vtt_path`; scheduler chặn trùng vị trí; runner ghi vào vị trí mới; upload service, API, UI đọc từ đường dẫn lưu trong job. Job cũ (không có `output_root`) giữ hành vi `<leaf>/output/`.

**Tech Stack:** Python ≥ 3.12, FastAPI, pytest, HTML/JS thuần.

**Spec:** `docs/superpowers/specs/2026-10-20-r2-upload-design.md` mục **4.9** (đọc kèm cả spec).

## Global Constraints

- Chuỗi tiếng Việt dùng nguyên văn trong plan.
- Không đổi pipeline sinh giọng/render, không đổi script upload, không đổi `VoiceStudioConfig`.
- Tương thích ngược: `WebJob.from_dict` chấp nhận record thiếu `output_root`/`vtt_path`; job cũ vẫn tải/xem/upload được từ `<leaf>/output/`.
- Toàn bộ test pass: `PYTHONPATH=<worktree> /home/meconlonton/work/auto-video/.venv/bin/python -m pytest -q`.

## Review Focus

1. Đường dẫn có dấu tiếng Việt/dấu cách (`/home/x/Video bài giảng/…`) → tạo thư mục và ghi file đúng (test Task 1).
2. Người dùng nhập `~/Videos/out` → mở rộng đúng, không tạo thư mục tên `~` (test Task 1).
3. Thư mục `T48_…/` đã có file khác (quiz `.json`) → tạo lại bài không xoá file đó (test Task 1).
4. Hai bài ở hai folder bọc khác nhau cùng ra `Toan6/Bài 19…/T48…` → bị chặn, không ghi đè lẫn nhau (test Task 2).
5. Retry một job cũ tạo trước thay đổi (không có `output_root`) → vẫn chạy và lưu vào `<leaf>/output/` như trước (test Task 3).

---

### Task 1: `web/output_location.py`

**Interfaces:**
```python
class OutputRootError(ValueError): ...
INTERNAL_DIRS = ("uploads", "work", "tts-cache", "state", "logs", "exports")
def resolve_output_root(raw: str, workspace_root: Path) -> Path
def output_relative_parts(leaf_relpath: str) -> tuple[str, ...]      # 3 thành phần cuối nếu ≥ 3, ngược lại tất cả
def output_paths(output_root: Path, leaf_relpath: str) -> tuple[Path, Path]
    # (root/*parts[:-1]/f"{parts[-1]}.mp4", root/*parts/"vi.vtt")
def publish_output_files(video_src: Path, vtt_text: str, video_dest: Path, vtt_dest: Path) -> None
    # mkdir parents; mỗi file: ghi "<tên>.tmp-<uuid>" cùng thư mục rồi os.replace; vtt UTF-8, newline "\n"
```
`resolve_output_root` (thông điệp chính xác):
- rỗng/chỉ khoảng trắng → `"Cần nhập thư mục lưu kết quả"`
- sau `expanduser` không tuyệt đối → `"Thư mục lưu kết quả phải là đường dẫn tuyệt đối"`
- nằm trong `workspace_root/<một trong INTERNAL_DIRS>` (so sau `resolve()`) → `"Không được lưu vào thư mục nội bộ của tool"`
- không tạo được / không ghi được (thử tạo rồi xoá một file tạm) → `f"Không ghi được vào thư mục: {path}"`
- trả `Path` đã `resolve()`.

- [ ] **Step 1: Test thất bại:** `output_paths(Path("/o"), "Ngay 20-10/Toan6/Bài 19_X/T48_Y")` == `(Path("/o/Toan6/Bài 19_X/T48_Y.mp4"), Path("/o/Toan6/Bài 19_X/T48_Y/vi.vtt"))`; `output_paths(Path("/o"), "test gen/T48_Y")` == `(Path("/o/test gen/T48_Y.mp4"), Path("/o/test gen/T48_Y/vi.vtt"))`; 4 thông điệp lỗi; `~/x` (monkeypatch `HOME` = tmp) → `tmp/x` tồn tại (Review Focus 2); đường dẫn có dấu + dấu cách (Review Focus 1); `publish_output_files` hai lần thay nội dung, file `T48_Y/quiz.json` có sẵn vẫn còn (Review Focus 3), không còn file `.tmp-*`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS + toàn bộ suite.
- [ ] **Step 5:** log commit message `feat: resolve and lay out user output folders`

---

### Task 2: Trạng thái job + scheduler

**Files:** `video_workflow/web/state.py`, `video_workflow/web/scheduler.py`, `video_workflow/web/runner.py` (chỉ `RunResult`); tests `tests/test_web_state.py`, `tests/test_web_scheduler.py`.

**Interfaces:**
- `WebJob` thêm `output_root: str | None = None`, `vtt_path: str | None = None` (to_dict/from_dict; thiếu khoá → None). `WebJob.new(..., output_root: str | None = None)`.
- `RunResult` thêm `vtt_path: Path | None = None`; scheduler lưu `vtt_path` khi COMPLETED.
- `JobScheduler.enqueue(batch_id, leaves, profile_id, output_root: str | None = None)`; khi `output_root` có giá trị: tính `output_paths` cho từng leaf; trùng nhau trong cùng lần gọi hoặc trùng với job WAITING/RUNNING khác → `DuplicateJobError(f"Trùng vị trí lưu với bài {relpath_khác}")`, không thêm job nào. `retry` giữ nguyên `output_root`.
- `WebStore.last_output_root() -> str | None`, `WebStore.set_last_output_root(path: str) -> None` (lưu trong `web.json` cùng `default_voice`, không làm mất khoá kia).

- [ ] **Step 1: Test thất bại:** round-trip `WebJob` có/không có 2 trường mới (record cũ thiếu khoá vẫn đọc được); enqueue 2 leaf `A/Toan6/B/T1`, `C/Toan6/B/T1` cùng output_root → `DuplicateJobError` đúng chuỗi, 0 job (Review Focus 4); job khác output_root thì không trùng; `set_last_output_root` không xoá `default_voice`.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS + toàn bộ suite.
- [ ] **Step 5:** log `feat: store output folder per job and block clashing outputs`

---

### Task 3: Runner ghi vào thư mục kết quả

**Files:** `video_workflow/web/runner.py`; test `tests/test_web_runner.py`.

- `run_web_job`: nếu `job.output_root` → `video_dest, vtt_dest = output_paths(Path(job.output_root), job.leaf_relpath)`; `publish_output_files(Path(report.output), vtt_text, video_dest, vtt_dest)`; `RunResult(video_path=video_dest, vtt_path=vtt_dest, ...)`. Nếu `None` → giữ nguyên `publish_leaf_output` (job cũ); `vtt_path = video_path.with_name("vi.vtt")`.
- Không tạo `<leaf>/output/` cho job có `output_root`.

- [ ] **Step 1: Test thất bại:** job có `output_root=tmp/out`, leaf `Ngay/Toan6/Bài 1_X/T1_Y` → tồn tại `tmp/out/Toan6/Bài 1_X/T1_Y.mp4` và `tmp/out/Toan6/Bài 1_X/T1_Y/vi.vtt`, **không** có `<leaf>/output`; job `output_root=None` → hành vi cũ (Review Focus 5).
- [ ] **Step 2–4:** FAIL → cài đặt → PASS + toàn bộ suite.
- [ ] **Step 5:** log `feat: write lesson outputs to the chosen output folder`

---

### Task 4: Upload service + API

**Files:** `video_workflow/web/upload_service.py`, `video_workflow/web/app.py`, `video_workflow/web/config.py` (`build_status`); tests `tests/test_web_upload_service.py`, `tests/test_web_app.py`, `tests/test_web_config.py`.

- `UploadService.candidates`: ưu tiên `job.video_path` + `job.vtt_path` khi cả hai file tồn tại; nếu `job.vtt_path` là None → `find_output_files(<leaf>)` như cũ.
- `GET /api/jobs/{id}/subtitles`: dùng `job.vtt_path` nếu có, ngược lại `Path(job.video_path).with_name("vi.vtt")`.
- `POST /api/batches/{id}/jobs`: body thêm `"output_root"` (bắt buộc với API này); `resolve_output_root(raw, services.layout.root)`; `OutputRootError` → 400 với thông điệp; thành công → `store.set_last_output_root(str(path))` rồi `enqueue(..., output_root=str(path))`.
- `build_status(..., last_output_root: str | None = None)` thêm khoá `"last_output_root"` (wire từ `WebStore` trong `__main__.py`).

- [ ] **Step 1: Test thất bại:** POST thiếu `output_root` → 400 `"Cần nhập thư mục lưu kết quả"`; POST với `layout.root/"uploads"/"x"` → 400 thư mục nội bộ; POST hợp lệ → job có `output_root`, `/api/status` trả `last_output_root`; candidates dùng `vtt_path` mới; subtitles route trả đúng file mới.
- [ ] **Step 2–4:** FAIL → cài đặt → PASS + toàn bộ suite.
- [ ] **Step 5:** log `feat: accept output folder in job API and upload from it`

---

### Task 5: Giao diện + README

**Files:** `video_workflow/web/static/index.html`, `app.js`, `styles.css`, `README.md`; test `tests/test_web_app.py`.

- Tab Tạo video, phía trên nút tạo: nhãn `Thư mục lưu kết quả (trên máy server)`, `<input id="output-root">` điền sẵn `status.last_output_root`, gợi ý `Ví dụ: /home/<tên>/Videos/bai-giang — video và phụ đề được xếp sẵn để upload lên R2`. Rỗng → khoá nút tạo. Gửi `output_root` trong POST; lỗi 400 hiện toast với `detail`.
- Hàng đợi: bài xong có dòng `Lưu tại: <video_path>`.
- README: mục thư mục kết quả, cây sắp xếp, cách chạy tay `SOURCE_DIR=<thư mục> CC_SOURCE_DIR=<thư mục> npm run dry-run` / `npm run dry-run:cc` trong `pyramid-upload-to-r2`.

- [ ] **Step 1: Test thất bại:** `index.html`/`app.js` chứa `Thư mục lưu kết quả (trên máy server)`, `output_root`, `Lưu tại:`.
- [ ] **Step 2–4:** FAIL → cài đặt → `node --check video_workflow/web/static/app.js` + PASS toàn bộ.
- [ ] **Step 5:** log `feat: ask for the output folder before creating videos`

---

### Task 6: Nghiệm thu (máy này)

- [ ] **Step 1:** Toàn bộ test pass.
- [ ] **Step 2:** **Không đụng** server cổng 8080 và VoiceStudio 3900 của chủ dự án; không tạo job trên server 8080. Kiểm tra `curl http://127.0.0.1:8080/api/jobs` — nếu có job `running`/`waiting` thì **dừng nghiệm thu bước 4–5 và báo lại** (GPU đang bận).
- [ ] **Step 3:** Chạy server nghiệm thu: `PYRAMID_UPLOAD_REPO=/home/meconlonton/work/pyramid-upload-to-r2 PYTHONPATH=<worktree> /home/meconlonton/work/auto-video/.venv/bin/python -m video_workflow.web --port 8090 --root /tmp/out-accept/root`.
- [ ] **Step 4:** Dựng bài mẫu nhỏ bằng `python-pptx`: `Ngay 20-10/Toan6/Bài 1_Kiểm tra/T1_Thư mục kết quả/` gồm PPTX 2 slide + kịch bản `slide1: Xin chào các em.` / `slide2: Hôm nay chúng ta kiểm tra thư mục kết quả.`. Upload qua trang (Chrome headless `/usr/bin/google-chrome` được), nhập thư mục kết quả `/tmp/out-accept/Kết quả ngày 20`, tạo video với giọng mặc định, chờ xong.
- [ ] **Step 5:** Kiểm tra có `/tmp/out-accept/Kết quả ngày 20/Toan6/Bài 1_Kiểm tra/T1_Thư mục kết quả.mp4` và `.../T1_Thư mục kết quả/vi.vtt`, không có `<leaf>/output`; tải MP4 và xem phụ đề qua API được. Chạy tay trong repo upload: `SOURCE_DIR="/tmp/out-accept/Kết quả ngày 20" OUTPUT_DIR=/tmp/out-accept/manual node scripts/local-bulk-upload.mjs --dry-run` và `CC_SOURCE_DIR="/tmp/out-accept/Kết quả ngày 20" OUTPUT_DIR=/tmp/out-accept/manual node scripts/local-cc-upload.mjs --dry-run` → mỗi script nhận đúng 1 file, không lỗi. Bấm **Kiểm tra upload** trên trang → `succeeded` 1/1. **Không** upload thật.
- [ ] **Step 6:** Tắt server 8090. Ghi `docs/superpowers/plans/2026-10-20-output-root-acceptance.md` (chỉ kết quả thật), log message `docs: record output folder acceptance`.
