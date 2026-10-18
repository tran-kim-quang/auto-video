# Nghiệm thu Auto Video Web — 2026-10-19

## Kết quả

**PASS** Task 15 trên Linux, NVIDIA GeForce RTX 3050 6 GB, sau Task 16 (`cb384b0`).

- Root nghiệm thu: `/home/meconlonton/video_and_cc/acceptance-2026-10-19-rerun2`
- Voice mặc định dùng cho batch hai bài: `599fc048` (`Teacher_male`)
- Test tự động trước và sau nghiệm thu: `261 passed, 1 warning`
- VoiceStudio không có CUDA OOM, HTTP 400/500 từ `/generate`, hoặc ASR fallback trong lần chạy thành công.

## Thời gian thực tế

Thời gian dưới đây theo Asia/Bangkok. Batch mặc định có thao tác huỷ/thử lại và restart server theo kịch bản, nên bảng ghi cả lần chạy cuối lẫn thời gian từ lúc enqueue đầu tiên.

| Bài | Voice | Enqueue đầu | Lần chạy cuối | Hoàn tất | Thời gian lần chạy cuối | Tổng từ enqueue đầu |
| --- | --- | --- | --- | --- | ---: | ---: |
| T40 kiểm tra voice clone | `e5f71a3d` | 04:17:38 | 04:17:38 | 04:33:44 | 16:06 | 16:06 |
| T41 batch mặc định | `599fc048` | 04:34:31 | 04:36:33 | 04:54:00 | 17:27 | 19:29 |
| T40 batch mặc định | `599fc048` | 04:34:31 | 04:36:33 | 05:09:04 | 32:31 | 34:33 |

T40 của batch mặc định bao gồm thời gian chờ GPU trong lúc T41 sinh giọng. Đã quan sát T41 `rendering_lecture 40/40` đồng thời T40 `synthesizing_audio 1/40`.

## Đối chiếu spec §14

1. **PASS — chạy server và truy cập LAN.** `./run-web.sh` sẵn sàng với VoiceStudio, FFmpeg/ffprobe, LibreOffice/Poppler, logo và outro. Trang/API mở được qua `localhost:8080` và `http://192.168.1.9:8080`. Không có máy vật lý thứ hai trong phiên nghiệm thu; kiểm tra LAN được thực hiện từ chính server qua IP LAN, không dùng localhost. Trên máy này `lan_urls` tự phát hiện trả danh sách rỗng, nên IP được lấy bằng `hostname -I`.
2. **PASS — quét cây upload.** Hiện đúng hai bài hợp lệ `input-t40-41/t40`, `input-t40-41/chapter-a/t41`; hai lỗi tiếng Việt là `Thiếu file kịch bản .txt` và `Kịch bản có 1 slide, PPTX có 40 slide`; folder trung gian không hiện.
3. **PASS — hàng đợi và GPU.** T41 ở `synthesizing_audio` trong khi T40 ở `waiting_gpu`. Khi T41 chuyển sang xuất slide/render, T40 bắt đầu sinh giọng; render và synthesis chạy song song. UI hiện stage, tiến độ slide và ETA.
4. **PASS — output và media.** Mỗi folder lá của batch mặc định chứa đúng MP4 và `vi.vtt`. T41: 1280×720, 24 fps, H.264/AAC stereo 48 kHz, 24:20.800. T40: 1280×720, 24 fps, H.264/AAC stereo 48 kHz, 23:23.626. Video clone T40 cũng đạt các kiểm tra trên, dài 25:00.583.
5. **PASS — WebVTT và phát trên trang.** T41 có 506 cue, T40 có 478 cue. Mọi `start_ms`/`end_ms` của 40 slide trùng ranh giới cue; không cue nào vượt thời điểm bắt đầu outro. Trên video player tại khoảng 6.73 giây, track ở chế độ `showing` và active cue là `Ở tiết này, yêu cầu không chỉ là ra đáp số mà còn phải trình bày lập luận rõ ràng.` Trang không có link tải VTT.
5b. **PASS có giới hạn LAN nêu ở bước 1.** Tải MP4 T41 qua `192.168.1.9` có SHA-256 `3e8c3ff2b4e103aaed98364d097e9d93d803c2ff73f71b166dbfaa802ea9b970`, trùng file server. ZIP mở và test sạch, chứa đúng:
   - `input-t40-41/chapter-a/t41/TOAN12_C2_BTCC_T41.mp4`
   - `input-t40-41/t40/TOAN12_C2_BTCC_T40.mp4`

   ZIP không chứa `vi.vtt`.
