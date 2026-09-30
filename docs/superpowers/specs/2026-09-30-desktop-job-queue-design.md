# Thiết kế ứng dụng desktop quản lý queue dựng video

Ngày: 2026-09-30

## Mục tiêu

Mở rộng workflow dựng video hiện có thành một ứng dụng desktop đơn giản chạy cục bộ trên Windows có Microsoft PowerPoint. Người dùng clone repository, chạy script cài đặt, mở UI, cấu hình logo và outro dùng chung một lần, rồi thêm liên tục các job vào queue. Queue xử lý tuần tự và tự bắt đầu ngay khi có job đầu tiên.

Ứng dụng không có web server, tài khoản người dùng, upload qua mạng hoặc dịch vụ lưu trữ. Các file đầu vào vẫn nằm ở vị trí do người dùng chọn; ứng dụng chỉ lưu đường dẫn.

## Phạm vi

Mỗi job gồm:

- một file video hoặc audio làm nguồn audio;
- một PPTX chỉ chứa ảnh slide;
- một TXT chứa timeline;
- tên file output;
- thư mục chứa output.

Logo và outro là cấu hình global, được chọn một lần và lưu lại giữa các lần mở ứng dụng. Kết quả luôn là MP4 1280x720 theo workflow hiện có. Overscan slide, logo sát góc dưới phải, cắt phần `Outro/blank` của nguồn và giữ trọn outro riêng tiếp tục áp dụng.

Không nằm trong phạm vi đầu tiên:

- xử lý nhiều job song song;
- copy file đầu vào vào vùng dữ liệu của ứng dụng;
- đồng bộ cloud hoặc NAS;
- tài khoản và phân quyền;
- kéo thả để đổi thứ tự queue;
- phần trăm tiến độ theo từng frame.

## Công nghệ

- Python 3.12 trở lên;
- Tkinter cho UI;
- JSON cho cấu hình và queue;
- một worker thread xử lý tuần tự;
- PowerPoint COM qua pywin32;
- FFmpeg và ffprobe;
- pipeline render hiện có trong `video_workflow`.

Tkinter và JSON giữ ứng dụng nhẹ, dễ triển khai nội bộ. Công việc dài không chạy trên Tkinter main thread. Mọi thay đổi widget được đưa về main thread qua một event queue an toàn giữa các thread.

## Bố cục giao diện

### Cấu hình global

- Đường dẫn logo và nút **Chọn logo**.
- Đường dẫn outro và nút **Chọn outro**.
- Thay đổi được kiểm tra rồi lưu ngay.
- Nếu một file global không còn tồn tại, UI hiển thị cảnh báo và tạm dừng việc lấy job mới cho tới khi người dùng chọn lại.

### Form thêm job

- Video hoặc audio mẫu và nút chọn file.
- PPTX và nút chọn file.
- Timeline TXT và nút chọn file.
- Tên output.
- Thư mục output và nút chọn thư mục.
- Nút **Thêm vào queue**.

Tên output chỉ là tên file, không chứa đường dẫn. Ứng dụng tự thêm `.mp4`. Tên có ký tự cấm của Windows, tên thiết bị dành riêng, dấu chấm hoặc khoảng trắng ở cuối bị từ chối. Nếu output đã tồn tại, job không được thêm.

File picker hiển thị các định dạng media phổ biến như MP4, MOV, MP3, WAV, M4A và AAC, đồng thời có lựa chọn mọi file. `ffprobe` là nguồn xác thực cuối cùng thay vì phần mở rộng.

### Danh sách queue

Các cột chính:

- tên output;
- media nguồn;
- trạng thái;
- giai đoạn hiện tại;
- đường dẫn kết quả hoặc lỗi ngắn.

Hành động:

- **Chạy lại** cho job `failed` hoặc `interrupted`;
- **Xóa** cho job chưa chạy;
- **Mở thư mục output** cho job có thư mục hợp lệ.

Job đầu tiên tự chạy ngay. Các job thêm sau nằm ở `waiting` và được xử lý theo thứ tự tạo.

## Mô hình dữ liệu

Dữ liệu cục bộ nằm trong thư mục repository:

```text
.workflow_data/
├── settings.json
├── jobs.json
└── logs/
    └── <job-id>.log
```

`settings.json` có version schema và hai đường dẫn global:

```json
{
  "schema_version": 1,
  "logo": "D:\\assets\\logo.png",
  "outro": "D:\\assets\\outro.mp4"
}
```

