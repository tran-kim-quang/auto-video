# Nghiệm thu dry-run upload R2 — 2026-10-20

## Test tự động

- Auto-video: `302 passed`, có 1 cảnh báo deprecation sẵn có từ FastAPI TestClient.
- Pyramid upload: `node --test tests/local-bulk-upload-env.test.mjs` đạt `2 passed`.

## Upload folder qua giao diện

- Server nghiệm thu ban đầu chạy với root `/tmp/r2-accept` trên cổng 8090.
- Chrome headless chọn folder qua đúng input folder của trang web.
- Batch `20261020-180740-e955` có leaf
  `Toan6/Bài 19_Hình chữ nhật, hình thoi, hình bình hành và hình thang cân/T48_Hình thang cân`;
  leaf hợp lệ, 40 slide, 28.553 ký tự và `upload_issue` là `null`.
- Batch `20261020-180740-9cac` có leaf `test gen/T48_Hình thang cân`;
  trang hiển đúng cảnh báo
  `Thiếu tầng Mã môn/Bài: cần dạng <Mã môn>/<Bài N_Tên bài>/<T<tiết>_Tên tiết>`.

## Job hoàn thành giả lập

Để không dùng GPU, đã copy output có sẵn `TOAN6_B19_T48.mp4` và
`vi.vtt` từ batch cũ, đổi tên video thành `T48_Hình thang cân.mp4`, rồi
tạo một record job `completed` trong state nghiệm thu. Không tạo video mới.

## Kiểm tra upload

- Nút `Kiểm tra upload` trên trang được bật và được bấm; không bấm
  `Upload lên R2`.
- Lượt `135353b8b45a405a893d13b4f16cd302` kết thúc `succeeded`:
  video `1/1`, phụ đề `1/1`, không có lỗi.
- Giao diện hiển `(kiểm tra — KHÔNG import)` và hai đường dẫn:
  - Video manifest: `/tmp/r2-accept/exports/20261020-180740-e955/20261020-181154/dry-run-output/manifests/bulk-upload-2026-10-20T11-11-54-964Z.json`
  - Phụ đề manifest: `/tmp/r2-accept/exports/20261020-180740-e955/20261020-181154/dry-run-output/cc-manifests/cc-upload-2026-10-20T11-11-55-041Z.json`
- Chạy tay `local-bulk-upload.mjs --dry-run` trên cùng cây export cho kết quả
  giống manifest của giao diện:
  - `videoSlug`: `48-hinh-thang-can`
  - `lessonSlug`: `19-bai-19-hinh-chu-nhat-hinh-thoi-hinh-binh-hanh-va-hinh-thang-can`

Không có upload thật lên R2 trong lần nghiệm thu này.

Sau vòng sửa code review, đã chạy lại luồng Python với chính script upload thật
ở chế độ `dry_run=True` trên output mẫu: kết quả `succeeded`, video `1/1`, phụ đề
`1/1`, và cả hai file manifest đều tồn tại. Không chạy lại upload thật.

## Trạng thái server nghiệm thu

- Server được khởi động trong interactive shell trên cổng 8092 để
  chạy nút dry-run, sau đó dừng đúng PID; cổng 8092 đã đóng.
- Hai lần khởi động foreground trước đó trên cổng 8090 và 8091 bị
  harness làm mất session quản lý sau khi gửi Ctrl-C, nhưng tiến trình con vẫn
  lắng nghe. PID của hai tiến trình này nằm ngoài PID namespace hiện tại nên
  không thể dừng đích danh; không dùng lệnh kill rộng vì có nguy cơ đụng
  server của chủ dự án trên 8080 hoặc VoiceStudio trên 3900.

## Dọn dẹp sau nghiệm thu (bổ sung khi review)

- Hai server nghiệm thu còn sót trên cổng 8090 (PID 3256588) và 8091 (PID 3257829) — đều chạy từ worktree
  `auto-video-r2` với `--root /tmp/r2-accept`, không có tiến trình con — đã được dừng bằng SIGTERM đích danh.
  Server của chủ dự án trên 8080 và VoiceStudio trên 3900 vẫn chạy bình thường.
- Thư mục manifest thật `pyramid-upload-to-r2/output/manifests` và `output/cc-manifests` không có file mới từ lần nghiệm thu.
