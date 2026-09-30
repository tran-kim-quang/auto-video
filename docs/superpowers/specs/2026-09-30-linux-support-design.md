# Ubuntu Linux Support Design

## Mục tiêu

Bổ sung một phiên bản chạy đầy đủ trên Ubuntu Desktop mà không thay thế hoặc làm thay đổi luồng Windows hiện có. Người dùng Ubuntu 24.04 LTS hoặc 26.04 LTS có thể cài dependency bằng một script Bash, mở cùng giao diện Tkinter, xếp job như trên Windows và dựng video bằng pipeline hiện tại.

Windows tiếp tục dùng Microsoft PowerPoint qua COM. Linux dùng LibreOffice Impress ở chế độ headless để đọc PPTX, sau đó dùng Poppler để raster hóa đúng các slide cần thiết. Timeline, queue, FFmpeg, CLI, cấu hình và định dạng output vẫn dùng chung.

## Phạm vi hỗ trợ

- Ubuntu Desktop 24.04 LTS và 26.04 LTS.
- Python 3.12 trở lên.
- Giao diện Tkinter chạy trong desktop session có display.
- PPTX được xử lý như slide tĩnh; animation, transition, video nhúng và macro không được phát.
- Script Windows và backend PowerPoint COM hiện tại tiếp tục hoạt động như trước.

Không nằm trong phạm vi:

- Các distro không dùng `apt`.
- Chạy GUI trên Ubuntu Server không có display.
- Bảo đảm kết quả LibreOffice giống PowerPoint tuyệt đối ở mức pixel.
- Tự động cài font Microsoft có giấy phép hoặc xử lý hộp thoại EULA.
- Dùng PowerPoint trên máy Windows từ xa.

## Phương án được chọn

Backend Linux dùng hai chương trình hệ thống:

1. `libreoffice` hoặc `soffice` chuyển toàn bộ PPTX thành PDF trong thư mục tạm với một user profile riêng cho mỗi lần chạy.
2. `pdftocairo` từ `poppler-utils` raster hóa từng trang PDF được yêu cầu thành PNG 1280×720.

Phương án này được chọn thay cho Python UNO vì không buộc virtual environment phải nhìn thấy module UNO của Python hệ thống và dễ kiểm thử bằng process boundary. Nó cũng đơn giản hơn một dịch vụ PowerPoint từ xa và giữ workflow chạy hoàn toàn local.

LibreOffice có thể thay font hoặc bố cục nếu máy Linux thiếu font dùng trong PPTX. `setup.sh` cài Liberation và Noto để có fallback tốt, nhưng README phải nói rõ người dùng cần tự cài đúng font của bài giảng nếu muốn bố cục gần bản Windows nhất.

## Kiến trúc exporter

`video_workflow.slide_export` trở thành facade mà pipeline gọi. Module này giữ interface hiện tại:

```python
count_pptx_slides(pptx: Path) -> int

export_slides(
    pptx: Path,
    slide_ids: Collection[int],
    target_dir: Path,
    width: int = 1280,
    height: int = 720,
    cancel_event: threading.Event | None = None,
) -> dict[int, Path]
```

Facade thực hiện validation dùng chung rồi chọn backend theo `sys.platform`:

- `win32`: gọi backend PowerPoint COM hiện tại;
- `linux`: gọi backend LibreOffice/Poppler mới;
- nền tảng khác: báo lỗi rõ ràng rằng hệ điều hành chưa được hỗ trợ.

Implementation PowerPoint hiện tại được giữ trong `video_workflow.powerpoint`. Backend Linux nằm trong `video_workflow.libreoffice`. `pipeline.py` chỉ phụ thuộc facade và không chứa nhánh theo hệ điều hành.

`count_pptx_slides` tiếp tục đọc `ppt/presentation.xml` trực tiếp từ file PPTX, không mở PowerPoint hoặc LibreOffice. Facade kiểm tra toàn bộ slide ID trước khi khởi chạy chương trình ngoài để một ID không tồn tại không tạo ra output một phần.

## Luồng xuất slide trên Linux

1. Chuẩn hóa đường dẫn và slide ID; từ chối file không tồn tại, danh sách rỗng, kích thước không dương hoặc slide ID ngoài phạm vi.
2. Tìm `libreoffice` hoặc `soffice`, đồng thời tìm `pdftocairo` trên `PATH`. Nếu thiếu, lỗi nêu đúng package Ubuntu cần cài.
3. Tạo thư mục làm việc và LibreOffice user profile tạm. Profile được truyền bằng `-env:UserInstallation=<file-uri>` để tránh khóa hoặc làm thay đổi profile LibreOffice của người dùng.
4. Chạy LibreOffice headless để chuyển PPTX thành PDF. Mỗi argument được truyền dưới dạng danh sách với `shell=False`, nên đường dẫn có dấu, khoảng trắng và ký tự shell không bị diễn giải lại.
5. Kiểm tra PDF tồn tại và khác rỗng.
6. Với từng slide ID duy nhất theo thứ tự tăng dần, chạy `pdftocairo` cho đúng một trang và tạo `slide_NNN.png` ở kích thước yêu cầu.
7. Kiểm tra mỗi PNG tồn tại và khác rỗng rồi trả về mapping theo slide ID.

Mỗi process được chạy bằng `Popen` và poll định kỳ. Nếu `cancel_event` được set, backend terminate process, đợi một khoảng ngắn, kill nếu cần, rồi báo cancellation. Thư mục tạm của pipeline xóa PDF, profile và PNG chưa hoàn tất. Không có output video hoặc report được publish khi export lỗi hay bị hủy.

