# Thiết kế workflow dựng video slide tự động

Ngày: 2026-09-30

## Mục tiêu

Một lệnh cục bộ nhận video mẫu, PPTX, timeline, ảnh logo và video outro, rồi xuất MP4 720p. Video mẫu chỉ cung cấp audio bài giảng. Hình bài giảng lấy từ từng slide PPTX và chuyển đúng mốc trong timeline. Workflow không dùng Canva, CapCut, agent, VLM hay STT. Mọi code và tài liệu mới nằm trong `workflow_capcut`; code legacy ở dự án khác chỉ được tham khảo.

## Giao diện đầu vào và đầu ra

Lệnh dự kiến:

```text
build-video --video SOURCE.mp4 --pptx SLIDES.pptx --timeline TIMELINE.txt \
  --logo LOGO.png --outro OUTRO.mp4 --output FINAL.mp4
```

Năm đường dẫn đầu vào luôn do người chạy truyền vào. `--output` là MP4 mới; mặc định không ghi đè file đang có. `--fps` tùy chọn, mặc định 24. Có thể đặt kích thước logo theo phần trăm chiều rộng video và khoảng cách đến mép bằng tùy chọn; mặc định 12% và 0 px. Vị trí cố định là sát góc dưới bên phải của phần bài giảng. Logo không phủ lên outro.

Timeline hiện có dạng `Slide 01: 00:00.000 --> 00:14.583` và có thể có dòng cuối `Outro/blank: ...`. Parser cũng nhận mảng JSON có `slide_id`, `start_ms`, `end_ms` và `duration_ms` tùy chọn. Bên trong, thời gian là số nguyên mili giây và mỗi hàng được ánh xạ theo `slide_id` sang slide thực trong PPTX; không suy ra slide từ vị trí hàng. Slide có thể được lặp hoặc đổi thứ tự nếu timeline nêu rõ ID.

## Luồng xử lý

1. Kiểm tra file, đọc metadata bằng `ffprobe`, đọc timeline, đếm slide PPTX và xác thực ID. Slide đầu bắt đầu tại 0; các đoạn có thời lượng dương; `duration_ms` nếu có phải lệch tối đa 20 ms so với `end_ms - start_ms`. Nếu hai mốc nối lệch tối đa 20 ms, đặt `start` của slide sau bằng `end` của slide trước; lệch lớn hơn là lỗi. `Outro/blank` chỉ hợp lệ sau slide cuối, phải bắt đầu ở mốc kết thúc slide cuối trong dung sai 20 ms và kết thúc không quá 20 ms sau audio nguồn. Mốc kết thúc slide cuối không được vượt quá audio nguồn.
2. Tạo một phiên PowerPoint riêng trên Windows để mở PPTX và xuất các slide được tham chiếu thành PNG trong thư mục tạm. Kích thước xuất theo tỷ lệ gốc đủ cho đầu ra 1280×720; khi tỷ lệ khác 16:9, co vừa và thêm viền để không cắt nội dung. Phần slide dùng overscan nhỏ để loại viền trắng mỏng do PowerPoint xuất ra; bước này không áp dụng cho outro. Chỉ đóng phiên PowerPoint do tool tạo, kể cả khi có lỗi; không động đến phiên người dùng đang mở. Không lưu ảnh trung gian vào thư mục đầu vào.
3. Đổi mọi mốc slide sang lưới frame của đầu ra, mặc định 24 fps, bằng cách làm tròn **mốc tuyệt đối** rồi lấy hiệu hai mốc liên tiếp. Cách này tránh cộng dồn sai số. Tạo danh sách concat từ các PNG và lặp ảnh cuối theo yêu cầu của FFmpeg để giữ thời lượng đoạn cuối. Render video slide đúng đến mốc kết thúc slide cuối; không dùng `-shortest` làm rút ngắn hình.
4. Lấy audio từ video mẫu thành **một track liên tục** từ 0 đến mốc kết thúc slide cuối; không tách audio theo slide. Dòng `Outro/blank` của timeline không thành slide. Phần đuôi này của video mẫu bị loại bỏ trước khi nối outro riêng. Nếu video mẫu thiếu audio hoặc audio ngắn hơn mốc slide cuối, dừng với lỗi rõ ràng.
5. Thu nhỏ logo giữ nguyên tỷ lệ và phủ ở góc dưới phải trong toàn bộ phần slide. Logo có hoặc không có alpha đều hợp lệ.
6. Chuẩn hóa outro về 1280×720, 24 fps, H.264/yuv420p và AAC. Giữ **toàn bộ hình và thời lượng** outro: co vừa trong khung 16:9, thêm nền đen hai bên nếu outro dọc. Giữ audio outro; nếu outro không có audio thì thêm im lặng tương ứng. Nối phần bài giảng và outro thành MP4 cuối.
7. Dùng `ffprobe` xác thực file cuối: 1280×720, H.264/AAC, 24 fps, thời lượng bằng mốc slide cuối cộng thời lượng outro trong dung sai một frame cộng sai số gói audio. Ghi báo cáo JSON gồm input, số slide, mốc cắt audio, thời lượng outro, đường dẫn output và các kiểm tra đã qua.

Mọi tiến trình ngoài được gọi bằng danh sách đối số, không dựng lệnh shell từ tên file. File đầu ra được render vào vị trí tạm rồi chuyển sang đường dẫn đích sau khi xác thực thành công; lỗi không để lại MP4 hoàn chỉnh giả.

## Bộ test hiện có

`workflow_capcut/test` có đủ năm đầu vào. PPTX có 20 slide 16:9. Video mẫu là 1280×720, 24 fps, audio dài 09:44.400. Timeline kết thúc slide 20 tại 09:40.791 và ghi `Outro/blank` tới 09:44.400. Kiểm tra đoạn đuôi cho thấy hình Gemini Notebook và audio im lặng gần hết, nên bỏ đoạn này. `outro.mp4` dài khoảng 7,701 giây, là video dọc 720×1280 và có audio. Đầu ra mong đợi dài khoảng 09:48.492. `logo.png` là ảnh RGB 474×316 và là dữ liệu test hợp lệ, dù không có nền trong suốt.

## Kiểm chứng

- Unit test parser cho TXT/JSON, ánh xạ `slide_id`, lỗi thiếu slide, duration sai và timeline hở/chồng.
- Integration test với bộ test trong `workflow_capcut/test`: kiểm tra thay hình ở các mốc slide, audio bài giảng liên tục, không còn hình Gemini Notebook, logo xuất hiện ở góc dưới phải của phần slide, trọn hình outro và audio outro ở cuối.
- `ffprobe` kiểm tra codec, độ phân giải, fps, stream audio và thời lượng. So sánh frame tại một vài mốc chuyển slide để phát hiện lệch frame.

## Giới hạn và lựa chọn

Mỗi slide là hình tĩnh; animation trong PPTX không được phát lại. Windows và Microsoft PowerPoint là yêu cầu của bản đầu tiên để giữ bố cục slide. Có thể thay bộ xuất PNG bằng LibreOffice sau này mà không đổi parser timeline hoặc phần ghép FFmpeg. PowerPoint hỗ trợ [`Slide.Export`](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.slide.export) với kích thước pixel; FFmpeg hỗ trợ [overlay, scale, pad và concat](https://ffmpeg.org/ffmpeg-filters.html).
