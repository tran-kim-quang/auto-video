# Workflow dựng video slide

Tool cục bộ dựng video bài giảng từ ba file bắt buộc và hai asset tùy chọn:

- video mẫu dùng làm audio bài giảng;
- PPTX chứa hình slide;
- timeline TXT hoặc JSON chứa mốc chuyển slide;
- ảnh logo tùy chọn, đặt ở góc dưới bên phải phần bài giảng;
- video outro tùy chọn, được nối nguyên vẹn ở cuối.

Phần `Outro/blank` ở cuối timeline nguồn luôn bị loại bỏ. Nếu không chọn logo, phần bài giảng được dựng không overlay; nếu không chọn outro, video kết thúc tại mốc cuối của slide. Outro được chọn sẽ co vừa vào khung ngang, có viền đen hai bên khi đầu vào là video dọc. Kết quả là MP4 1280×720, H.264/AAC, mặc định 24 fps.

## Cài đặt và mở giao diện

### Windows

Yêu cầu Windows 10/11, Python 3.12 trở lên và Microsoft PowerPoint. Chạy một lần:

```bat
setup.bat
```

Script tạo `.venv`, cài package Python, kiểm tra Tkinter/PowerPoint và tự cài FFmpeg bằng `winget` nếu máy chưa có. Sau đó mở ứng dụng:

```bat
run-ui.bat
```

Có thể chạy với console để xem lỗi khởi động:

```powershell
.venv\Scripts\python.exe -m video_workflow.ui
```

Để build bản portable cho Windows, chạy sau khi `setup.bat` hoàn tất:

```bat
build-windows.bat
```

Kết quả nằm tại `dist\PyramidVideoWorkflow\PyramidVideoWorkflow.exe`. Phân
phối nguyên thư mục `PyramidVideoWorkflow`; máy chạy vẫn cần Microsoft
PowerPoint và FFmpeg trên `PATH`. Queue và settings của bản đóng gói được lưu
trong `.workflow_data` cạnh file EXE.

### Ubuntu Linux

Hỗ trợ Ubuntu Desktop 24.04/26.04 LTS với Python 3.12 trở lên. Script cài Python/Tkinter, FFmpeg, LibreOffice Impress headless, Poppler (`pdftocairo`) và các font fallback Liberation/Noto:

```bash
chmod +x setup.sh run-ui.sh
./setup.sh
./run-ui.sh
```

Chỉ kiểm tra dependency mà không gọi `sudo`, không cài package và không tạo virtual environment:

```bash
./setup.sh --check-only
```

Có thể chạy UI với console để xem log khởi động:

```bash
.venv/bin/python -m video_workflow.ui
```

Trên Linux, LibreOffice chuyển PPTX thành PDF và Poppler tạo ảnh slide. LibreOffice có thể thay font hoặc bố cục so với Microsoft PowerPoint; hãy cài đúng font gốc của bài giảng nếu cần kết quả gần bản Windows. Với tài liệu cần độ trung thực PowerPoint tuyệt đối, nên dựng trên Windows bằng Microsoft PowerPoint.

Trong phần **Global assets**, logo và outro đều tùy chọn độc lập: có thể chọn một trong hai, cả hai hoặc để trống cả hai rồi bấm **Save assets**. Đường dẫn được lưu trong `.workflow_data/settings.json`. Mỗi job chọn video hoặc audio nguồn, PPTX, timeline TXT, tên output và thư mục output. Job đầu tiên tự chạy, các job sau chờ tuần tự.

### Batch folder theo cấu trúc slide

Tab **Batch folder** nhận một thư mục gốc rồi quét đệ quy các folder lá. Mỗi
PPTX được ghép với part 1 hoặc part 2 theo đúng tên gốc, ví dụ:

```text
Bài 1_Đơn thức/
  TOAN8_B1_T1.pptx
  TOAN8_B1_T1_1.mp4
  timeline_slide_TOAN8_B1_T1_1.txt
  TOAN8_B1_T1_2.wav
  timeline_slide_TOAN8_B1_T1_2.json
```

Media hỗ trợ các đuôi `.mp4`, `.mov`, `.mkv`, `.mp3`, `.wav`, `.m4a` và
`.aac`; timeline hỗ trợ `.txt` hoặc `.json`. Có part nào thì dựng part đó,
không bắt buộc phải có cả hai. Kết quả nằm ngay trong folder lá:

```text
Bài 1_Đơn thức/output/
  TOAN8_B1_T1_1.mp4
  TOAN8_B1_T1_2.mp4
```

Thư mục `output` không bị quét lại. Output đã tồn tại hoặc đã có trong queue
được bỏ qua và không bị ghi đè. Batch này không tạo file `*.report.json`; nếu
gặp report cũ đi kèm một output đã hoàn thành, ứng dụng xóa report đó. Bộ file
thiếu hoặc trùng media/timeline được báo theo từng folder, còn các bộ hợp lệ
khác vẫn được thêm vào queue tuần tự.

Tab **Merge 2 videos** tạo job ghép theo thứ tự **Video 1 → Video 2**. Hai video được chuẩn hóa độc lập về 1280×720, 24 fps, H.264/AAC trước khi ghép; video dọc được giữ trọn khung với viền đen và video không có audio được thêm silence. Logo và outro toàn cục không áp dụng cho loại job này.

Cả job dựng slide và job ghép video dùng chung một queue FIFO. Cột **Type** cho biết job là `slide` hay `merge`; các thao tác **Retry**, **Remove waiting** và **Open output folder** dùng chung cho cả hai loại.

Trình chọn file/thư mục tự quét lại nội dung đang hiển thị, còn các ô đường dẫn và cột **Files** trong queue cập nhật trạng thái filesystem mỗi giây. Nếu một job lỗi chỉ vì file đầu vào tạm thời biến mất, ứng dụng tự đưa job về cuối queue khi file xuất hiện lại đúng đường dẫn.