Stderr của LibreOffice hoặc Poppler được giữ lại và rút gọn trong `PowerPointExportError` hiện có để UI/worker ghi thông tin hữu ích vào job error. Tên exception được giữ để tránh phá API hiện tại, dù trên Linux thông báo sẽ nhắc LibreOffice thay vì PowerPoint.

## Worker đa nền tảng

`video_workflow.worker` không được import `pythoncom` vô điều kiện. Một context manager nhỏ thực hiện `CoInitialize`/`CoUninitialize` chỉ trên Windows và là no-op trên Linux. Vòng đời queue, trạng thái job, cancellation và event không thay đổi.

Nhờ vậy `python -m video_workflow.ui`, CLI và test suite có thể import trên Linux mà không cần `pywin32`. Marker dependency hiện tại trong `pyproject.toml` tiếp tục chỉ cài `pywin32` khi `sys_platform == 'win32'`.

## Cài đặt Ubuntu

Thêm `setup.sh` ở root repo. Script dùng `set -Eeuo pipefail`, luôn resolve repo root theo vị trí của chính script và hỗ trợ `--check-only`.

Ở chế độ cài đặt, script dùng `sudo apt-get` để cài:

- `python3`;
- `python3-venv`;
- `python3-tk`;
- `ffmpeg`;
- `libreoffice-impress-nogui`;
- `poppler-utils`;
- `fonts-liberation`;
- `fonts-noto-core`.

Sau đó script tạo `.venv` nếu chưa có, nâng cấp pip và chạy `pip install -e <repo-root>`. Script có tính idempotent: chạy lại không xóa virtual environment, dữ liệu queue hoặc output.

Ở chế độ `--check-only`, script không gọi `apt`, không tạo `.venv` và không thay đổi môi trường. Nó kiểm tra và in dòng PASS/FAIL riêng cho Ubuntu version, Python, Tkinter, FFmpeg, ffprobe, LibreOffice, pdftocairo và khả năng import package từ `.venv` nếu virtual environment đã tồn tại. Mọi lỗi kết thúc với exit code khác 0 và hướng dẫn sửa cụ thể.

Thêm `run-ui.sh` ở root repo. Script kiểm tra `.venv/bin/python`; nếu thiếu, yêu cầu chạy `./setup.sh`. Khi hợp lệ, script `exec` Python trong virtual environment với `-m video_workflow.ui` từ repo root để log khởi động vẫn hiển thị trong terminal.

Cả hai script được commit với executable bit. `setup.bat`, `run-ui.bat` và `scripts/setup.ps1` không bị thay đổi trừ khi README cần dẫn liên kết.

## README và trải nghiệm sử dụng

README tách hướng dẫn cài đặt thành Windows và Ubuntu:

```bash
chmod +x setup.sh run-ui.sh
./setup.sh
./run-ui.sh
```

README cũng mô tả:

- Ubuntu 24.04/26.04 LTS được hỗ trợ;
- dependency hệ thống mà `setup.sh` cài;
- `./setup.sh --check-only` chỉ kiểm tra;
- cách chạy UI có console bằng `.venv/bin/python -m video_workflow.ui`;
- LibreOffice có thể render font/layout khác PowerPoint và cách khắc phục là cài đúng font nguồn;
- Windows vẫn là lựa chọn cho độ trung thực PowerPoint cao nhất.

CLI giữ nguyên toàn bộ option và ví dụ Linux chỉ thay cú pháp nối dòng thành backslash.

## Kiểm thử

Các test mới tuân theo test-first và không cần LibreOffice thật cho phần unit:

- facade chọn PowerPoint trên Windows, LibreOffice trên Linux và từ chối nền tảng khác;
- Linux backend giữ nguyên Unicode/path có khoảng trắng và dùng argument list với `shell=False`;
- slide ID được deduplicate, sort và kiểm tra trước khi spawn process;
- thiếu LibreOffice hoặc pdftocairo cho lỗi dependency chính xác;
- lỗi process và output rỗng không trả về mapping;
- cancellation terminate process và không tiếp tục render slide sau;
- worker import/chạy trên Linux mà không có `pythoncom`, còn lifecycle COM trên Windows vẫn được gọi đúng;
- shell script vượt qua `bash -n`, có executable bit và `--check-only` không chạy lệnh thay đổi hệ thống.

Một integration test được đánh dấu để tự skip khi thiếu tool hệ thống. Khi LibreOffice và Poppler có sẵn, test dùng PPTX fixture thật, xuất một vài slide, xác nhận tên file/kích thước PNG và mapping slide. Toàn bộ test suite hiện có phải tiếp tục pass.

## Tiêu chí hoàn thành

- `./setup.sh` cài được dependency và virtual environment trên Ubuntu Desktop 24.04/26.04 LTS.
- `./run-ui.sh` mở được queue UI trên desktop session Ubuntu.
- Một job dùng fixture thật xuất được slide bằng LibreOffice, dựng MP4 và report qua pipeline hiện có.
- Hủy hoặc đóng ứng dụng trong lúc LibreOffice/Poppler chạy không publish output một phần.
- Bản Windows vẫn dùng PowerPoint COM và test hành vi cũ vẫn pass.
- README đủ để người mới clone repo có thể cài và khởi chạy trên cả Windows lẫn Ubuntu.
