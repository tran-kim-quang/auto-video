# Batch TTS Slide Video

Ứng dụng desktop tạo video bài giảng trực tiếp từ PowerPoint và kịch bản có
marker slide. Workflow không cần video nguồn, audio nguồn, timeline viết tay,
STT, OCR hoặc nhận diện hình ảnh.

## Đầu vào

Chọn một thư mục gốc. Ứng dụng quét đệ quy và ghép chính xác từng cặp cùng tên:

```text
lessons/
  chapter-1/
    bai-01.pptx
    bai-01.txt
    bai-02.pptx
    bai-02.txt
```

Hai global asset bắt buộc:

- một ảnh logo, áp dụng cho phần bài giảng;
- một video outro, nối đúng một lần sau slide cuối.

Kịch bản TXT dùng marker `Slide N:`; viết liền `slide1:` hoặc in hoa `SLIDE 1:`
cũng được chấp nhận. Kịch bản bắt đầu bằng `Slide 1:` hoặc `Slide 2:`; phần chữ
đứng trước marker đầu tiên (ví dụ tiêu đề) bị bỏ qua, không được đọc. Ví dụ:

```text
Slide 1: Mở đầu của slide 1.
slide2: Nội dung slide thứ hai.
Slide 3:
Nội dung có thể nằm trên nhiều dòng.
```

Số đoạn narration phải bằng đúng số slide và mọi đoạn phải có nội dung.

## VoiceStudio

Workflow sử dụng profile cố định `Teacher_male`, ID mặc định `599fc048`:

- engine `omnivoice`, ngôn ngữ `Vietnamese`;
- 32 bước, seed 42, tốc độ đọc 0.9;
- chunk tối đa 300 ký tự, post-processing bật, crossfade 50 ms;
- inference qua GPU; không tự hạ xuống 8 bước, CPU hoặc profile khác.

API mặc định là `http://127.0.0.1:3900`. Nếu API chưa chạy, ứng dụng tìm
VoiceStudio đã cài và tự khởi động backend với CUDA. Có thể đặt biến môi trường
`VOICESTUDIO_BACKEND` cho đường dẫn backend tùy chỉnh. Setup chỉ kiểm tra
VoiceStudio, không tự tải hoặc cài ứng dụng này.

Khi CUDA hết bộ nhớ, slide hiện tại được flush memory và retry đúng một lần với
cùng cấu hình 32 bước. Nếu vẫn lỗi, job thất bại rõ ràng và bài tiếp theo trong
queue vẫn có thể chạy.

## Output

Với `bai-01.pptx` + `bai-01.txt`:

```text
chapter-1/output/bai-01/
  bai-01.mp4
  report.json
  timeline.json
  combined.wav
  audio-manifest.json
  audio/
    slide_001.wav
    slide_002.wav
  slide-images/
    slide_001.png
    slide_002.png
```

Timeframe được cộng trực tiếp từ duration thực tế của từng WAV. Video cuối là
1280×720, 24 fps mặc định, H.264/AAC stereo. Logo chỉ nằm trên phần slide;
outro được chuẩn hóa và nối một lần.

WAV hợp lệ được cache theo text NFC, profile và toàn bộ inference config. Sửa
một slide chỉ sinh lại audio của slide đó. Job bị hủy sẽ dừng sau request
VoiceStudio đồng bộ hiện tại và giữ các WAV đã validate. Output cũ chỉ được thay
sau khi toàn bộ staging đã qua kiểm tra; lỗi hoặc hủy không làm mất output cũ.

## Cài đặt và chạy

### Ubuntu 24.04/26.04

Yêu cầu Python 3.12+, Tkinter, FFmpeg/ffprobe, LibreOffice Impress, Poppler và
VoiceStudio đã cài:

```bash
chmod +x setup.sh run-ui.sh
./setup.sh
./run-ui.sh
```

Chỉ kiểm tra dependency, không gọi `sudo` hoặc cài package:

```bash
./setup.sh --check-only
```

Linux dùng LibreOffice + Poppler để xuất ảnh slide. Cài đúng font của PPTX để
giảm sai khác bố cục.

### Windows 10/11

Yêu cầu Python 3.12+, Microsoft PowerPoint desktop đã kích hoạt, FFmpeg và
VoiceStudio đã cài:

```bat
setup.bat
run-ui.bat
```

Build bản portable:

```bat
build-windows.bat
```

Kết quả ở `dist\PyramidVideoWorkflow\PyramidVideoWorkflow.exe`. Máy chạy vẫn
cần PowerPoint, FFmpeg và VoiceStudio.

## Sử dụng giao diện