6. **PASS — quản lý giọng.** Clip mẫu dài 5.49 giây, transcript chính xác: `Các em thân mến, hôm nay chúng ta cùng ôn tập chủ đề véc tơ trong không gian. Hãy lắng nghe thật kỹ.` Tạo voice `e5f71a3d`, auto-preview và preview câu tuỳ ý đều thành công; đặt làm mặc định tạm thời; render T40 thành công; sau đó khôi phục `599fc048` và xoá voice thử. Clip 10.0 giây bị UI khoá nút tạo với `File dài 10.0 giây, vượt giới hạn 7 giây trên máy này`; server trả HTTP 400 với `File ghi âm dài 10.0 giây, tối đa 7 giây trên máy này — hãy cắt ngắn và nhập đúng lời nói của đoạn đó`, không gọi VoiceStudio.
7. **PASS — preview ưu tiên.** Trong lúc T41 đang sinh giọng, preview voice mặc định đã cache trả WAV sau 7.24 giây, trong phạm vi khoảng một request slide.
8. **PASS — huỷ và thử lại.** Huỷ T40 khi đang `waiting_gpu`; job chuyển `interrupted` với `Đã huỷ`; thử lại thành công. Một request retry trực tiếp gửi trước khi trạng thái bất đồng bộ kịp chuyển sang `interrupted` trả 409; sau lần poll kế tiếp, thao tác retry đúng như UI thành công.
9. **PASS — restart.** Tắt server khi T41 đang `synthesizing_audio 2/40` và T40 `waiting_gpu`; sau khi bật lại cả hai ở trạng thái `interrupted`, rồi retry và hoàn tất thành công.
10. **PASS — an toàn đường dẫn.** `../escape-task15.txt` trả HTTP 400 `Đường dẫn upload chứa thành phần không hợp lệ`; `root/CON.txt` trả HTTP 400 `Tên file dành riêng cho Windows`; không có file nào được ghi ra ngoài batch.
11. **PASS — test tự động.** `.venv/bin/python -m pytest -q` sau nghiệm thu: `261 passed, 1 warning in 15.08s`. Warning là `StarletteDeprecationWarning` từ `fastapi.testclient`.

## Ảnh chụp và bằng chứng

- Tab Tạo video: `/home/meconlonton/video_and_cc/acceptance-2026-10-19-rerun2/screenshots/01-create-upload.png`
- Tab Giọng đọc: `/home/meconlonton/video_and_cc/acceptance-2026-10-19-rerun2/screenshots/02-voices-clone-default.png`
- Tab Hàng đợi: `/home/meconlonton/video_and_cc/acceptance-2026-10-19-rerun2/screenshots/03-queue-running.png`
- Video và phụ đề đang chạy: `/home/meconlonton/video_and_cc/acceptance-2026-10-19-rerun2/screenshots/04-video-subtitles.png`
- VoiceStudio trước restart: `/home/meconlonton/video_and_cc/acceptance-2026-10-19-rerun2/logs/voicestudio-20261018T211513Z.log`
- VoiceStudio sau restart: `/home/meconlonton/video_and_cc/acceptance-2026-10-19-rerun2/logs/voicestudio-20261018T213552Z.log`
- File tải thử qua LAN: `/home/meconlonton/video_and_cc/acceptance-2026-10-19-rerun2/lan-downloads/`

## Hạn chế đã biết

- **Clone với transcript rỗng thất bại trên GPU 6 GB:** VoiceStudio cần Whisper/ASR và TTS cùng nằm trên GPU, dẫn tới OOM. Trường hợp này không được chạy trong lần nghiệm thu theo chỉ dẫn phần cứng.
- Bước 5b chưa được thử từ máy vật lý thứ hai; chỉ thử qua IP LAN của server từ chính máy server.
- Giới hạn mặc định 7 giây dành cho RTX 3050 6 GB. Máy có nhiều VRAM hơn có thể cấu hình `--max-reference-seconds` theo công thức đã đo trong spec.

## Cleanup

- Voice thử `e5f71a3d` đã bị xoá.
- `599fc048` là voice mặc định sau nghiệm thu.
- Web server, Chrome headless và VoiceStudio backend đã dừng.