Mỗi bản ghi trong `jobs.json` gồm ít nhất:

```json
{
  "id": "uuid",
  "created_at": "2026-09-30T08:30:00+07:00",
  "source_media": "D:\\inputs\\audio.mp3",
  "pptx": "D:\\inputs\\slides.pptx",
  "timeline": "D:\\inputs\\timeline.txt",
  "output_name": "bai-giang-12",
  "output_directory": "D:\\videos",
  "status": "waiting",
  "stage": null,
  "error": null,
  "started_at": null,
  "finished_at": null
}
```

Các trạng thái hợp lệ là `waiting`, `running`, `completed`, `failed` và `interrupted`. Các giai đoạn khi chạy là `validating`, `exporting_slides`, `rendering_lecture`, `preparing_outro`, `joining` và `verifying`.

Mọi cập nhật JSON được bảo vệ bằng lock và ghi nguyên tử: ghi file tạm trong cùng thư mục, flush, rồi `os.replace`. Nếu JSON không đọc được, ứng dụng giữ bản lỗi với hậu tố thời gian, mở queue rỗng và hiển thị cảnh báo thay vì âm thầm ghi đè dữ liệu hỏng.

## Vòng đời queue

Khi khởi động:

1. Đọc settings và queue.
2. Chuyển mọi job `running` còn sót lại thành `interrupted`.
3. Hiển thị lịch sử `completed`, `failed` và `interrupted`.
4. Nếu global assets hợp lệ, tự lấy job `waiting` đầu tiên.

Một job lỗi không chặn job tiếp theo. Trường hợp thiếu logo hoặc outro là lỗi cấu hình chung nên queue tạm dừng thay vì đánh dấu hàng loạt job là `failed`.

Khi chạy lại job, ứng dụng kiểm tra output chưa tồn tại và tất cả đường dẫn đầu vào còn hợp lệ, xóa lỗi cũ rồi đưa job về cuối danh sách `waiting`.

Ứng dụng dùng đường dẫn logo và outro mới nhất tại thời điểm job bắt đầu. Việc thay đổi global assets không sửa job đã hoàn thành.

## Worker thread và hủy xử lý

Chỉ một worker thread tồn tại. Worker lấy job `waiting`, cập nhật `running`, gọi pipeline và phát sự kiện trạng thái về UI. PowerPoint COM được khởi tạo và hủy trong chính worker thread bằng `CoInitialize`/`CoUninitialize`.

Pipeline nhận thêm:

- callback báo giai đoạn;
- cancellation event;
- file log của job.

FFmpeg được chạy bằng `Popen` để worker có thể kiểm tra cancellation event. Khi đóng ứng dụng, UI:

1. đánh dấu job hiện tại là `interrupted` và ghi JSON;
2. bật cancellation event;
3. dừng FFmpeg đang chạy, chờ ngắn rồi kill nếu cần;
4. yêu cầu PowerPoint dừng ở ranh giới slide gần nhất, đóng presentation và thoát COM;
5. đóng cửa sổ sau khi worker đã dọn tài nguyên.

Output luôn được tạo trong staging cạnh thư mục đích và chỉ publish sau khi xác minh. Hủy hoặc lỗi không để lại MP4 hoàn chỉnh giả. Các file staging của job bị xóa khi worker kết thúc.

## Hỗ trợ video và audio nguồn

Tên khái niệm trong pipeline đổi từ `video` sang `source_media`, nhưng CLI cũ tiếp tục được hỗ trợ để không phá các lệnh hiện có. Nguồn hợp lệ chỉ cần có audio stream đủ dài so với timeline; không yêu cầu video stream.

Audio tổng được giữ liên tục từ 0 đến mốc kết thúc slide cuối. Không cắt thành file theo slide. Khi nguồn là video, workflow chỉ đọc audio stream. Vì vậy audio trực tiếp chỉ giảm một phần nhỏ chi phí đọc container; thời gian chính vẫn nằm ở PowerPoint export và H.264 encoding.

## Báo lỗi và log

- File job bị di chuyển: job `failed`, lỗi ghi rõ đường dẫn thiếu.
- Source không có audio hoặc audio quá ngắn: job `failed` trước khi export slide.
- Timeline/PPTX không hợp lệ: giữ thông báo validation hiện có.
- PowerPoint hoặc FFmpeg lỗi: thông báo ngắn trong UI, chi tiết trong log riêng của job.
- Output đã tồn tại: từ chối trước khi render.
- Global asset bị mất: cảnh báo ở vùng global và tạm dừng queue.

