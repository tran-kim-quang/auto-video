# Nghiệm thu production sync với backend giả

Ngày chạy: 2026-10-21  
Worktree: `/home/meconlonton/work/auto-video-sync`  
Batch: `acceptance-batch`

## Test tự động

Lệnh:

```bash
PYTHONPATH=/home/meconlonton/work/auto-video-sync /home/meconlonton/work/auto-video/.venv/bin/python -m pytest -q
```

Kết quả thật sau vòng sửa code review: **363 passed**, 1 cảnh báo
`StarletteDeprecationWarning`, không có
test thất bại.

Vòng sửa review bổ sung test hồi quy cho trùng đích sau khi khớp production,
job cùng đường dẫn ở batch khác, trạng thái trong lúc chuẩn bị import, thứ tự
manifest của video nhiều phần, tiêu đề Unicode tương đương và token có ký tự
điều khiển.

## Thiết lập nghiệm thu

- Backend giả: `http://127.0.0.1:8091/api/v1`, token
  `fake-admin-token`.
- Web tool: `http://127.0.0.1:8090`, root
  `/tmp/sync-accept/root`, repo upload chỉ đọc tại
  `/home/meconlonton/work/pyramid-upload-to-r2`.
- Bốn job `COMPLETED` dùng MP4/VTT sao chép từ
  `/home/meconlonton/gen_video`: một video sẵn sàng, một video đã tồn tại để
  kiểm tra ghi đè, một bài có mô tả để kiểm tra chặn, và một bài không có trên
  production giả.
- Chỉ gọi API dry-run `{"dry_run": true, "admin_token": "fake-admin-token"}`.
  Không gọi production thật, không chạy upload R2 thật và không chạy luồng
  import thật.

## Kết quả API thực tế

Response khởi động trả:

- `production_checked: true`;
- `video_total: 2`: hai bài/tiết tìm thấy và đủ điều kiện dry-run;
- `skipped_not_on_production`: một mục
  `Toan6/Bài 99_Bài chưa có/T99_Bài chưa có`;
- `blocked`: một mục
  `Toan6/Bài 20_Chu vi và diện tích của một số tứ giác đã học/T49_Chu vi và diện tích hình chữ nhật`
  với lý do `Bài có mô tả trên production — import sẽ xoá mô tả`;
- `overwrite`: một mục
  `Toan6/Bài 19_Hình chữ nhật. Hình thoi. Hình bình hành. Hình thang cân/T48_Hình thang cân`.

Kết quả cuối:

- `status: succeeded`;
- video `2/2`, phụ đề `2/2`;
- manifest video và phụ đề đều nằm trong
  `/tmp/sync-accept/root/exports/acceptance-batch/20261021-004546/dry-run-output/`;
- `import_manifest`, `import_job_id`, `cc_import_result` và `backup_path` đều
  `null`, đúng với dry-run;
- file MP4 và VTT của bài chưa có trên production vẫn ở vị trí ban đầu, không
  bị di chuyển.

Log dry-run ghi rõ `khong transcode, khong upload R2` cho video và
`khong upload R2` cho phụ đề. Không thấy URL production hoặc lời gọi endpoint
import trong các log nghiệm thu.

## Dọn server

Hai server được khởi động với PID namespace-local đã ghi nhận: backend giả PID
`3`, web PID `4`. Lệnh `kill 4` và `kill 3` từ shell theo sau không thể nhìn thấy
PID của namespace đã khởi động server (`No such process`). Sau khi session gốc
kết thúc, kiểm tra thật vẫn thấy cổng 8090 và 8091 đang lắng nghe. Vì vậy điều
kiện dọn server bằng đúng PID **chưa đạt** trong lần nghiệm thu này. Cổng 8080
và 3900 chỉ được quan sát, không bị dừng, khởi động lại hay thay đổi.

## Bổ sung khi review (Claude)

- Hai tiến trình nghiệm thu còn sót (server tool cổng 8090, PID 3617147; fake backend cổng 8091, PID 3617146 — đều không có tiến trình con) đã được dừng bằng SIGTERM đích danh. Server 8080 và VoiceStudio 3900 của chủ dự án không bị ảnh hưởng.
- Sửa lỗi review "polling làm mất focus ô token": `renderJobs` ghi nhớ ô token đang focus và vị trí con trỏ, dựng lại hàng đợi rồi trả focus. Kiểm tra trên Chrome headless thật (`/usr/bin/google-chrome`): gõ `abc-token-123` trong ~4,5 s qua nhiều lần làm mới 1,5 s → ô vẫn focus, giá trị đầy đủ, không lỗi JavaScript.
- Sửa nhãn dry-run: bài chưa có trên production hiện "Chưa có trên production (sẽ chuyển sang thư mục chờ khi upload thật)" thay vì "Chuyển sang thư mục chờ" với đường dẫn hiện tại.
- Chưa chạy với backend production thật; bước "Kiểm tra upload" với production thật sẽ do Claude chạy khi có token admin.