1. Chọn logo và outro trong **Global assets**, bấm **Save assets**.
2. Chọn thư mục gốc trong **Batch Folder**.
3. Bấm **Scan & Add All**.
4. Theo dõi stage và tiến độ slide trong **Queue**; có thể Retry, Cancel active
   hoặc mở thư mục output.

Queue chạy tuần tự để các bài không tranh GPU. Mỗi lần scan tạo record mới;
job cũ có signature đầu vào không còn khớp sẽ không được phép publish đè kết
quả mới. Thư mục `output`, thư mục ẩn và staging của ứng dụng không bị quét lại.

State, cache và log nằm trong `.workflow_data` (hoặc cạnh file EXE của bản
Windows portable). Job đang chạy khi đóng ứng dụng trở thành `interrupted`.

## Giao diện web (LAN)

Chạy `run-web.sh` trên Linux hoặc `run-web.bat` trên Windows, rồi mở
`http://localhost:8080`. Dữ liệu mặc định nằm trong `~/video_and_cc` với các
thư mục `uploads`, `work`, `tts-cache`, `state` và `logs`.

Mỗi folder lá phải chứa đúng một file `.pptx` và một file kịch bản `.txt`; hai
file không cần trùng tên. Folder `output`, folder ẩn và file rác hệ điều hành
được bỏ qua.

Trước khi tạo video, nhập **Thư mục lưu kết quả (trên máy server)**.
Thư mục được dùng lại cho lần sau. Kết quả lấy ba thành phần cuối
của đường dẫn folder lá và được xếp sẵn cho tool upload:

```text
<thư-mục-kết-quả>/
  Toan6/
    Bài 19_Hình thang cân/
      T48_Hình thang cân.mp4
      T48_Hình thang cân/
        vi.vtt
```

Job cũ tạo trước thay đổi này vẫn đọc kết quả từ `<folder-lá>/output/`.

Để máy khác trong LAN truy cập, mở cổng TCP 8080 trong Windows Firewall. Chạy
ứng dụng trong phiên người dùng đã đăng nhập để PowerPoint và VoiceStudio hoạt
động; nếu cần tự khởi động, dùng Task Scheduler với trigger **At log on**.
Không chạy ứng dụng như Windows service.

File ghi âm mẫu để nhân bản giọng được giới hạn mặc định ở 7 giây trên máy
6 GB VRAM. Có thể đổi bằng `./run-web.sh --max-reference-seconds N` theo dung
lượng card; mức VRAM đo được xấp xỉ `1,9 GB + 0,3 GB × số giây`. Nên dùng đoạn
3–7 giây và nhập đúng lời nói trong file; giới hạn này không áp dụng cho video
bài giảng.

### Upload lên R2

Mỗi bài cần nằm trong cây ba tầng
`<Mã môn>/<Bài N_Tên bài>/<T<số>_Tên tiết>`. Có thể bọc thêm folder
bên ngoài; hệ thống luôn dùng ba thành phần cuối. Bài không đúng
quy ước vẫn tạo video được nhưng sẽ hiện lý do chưa upload được.

Cấu hình repo upload bằng một trong hai cách:

```bash
export PYRAMID_UPLOAD_REPO=/duong/dan/pyramid-upload-to-r2
python -m video_workflow.web

# hoặc
python -m video_workflow.web --upload-repo /duong/dan/pyramid-upload-to-r2
```

Máy chủ cần có Node trên `PATH`; chạy `npm install` trong repo
`pyramid-upload-to-r2` và cấu hình file `.env` của repo đó. Trang web chỉ
kiểm tra file `.env` có tồn tại, không đọc hay hiển thị khoá R2.

Trong tab **Hàng đợi**, nút **Kiểm tra upload** chạy toàn bộ luồng
ở chế độ dry-run, không đẩy dữ liệu lên R2. Chỉ dùng **Upload lên
R2** sau khi lần kiểm tra thành công. Khi xong, giao diện hiển đường
dẫn tuyệt đối của manifest video, manifest phụ đề và log. Manifest
dry-run nằm trong `video_and_cc/exports/<batch-id>/<thời-gian>/dry-run-output/`;
manifest upload thật nằm trong thư mục output của repo upload.

Có thể kiểm tra thủ công ngay trong repo `pyramid-upload-to-r2` bằng cùng
thư mục kết quả cho video và phụ đề:

```bash
cd /duong/dan/pyramid-upload-to-r2
SOURCE_DIR="<thư mục>" npm run dry-run
CC_SOURCE_DIR="<thư mục>" npm run dry-run:cc
```

## Phát triển và kiểm tra

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m video_workflow
```