Log không chứa nội dung file; chỉ ghi giai đoạn, lệnh công cụ đã được cấu trúc an toàn, thời gian và stderr cần thiết để chẩn đoán.

## Cài đặt và khởi chạy

Repository cung cấp `setup.bat` và `run-ui.bat`.

`setup.bat`:

1. tìm Python 3.12 trở lên;
2. tạo `.venv` nếu chưa có;
3. nâng cấp pip và cài package runtime bằng `pip install -e .`;
4. kiểm tra Tkinter và pywin32;
5. kiểm tra `ffmpeg` và `ffprobe` trên PATH;
6. nếu thiếu FFmpeg, chạy `winget install --id Gyan.FFmpeg --exact` với các cờ chấp nhận thỏa thuận;
7. kiểm tra lại FFmpeg và hướng dẫn mở terminal mới nếu PATH chưa cập nhật;
8. kiểm tra có thể tạo PowerPoint COM instance, rồi đóng đúng instance đó.

Script không tự cài Microsoft Office hoặc PowerPoint vì đây là phần mềm có giấy phép. Nếu thiếu PowerPoint, script dừng với hướng dẫn rõ ràng.

`run-ui.bat` kiểm tra `.venv`, nếu thiếu thì yêu cầu chạy `setup.bat`, sau đó khởi động:

```powershell
.venv\Scripts\pythonw.exe -m video_workflow.ui
```

Lệnh chẩn đoán vẫn có thể dùng console:

```powershell
.venv\Scripts\python.exe -m video_workflow.ui
```

## Ranh giới mã nguồn

- `video_workflow/ui.py`: widget, dialog và event loop Tkinter.
- `video_workflow/job_models.py`: model, enum trạng thái và validation tên output.
- `video_workflow/json_store.py`: settings/jobs JSON, lock và atomic write.
- `video_workflow/queue_controller.py`: chuyển trạng thái, tự chạy và retry/remove.
- `video_workflow/worker.py`: worker thread, cancellation và phát sự kiện.
- `video_workflow/pipeline.py`: nhận source audio/video, callback giai đoạn và cancellation.
- `video_workflow/compose.py`: FFmpeg cancellable và log theo job.
- `setup.bat`, `run-ui.bat`: cài đặt và khởi chạy Windows.

UI không gọi trực tiếp PowerPoint hoặc FFmpeg. Store không biết widget. Worker không thay đổi widget. Các ranh giới này cho phép kiểm thử queue mà không cần mở PowerPoint hoặc cửa sổ thật.

## Kiểm thử và tiêu chí hoàn thành

### Tự động

- round trip và atomic write cho settings/jobs JSON;
- chuyển `running` thành `interrupted` khi khởi động;
- queue chạy tuần tự và tự lấy job tiếp theo;
- một job lỗi không chặn job sau;
- retry, xóa job chờ và output trùng;
- mất global asset tạm dừng rồi tự tiếp tục sau khi sửa;
- đóng ứng dụng gửi cancellation và lưu `interrupted`;
- pipeline nhận MP3/WAV/M4A và MP4 có audio;
- source thiếu audio hoặc quá ngắn bị từ chối;
- toàn bộ test renderer và timeline hiện có tiếp tục pass.

### Tích hợp Windows

- chạy `setup.bat` trên môi trường có và chưa có FFmpeg;
- mở UI bằng `run-ui.bat`;
- lưu logo/outro, đóng rồi mở lại và thấy cấu hình cũ;
- thêm nhiều job trong khi job đầu đang chạy;
- xác nhận chỉ một job `running`;
- đóng ứng dụng giữa lúc FFmpeg chạy, mở lại thấy `interrupted`, rồi chạy lại thành công;
- chạy một job nguồn audio và một job nguồn video bằng fixture thật;
- xác nhận output 720p, logo sát góc, không có viền trắng slide, cắt tail và giữ trọn outro.

Ứng dụng hoàn thành khi người dùng mới có thể clone repository, chạy `setup.bat`, mở `run-ui.bat`, cấu hình hai global assets, thêm liên tục các job audio/video và nhận các MP4 hợp lệ tại thư mục đã chọn mà không cần dùng dòng lệnh.
