# Nghiệm thu thư mục kết quả do người dùng nhập — 2026-10-20

Máy: Linux, RTX 3050 6 GB. Nhánh `feature/output-root`. Code do Codex (gpt-5.6-sol) viết theo
`docs/superpowers/plans/2026-10-20-output-root.md`; bước 3–5 do Claude chạy (phiên Codex dừng đúng quy tắc
"không tạo job trên VoiceStudio 3900" trong prompt, quy tắc này mâu thuẫn với bước nghiệm thu).

## Test tự động

- `pytest -q`: **324 passed** (1 cảnh báo deprecation sẵn có của Starlette/TestClient).
- Mỗi commit `c04a9e2`, `b03b463`, `753bdb8`, `c1a7daa`, `da89f8c` được checkout riêng và chạy test: 311 → 316 → 317 → 323 → 324 passed.

## Nghiệm thu đầu-cuối

- Server chủ dự án cổng 8080 không có job `running`/`waiting` trước khi chạy; không bị dừng hay tạo job.
- Server nghiệm thu: cổng 8090, `--root /tmp/out-accept/root`, `PYRAMID_UPLOAD_REPO=/home/meconlonton/work/pyramid-upload-to-r2`,
  dùng chung VoiceStudio 3900; đã dừng sau nghiệm thu.
- Bài mẫu (python-pptx, 2 slide, kịch bản `slide1:`/`slide2:`) upload qua API với đường dẫn
  `Ngay 20-10/Toan6/Bài 1_Kiểm tra/T1_Thư mục kết quả` → hợp lệ, 2 slide, `upload_issue = null`.
- Tạo job với `output_root = /tmp/out-accept/root/uploads/x` → HTTP 400 `Không được lưu vào thư mục nội bộ của tool`.
- Tạo job với `output_root = /tmp/out-accept/Kết quả ngày 20` → job `completed`. Kết quả (folder bọc `Ngay 20-10` bị bỏ):
  - `/tmp/out-accept/Kết quả ngày 20/Toan6/Bài 1_Kiểm tra/T1_Thư mục kết quả.mp4` — H.264 1280×720 + AAC, 7,54 s
  - `/tmp/out-accept/Kết quả ngày 20/Toan6/Bài 1_Kiểm tra/T1_Thư mục kết quả/vi.vtt` — cue 1 `00:00:00.000 --> 00:00:01.110 Xin chào các em.`
  - Không có thư mục `output/` nào trong `uploads/<batch>`.
- API: tải video với `Range: bytes=0-99` → 206, 100 byte; `/api/jobs/{id}/subtitles` → 200.
- Chạy tay trong `pyramid-upload-to-r2`, **cùng một thư mục** cho cả hai script, `OUTPUT_DIR=/tmp/out-accept/manual`, `--dry-run`:
  - `SOURCE_DIR=<thư mục kết quả> node scripts/local-bulk-upload.mjs` → `[1/1]`, key `videos/courses/601-toan-hoc-lop-6/lessons/1-bai-1-kiem-tra/1-thu-muc-ket-qua/master.m3u8`.
  - `CC_SOURCE_DIR=<thư mục kết quả> node scripts/local-cc-upload.mjs` → `[1/1]`, key `…/1-thu-muc-ket-qua/subtitles/vi.vtt`.
- "Kiểm tra upload" qua API (`dry_run: true`) → `succeeded`, video 1/1, phụ đề 1/1, không lỗi; manifest nằm trong
  `/tmp/out-accept/root/exports/<batch>/<ts>/dry-run-output/`.
- Không có upload thật lên R2. Repo `pyramid-upload-to-r2` không thay đổi (dry-run ghi vào `/tmp`).