Queue và lịch sử nằm trong `.workflow_data/jobs.json`. Job đang chạy khi ứng dụng đóng sẽ thành `interrupted` và có thể **Retry**. Chi tiết lỗi nằm trong `.workflow_data/logs`. Ứng dụng chỉ lưu đường dẫn đầu vào; nếu file bị di chuyển, job sẽ lỗi và cần chọn lại đúng file. Output có sẵn không bị ghi đè. Job đơn lẻ và output ghép vẫn tạo `<output>.report.json`; riêng job được thêm từ tab **Batch folder** chỉ xuất MP4 cuối.

## Yêu cầu

- Windows 10/11: Microsoft PowerPoint, Python 3.12+; setup cài `pywin32` và FFmpeg.
- Ubuntu Desktop 24.04/26.04 LTS: Python 3.12+, Tkinter, FFmpeg, LibreOffice và Poppler.
- GUI cần desktop session có display; Ubuntu Server headless chỉ phù hợp để chạy CLI.

Cài package ở chế độ phát triển:

```powershell
python -m pip install -e ".[dev]"
```

## Chạy CLI

Ví dụ trên Windows PowerShell:

```powershell
python -m video_workflow `
  --video test/TOAN7_C4_B12_T36_2.mp4 `
  --pptx test/TOAN7_C4_B12_T36_2_fixed.pptx `
  --timeline test/timeline_slide_TOAN7_C4_B12_T36_2.txt `
  --logo test/logo.png `
  --outro test/outro.mp4 `
  --output test/final.mp4
```

Trên Ubuntu/Linux:

```bash
.venv/bin/python -m video_workflow \
  --video test/TOAN7_C4_B12_T36_2.mp4 \
  --pptx test/TOAN7_C4_B12_T36_2_fixed.pptx \
  --timeline test/timeline_slide_TOAN7_C4_B12_T36_2.txt \
  --logo test/logo.png \
  --outro test/outro.mp4 \
  --output test/final.mp4
```

Các tùy chọn:

- `--logo`: ảnh logo tùy chọn; bỏ cờ này để không ghép logo.
- `--outro`: video outro tùy chọn; bỏ cờ này để kết thúc ở mốc slide cuối.
- `--fps`: frame rate, mặc định `24`.
- `--logo-width-ratio`: chiều rộng logo so với 1280 px, mặc định `0.12`.
- `--margin-px`: khoảng cách logo tới mép phải và dưới, mặc định `0` để logo sát góc.

Tool không ghi đè output có sẵn. Khi thành công, file `<output>.report.json` chứa đường dẫn đầu vào (asset bị bỏ qua có giá trị `null`), mốc cắt audio nguồn, số slide, thời lượng outro (`0` nếu không dùng), thời lượng mong đợi và các kiểm tra đầu ra.

## Batch hai phần tự nhận diện timeline

Trên Ubuntu, lệnh batch mặc định quét đệ quy các thư mục `T<number>` trong
`/home/meconlonton/Documents/gen_video` và bỏ qua mọi thư mục `output`:

```bash
.venv/bin/build-video-batch
```

Mỗi thư mục cần đúng bốn file theo tên của bài. Hậu tố lớp có thể là `V8`,
`V12` hoặc một số lớp khác; ví dụ với `T8` lớp 8:

```text
P1_T8_V8.mp4
P2_T8_V8.mp4
T8_1_Slide.pptx
T8_2_Slide.pptx
```

Logo `logo.png` và outro `Outro720.mp4` mặc định được đọc từ
`/home/meconlonton/work/auto-video/test/logo_and_outro`. Có thể đổi hai thư
mục đầu vào mà không có tùy chọn ghi đè:

```bash
.venv/bin/build-video-batch \
  --source-root /duong/dan/gen_video \
  --assets-dir /duong/dan/logo_and_outro
```

Workflow ghép hai deck thành 40 slide, tự so khớp khung hình video với ảnh
slide để sinh timeline, dựng P1 bằng slide 1–20 với logo, dựng P2 bằng slide
21–40 với logo và outro, rồi nối hai phần. Kết quả của mỗi bài nằm trong
`Tn/output`:

```text
Tn_Slide.pptx
slide-images/
P1_Tn_timeline.json
P2_Tn_timeline.json
alignment-report.json
P1_Tn_slide.mp4
P2_Tn_slide.mp4
Tn_final.mp4
```

Nếu `Tn_final.mp4` đã tồn tại và qua kiểm tra media thì cả bài được bỏ qua.
Nếu lần chạy trước dừng giữa chừng, các PPTX, timeline và video trung gian hợp
lệ được tái sử dụng. Lỗi input hoặc độ tin cậy nhận diện thấp được in riêng
cho từng bài; các bài còn lại vẫn tiếp tục chạy. Lệnh trả mã `1` nếu có ít
nhất một bài lỗi.

## Timeline

TXT:

```text
Slide 01: 00:00.000 --> 00:14.583
Slide 02: 00:14.583 --> 00:30.375
Outro/blank: 00:30.375 --> 00:34.000
```

JSON:

```json
[
  {"slide_id": 1, "start_ms": 0, "end_ms": 14583, "duration_ms": 14583},
  {"slide_id": 2, "start_ms": 14583, "end_ms": 30375, "duration_ms": 15792}
]
```

`slide_id` ánh xạ trực tiếp tới số slide trong PPTX. Timeline phải bắt đầu ở 0, có thời lượng dương và không được hở hoặc chồng quá 20 ms.

## Kiểm tra

```powershell
python -m pytest tests -q
```
