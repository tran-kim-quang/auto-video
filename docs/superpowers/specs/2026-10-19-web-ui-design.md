# Auto Video Web — thiết kế giao diện web dùng chung qua LAN

Ngày: 2026-10-19 · Repo: `auto-video` · Người thực hiện dự kiến: Codex

Ghi chú đọc: mục nào ghi **[quyết định của người viết spec]** là lựa chọn mặc định, chủ dự án chưa duyệt riêng; builder giữ nguyên trừ khi có lý do kỹ thuật rõ ràng. Mục **Để builder tự quyết** liệt kê những gì được tự do chọn.

---

## 1. Mục đích và lý do

Auto-video hiện là app desktop Tkinter: chọn thư mục gốc, quét đệ quy các cặp `.pptx` + `.txt` cùng tên, sinh giọng đọc từng slide bằng VoiceStudio (local, GPU), xuất ảnh slide, render video bài giảng + logo + outro.

Cần một **trang web** thay cho giao diện Tkinter để:

1. **Demo cho sếp** — trực quan, nhìn là hiểu luồng: upload folder → hệ thống tự tìm bài → chọn giọng → ra video + phụ đề.
2. **Nếu được duyệt**: chạy trên một máy server Windows có GPU (card Quadro, chưa rõ thông số), các máy khác **trong cùng LAN** mở trình duyệt để upload bài giảng, chọn giọng, nhân bản giọng, theo dõi hàng đợi và tải kết quả.

Giọng đọc **tiếp tục chạy local qua VoiceStudio** (đã cân nhắc TTS cloud và quyết định không dùng). Cơ chế cốt lõi phải giữ nguyên: **mỗi slide được sinh một file WAV riêng**, timeline của video là tổng độ dài thực tế của các WAV đó.

## 2. Mục tiêu / ngoài phạm vi / điều không được xảy ra

**Mục tiêu**
- Trang web một trang, chia 3 tab ngang: **Tạo video · Giọng đọc · Hàng đợi** (bố cục "A" đã được chọn).
- Upload cả cây thư mục từ trình duyệt; hệ thống quét đệ quy tìm **folder lá** và tạo mỗi folder lá hợp lệ đúng 1 video.
- Mỗi folder lá sau khi chạy có `output/` chứa **đúng 2 file**: video `.mp4` và phụ đề `vi.vtt`.
- **Phụ đề ở lại trên server** (trong `video_and_cc/uploads/...`); người dùng **chỉ tải video** về. Một batch có nhiều bài (vd. upload cả ngày làm việc) thì tải **một file zip** chứa các video.
- Phụ đề VTT **sinh từ dữ liệu pipeline** (`report.json`), **không dùng Whisper/ASR**.
- Thư viện giọng: xem danh sách, nghe mẫu, nghe thử câu, **nhân bản giọng từ file ghi âm có sẵn**, đặt giọng mặc định, xoá — tất cả qua API sẵn có của VoiceStudio.
- Tối đa **3 bài cùng hoạt động**; bước dùng GPU xếp hàng, các bước CPU chạy song song.
- Chạy được trên **Windows** (máy server thật) và **Linux** (máy phát triển/demo hiện tại).

**Ngoài phạm vi (bản này)**
- Đăng nhập, phân quyền, nhiều người dùng tách biệt. Ai mở được trang đều thấy và thao tác toàn bộ.
- Ghi âm trực tiếp trên trang web (chỉ upload file có sẵn).
- TTS cloud; ASR/Whisper cho phụ đề.
- Trang cài đặt logo/outro (đọc từ file cài đặt, chỉ hiển thị trên UI — xem §9).
- Tự động dọn dẹp dữ liệu cũ.
- Gỡ bỏ giao diện Tkinter (giữ nguyên trong repo, không phát triển thêm).

**Điều không được xảy ra (dù "chạy được" vẫn coi là hỏng)**
- Gộp nhiều slide thành một lần sinh audio rồi mất ranh giới slide. Timeline slide phải đến từ độ dài WAV từng slide.
- Hai model chiếm GPU cùng lúc ngoài tầm kiểm soát dẫn tới CUDA OOM (đã xảy ra trên RTX 3050 6 GB khi backend nạp thêm model).
- File upload ghi ra ngoài thư mục làm việc (path traversal) hoặc ghi đè dữ liệu của lần upload khác.
- `output/` trong folder lá chứa thêm file khác ngoài `.mp4` và `vi.vtt`.
- Phụ đề lệch ranh giới slide.

## 3. Ràng buộc kỹ thuật đã biết

- **VoiceStudio** chạy local tại `http://127.0.0.1:3900`, đã được `VoiceStudioSupervisor` tự khởi động nếu chưa chạy (`video_workflow/voicestudio_supervisor.py`). Cấu hình sinh giọng hiện hành nằm ở `VoiceStudioConfig` (`video_workflow/voicestudio_client.py`): engine `omnivoice`, `Vietnamese`, 32 bước, seed 42, **speed 0.9**, **chunk 300 ký tự**, postprocess bật, crossfade 50 ms. Không đổi các giá trị này trong dự án này.
- **GPU là cổ chai**: trên RTX 3050, sinh giọng chiếm ~88% thời gian một bài; chạy nhiều request VoiceStudio song song **không** tăng tốc (GPU đã bão hoà). VoiceStudio tự chia số worker GPU theo VRAM (≈5 GB/worker, biến môi trường `OMNIVOICE_GPU_WORKERS` để ép). Trên card Quadro lớn hơn có thể có >1 worker.
- **Xuất ảnh slide**: Windows dùng PowerPoint desktop qua COM (`video_workflow/powerpoint.py`), Linux dùng LibreOffice + Poppler. PowerPoint COM **không an toàn khi gọi song song** → xuất slide phải tuần tự trên Windows. Mỗi luồng gọi COM phải `CoInitialize` (đã có `_com_context` trong `worker.py`).
- **Pipeline hiện có** được tái sử dụng, không viết lại: `build_narrated_video` (`video_workflow/pipeline.py`) với các stage `validating → synthesizing_audio → assembling_narration → exporting_slides → rendering_lecture → preparing_outro → joining → verifying → publishing`; parser kịch bản `load_slide_script` / `validate_script_slide_count` (`video_workflow/narration_script.py`); cache WAV theo text + profile + toàn bộ config (`video_workflow/narration.py`).
- **Kịch bản** `.txt` UTF-8 dùng marker `Slide N:`; số đoạn phải khớp số slide PPTX (slide đầu có thể là 1 hoặc 2 theo quy tắc hiện có).
- **Thời gian tham khảo** (RTX 3050, chunk 300, speed 1.0): bài 40 slide / ~27.300 ký tự mất ~15 phút (sinh giọng ~13 phút, xuất slide ~13 s, render ~1 phút, ghép outro ~40 s). Speed 0.9 dự kiến thêm ~10% thời gian sinh giọng.
- Python ≥ 3.12. Bản Windows được đóng gói portable bằng PyInstaller (`scripts/build_windows.py`, `build-windows.bat`) — thiết kế web phải đóng gói được theo cách này.

## 4. Kiến trúc tổng quát

```text
Trình duyệt (máy bất kỳ trong LAN)
   │  HTTP (upload, API JSON, tải file, xem video)
   ▼
Web server Python (máy server có GPU)  ──►  Scheduler (tối đa 3 job hoạt động)
   │                                         │
   │                                         ├─ GPU lane ─► VoiceStudio :3900 (sinh giọng, nhân bản, nghe thử)
   │                                         ├─ Slide-export lane (tuần tự trên Windows)
   │                                         └─ FFmpeg (render, outro, ghép) — song song
   ▼
video_and_cc/  (toàn bộ dữ liệu của tool)
```

**[quyết định của người viết spec]**
- Backend: **FastAPI + uvicorn** (thêm `fastapi`, `uvicorn`, `python-multipart` vào dependencies).
- Frontend: **HTML + CSS + JavaScript thuần**, phục vụ tĩnh từ package Python, **không có bước build Node** (để PyInstaller đóng gói đơn giản).
- Cập nhật tiến độ: **polling** API mỗi 1–2 giây (đủ cho LAN, đơn giản hơn WebSocket/SSE).
- Entry point mới: `python -m video_workflow.web` và script `video-workflow-web`; thêm `run-web.sh` / `run-web.bat`. Mặc định bind `0.0.0.0:8080`, tham số `--host`, `--port`, `--root` (thư mục làm việc).
- Khi khởi động, in ra địa chỉ LAN để mở từ máy khác (vd. `http://192.168.1.20:8080`).

## 5. Thư mục làm việc `video_and_cc`

Mặc định `Path.home() / "video_and_cc"` (Windows: `C:\Users\<tên>\video_and_cc`; Linux: `/home/<tên>/video_and_cc`), tự tạo nếu chưa có, ghi đè được bằng `--root`.

```text
video_and_cc/
  uploads/
    <batch-id>/                  ← mỗi lần upload một batch-id riêng, không bao giờ ghi đè batch khác
      <tên folder gốc người dùng>/
        .../t40/                 ← folder lá
          TOAN12_C2_BTCC_T40.pptx
          TOAN12_C2_BTCC_T40.txt
          output/                ← do tool tạo, đúng 2 file
            TOAN12_C2_BTCC_T40.mp4
            vi.vtt
  work/<job-id>/                 ← file trung gian của từng job (report.json, timeline.json,
                                   audio-manifest.json, combined.wav, audio/, slide-images/)
  tts-cache/                     ← cache WAV theo slide (cơ chế hiện có)
  state/                         ← jobs, batches, settings (JSON, ghi nguyên tử như JsonStore hiện có)
  logs/                          ← log job + log VoiceStudio
```

- `batch-id` dạng thời gian + ngắn gọn ngẫu nhiên (vd. `20261019-143005-a1b2`) **[quyết định của người viết spec]**.
- Không tự dọn dẹp. UI hiển thị dung lượng đang dùng là tuỳ chọn (xem §13).

## 6. Quy tắc folder lá và quét

**Định nghĩa (chủ dự án quyết):** folder lá là folder cuối cùng, **không có folder con**, và **chỉ chứa đúng 2 file: một `.pptx` và một `.txt`** (kịch bản thuyết minh). Hai file **không cần trùng tên**.

Chi tiết:
- Khi xét "không có folder con", **bỏ qua** folder tên `output` (không phân biệt hoa thường), folder ẩn (bắt đầu bằng `.`) và symlink — giống `_ignored_directory` trong `video_workflow/folder_batch.py`. Nhờ vậy folder lá đã chạy (có `output/`) vẫn là folder lá.
- Khi đếm file, **bỏ qua** file ẩn và file rác hệ điều hành (`Thumbs.db`, `desktop.ini`, `.DS_Store`, file tạm `~$*.pptx` của PowerPoint) **[quyết định của người viết spec]**.
- Folder còn folder con (sau khi bỏ qua như trên) là folder trung gian: không hiển thị, không báo lỗi, chỉ đi tiếp xuống dưới.
- Folder lá nhưng không đúng chuẩn → **hiển thị kèm lý do bằng tiếng Việt** và không cho tạo video:
  - "Thiếu file kịch bản .txt" / "Thiếu file .pptx"
  - "Có N file (cần đúng 1 .pptx và 1 .txt)" — kể cả có file lạ như `.docx`, `.mp3`
  - "Có 2 file .pptx" / "Có 2 file .txt"
  - Lỗi đọc kịch bản (thông điệp từ `ScriptError`, dịch sang tiếng Việt)
  - "Kịch bản có X đoạn, PPTX có Y slide" (dùng `count_pptx_slides` + `validate_script_slide_count`)
- Folder rỗng: bỏ qua, không báo.
- Kiểm tra kịch bản và số slide **ngay lúc quét** để báo lỗi trước khi xếp hàng.
- Tên file kết quả: `<tên file .pptx không đuôi>.mp4` và `vi.vtt` **[quyết định của người viết spec — `vi.vtt` theo quy ước của luồng upload CC trong `pyramid-upload-to-r2`]**.

Bộ quét mới **thay thế** logic ghép cặp cùng tên trong `discover_folder_jobs` cho luồng web (có thể viết hàm mới; giữ hàm cũ cho Tkinter nếu tiện).

## 7. Luồng upload

- Người dùng kéo thả một folder vào vùng upload, hoặc bấm để chọn folder (`<input type="file" webkitdirectory multiple>`; kéo thả dùng `DataTransferItem.webkitGetAsEntry()` để đọc cả cây).
- Client gửi kèm **đường dẫn tương đối** của từng file; client **không upload** folder `output/`, file/folder ẩn và file rác ở §6 **[quyết định của người viết spec — tránh đẩy video cũ nặng lên lại]**.
- Server **bắt buộc** làm sạch đường dẫn: từ chối đường dẫn tuyệt đối, `..`, ký tự/tên không hợp lệ trên Windows (dùng lại tinh thần `validate_output_name` trong `job_models.py`), độ dài vượt giới hạn Windows. Mọi file phải nằm trong `uploads/<batch-id>/`.
- File có đuôi khác `.pptx`/`.txt` **vẫn được lưu**, để bước quét báo đúng lỗi "Có N file" cho folder đó thay vì im lặng coi folder là hợp lệ. Tổng dung lượng một batch tối đa 1 GB **[quyết định của người viết spec]**.
- Upload xong → server quét ngay batch đó và trả kết quả cho tab Tạo video (§10.1).
- Hiển thị tiến độ upload (phần trăm).

## 8. Kết quả và file trung gian

- `build_narrated_video` chạy với `artifact_directory = video_and_cc/work/<job-id>/` (file trung gian ở đây).
- Sau khi pipeline publish thành công: tạo `vi.vtt` (§9), rồi **thay nguyên tử** `<folder lá>/output/` bằng folder mới chỉ gồm `<stem>.mp4` + `vi.vtt` (viết vào folder staging cạnh đó rồi rename, giống `_publish_artifact_tree`). Lỗi hoặc huỷ giữa chừng không được làm mất `output/` cũ.
- `output/` do tool quản lý hoàn toàn; chạy lại sẽ thay cả folder.
- Cache WAV: cache hiện tại khoá theo đường dẫn `.pptx` (`pipeline.py`, `cache_key` từ `pptx.resolve()`), nên upload lại cùng bài vào batch mới sẽ không dùng lại cache. Chấp nhận được cho bản này; nếu muốn tái sử dụng thì khoá theo hash nội dung kịch bản (xem §13).

## 9. Phụ đề `vi.vtt` từ `report.json`

**Nguồn dữ liệu:** `report.json` của job (`NarratedBuildReport`), mảng `slides[]` có `slide_id`, `text` (lời thuyết minh, đúng chữ kịch bản, đã NFC), `start_ms`, `end_ms`. Mốc này là mốc của audio thuyết minh; video bài giảng bắt đầu ở 0 và outro nằm sau slide cuối, nên **không có cue nào trong phần outro**.

**Thuật toán (chủ dự án đồng ý hướng chia theo tỷ lệ ký tự):**
1. Với mỗi slide, tách `text` thành câu tại `.`, `!`, `?`, `…` và xuống dòng; bỏ đoạn rỗng; gộp khoảng trắng thừa.
2. Câu dài hơn **90 ký tự** thì tách tiếp tại `;`, `:`, `,`; nếu vẫn dài thì tách ở khoảng trắng gần giữa nhất. Không tách giữa một từ **[ngưỡng 90 là quyết định của người viết spec]**.
3. Chia khoảng `[start_ms, end_ms]` của slide cho các cue **theo tỷ lệ số ký tự không tính khoảng trắng** của từng cue. Cue nối tiếp nhau liên tục; cue cuối kết thúc đúng `end_ms`; làm tròn mili giây.
4. Cue ngắn hơn 600 ms thì gộp với cue kế bên trong cùng slide **[quyết định của người viết spec]**.
5. Ranh giới slide luôn khớp tuyệt đối với `start_ms`/`end_ms`. Sai lệch chỉ nằm bên trong một slide (ước lượng tối đa ~1–2 giây ở giữa slide, chưa đo).

**Định dạng (theo `scripts/extract_cc.py` của `pyramid-upload-to-r2`):**
```text
WEBVTT

1
00:00:00.000 --> 00:00:04.120
Chào các em, hôm nay chúng ta làm bài tập cuối chương.

2
00:00:04.120 --> 00:00:08.900
...
```
- Dòng đầu `WEBVTT`, một dòng trống; mỗi cue: số thứ tự (bắt đầu từ 1, liên tục toàn file), mốc `HH:MM:SS.mmm --> HH:MM:SS.mmm` (luôn có giờ), một dòng chữ, một dòng trống. UTF-8, xuống dòng `\n`.
- Chữ trong cue lấy nguyên văn kịch bản (dạng đọc, ví dụ "véc tơ A D"), escape `&`, `<`, `>` theo chuẩn WebVTT.
- Viết hàm thuần (không I/O) `render_vtt_from_report(report) -> str` để test dễ.

## 10. Giao diện

Một trang, thanh tab ngang trên cùng: **Tạo video | Giọng đọc | Hàng đợi**. Toàn bộ chữ trên UI bằng tiếng Việt. Thiết kế gọn, sáng sủa, đủ "ra dáng" để demo; giao diện desktop là chính, không bắt buộc tối ưu điện thoại.

### 10.1 Tab Tạo video

```text
┌───────────────────────────────────────────────────────────┐
│ 📁 Kéo thả folder bài giảng vào đây (hoặc bấm để chọn)     │
└───────────────────────────────────────────────────────────┘
Đã upload: Toan12_Chuong2 (5 folder lá)
 ☑ Toan12_Chuong2/t40 · T40.pptx + T40.txt · 40 slide        ✅
 ☑ Toan12_Chuong2/t41 · T41.pptx + T41.txt · 40 slide        ✅
 ☐ Toan12_Chuong2/t43 · Thiếu file kịch bản .txt             ⚠️ (không tick được)
 ☐ Toan12_Chuong2/t44 · Kịch bản 38 đoạn, PPTX 40 slide       ⚠️
Giọng đọc: [⭐ Teacher_male ▾]     Logo/Outro: logo.png · Outro720.mp4
[ Tạo 2 video · ước tính ~32 phút ]
```
- Folder hợp lệ được tick sẵn; người dùng bỏ tick bài không muốn chạy.
- Ô chọn giọng điền sẵn **giọng mặc định**; đổi được cho lần tạo này (áp dụng cho mọi bài trong lần bấm).
- Ước tính thời gian = tổng số ký tự kịch bản × tốc độ đo được + ~2 phút/bài cho phần render. Tốc độ lấy trung bình từ các job đã xong trên máy đó (`generation_seconds` / ký tự); chưa có lịch sử thì dùng 0,032 giây/ký tự **[quyết định của người viết spec, từ số đo RTX 3050 + speed 0.9]**. Ghi rõ là "ước tính".
- Bấm tạo → chuyển sang tab Hàng đợi.
- Có danh sách các batch đã upload trước đó để chọn lại/quét lại (đơn giản: danh sách theo thời gian) **[quyết định của người viết spec]**.

### 10.2 Tab Giọng đọc

```text
Danh sách giọng (3)                                   │ Thêm giọng mới (nhân bản)
⭐ Teacher_male · 37 s mẫu · ▶ Nghe mẫu · 🔊 Nghe thử  │ [Tên giọng                ]
   Cô Lan – Văn · 12 s mẫu · ▶ · 🔊 · Đặt mặc định · 🗑 │ [ 📁 Kéo thả file ghi âm   ]
   Thầy Hùng – Toán · 9 s · ▶ · 🔊 · Đặt mặc định · 🗑  │  .wav/.mp3/.m4a · nên 5–15 s
Nghe thử câu: [Cho tam giác ABC vuông tại A, …] [🔊 Đọc]│ [Lời nói trong file (không bắt buộc)]
                                                       │ [ Tạo giọng ]
```
API VoiceStudio dùng (đường dẫn backend: `~/.config/VoiceStudio/runtime/project/backend/api/routers/profiles.py` trên Linux):

| Chức năng | API | Ghi chú |
|---|---|---|
| Danh sách | `GET /profiles` | Trường: `id`, `name`, `kind`, `language`, `ref_text`, `audio_url`, `image_url`, `created_at`… |
| Nghe mẫu | `GET /profiles/{id}/audio` | Server web proxy lại cho trình duyệt (VoiceStudio chỉ nghe ở 127.0.0.1) |
| Nhân bản | `POST /profiles` multipart: `name`, `ref_audio` (file), `ref_text` (có thể rỗng), `language`, `kind=clone` | `ref_text` rỗng → VoiceStudio tự nhận dạng bằng ASR local |
| Xoá | `DELETE /profiles/{id}` | Xoá thật trong VoiceStudio (danh sách dùng chung với app VoiceStudio) |
| Nghe thử câu | `POST /generate` với `profile_id` + config hiện hành | Trả WAV, phát ngay trên trang |

- **Giọng mặc định** lưu trong `state/` của tool (không phải của VoiceStudio). Ban đầu là `599fc048` (`Teacher_male`). Không cho xoá giọng đang là mặc định **[quyết định của người viết spec]**.
- Gợi ý trên form: file 5–15 giây, một người nói, không nhạc nền. Nếu file > 20 giây, hiển thị cảnh báo: VoiceStudio sẽ bỏ qua lời nói đã nhập và tự chọn một đoạn.
- Tạo xong → tự đọc một câu mẫu bằng giọng mới để nghe ngay.
- **Lời nói để trống và nguy cơ OOM [quyết định của người viết spec]:** khi `ref_text` rỗng, `POST /profiles` gọi `services/asr_backend.transcribe_reference` của VoiceStudio, nạp thêm một model nhận dạng giọng nói (ưu tiên CUDA) bên cạnh model TTS. Vì vậy:
  - Có nhập lời nói → yêu cầu tạo giọng được **ưu tiên** như nghe thử (chen giữa hai request slide, §11).
  - Để trống lời nói → yêu cầu tạo giọng **chờ đến khi không job nào giữ GPU** (xếp sau stage sinh giọng đang chạy). UI hiện "Đang chờ GPU rảnh để tự nhận dạng lời nói — nhập lời nói để tạo ngay".
  - Sau khi tạo giọng với lời nói rỗng, gọi `POST /system/flush-memory?unload_model=false` để giải phóng bộ nhớ trước job kế tiếp.
- Câu nghe thử mặc định là một câu toán (để nghe cách đọc ký hiệu); người dùng sửa được.
- Ai trong LAN cũng xoá được (trừ giọng mặc định) — xác nhận trước khi xoá.

### 10.3 Tab Hàng đợi

```text
Đang chạy
 🎙️ t40 · Sinh giọng 28/40 ███████░░ · còn ~4 phút · [Huỷ]
 🎞️ t39 · Render video ████░░░░                    · [Huỷ]
 ⏳ t41 · Chờ GPU (vị trí 1)                        · [Huỷ]
Đã xong
 Batch Toan12_Chuong2 (upload 14:30) · 4/5 xong · [⬇ Tải tất cả video (.zip)]
   ✅ t38 · 21:12 · ⬇ MP4 · ▶ Xem
   ❌ t37 · Lỗi ở slide 12: VoiceStudio hết bộ nhớ GPU · [Thử lại]
```
- Job được nhóm theo batch upload.
- Mỗi job: tên folder lá (kèm đường dẫn tương đối trong batch), giọng, trạng thái, bước hiện tại (tên tiếng Việt cho từng `JobStage`), tiến độ slide khi đang sinh giọng, thời gian còn lại ước tính, thời điểm bắt đầu/kết thúc.
- Nút **Huỷ** (job chờ hoặc đang chạy — dừng ở ranh giới an toàn như pipeline hiện có), **Thử lại** (job lỗi/bị huỷ/bị gián đoạn).
- Job xong: **tải MP4** và **xem ngay** trên trang (thẻ `<video>` kèm `<track kind="subtitles" src="…/vi.vtt" srclang="vi">` để thấy phụ đề khi xem). Server phải hỗ trợ HTTP Range cho file video. **Không có nút tải `vi.vtt`** — file phụ đề ở lại trên server (chủ dự án quyết).
- **Tải cả batch dạng zip** (chủ dự án quyết): nút "Tải tất cả video (.zip)" hiện khi batch có từ 2 video đã xong. **[quyết định của người viết spec]**:
  - Zip chỉ chứa các file `.mp4` đã xong, giữ đường dẫn tương đối của folder lá trong batch (vd. `Toan12_Chuong2/t40/TOAN12_C2_BTCC_T40.mp4`) để không trùng tên; không chứa `output/` hay `vi.vtt`.
  - Zip được tạo kiểu **stream** khi tải (không lưu file zip tạm), nén kiểu STORED (video đã nén sẵn), hỗ trợ file > 4 GB (ZIP64).
  - Nếu batch còn bài chưa xong hoặc lỗi, vẫn cho tải các bài đã xong và ghi rõ trên nút "4/5 video".
  - Tên file zip: `<tên folder gốc>_<batch-id>.zip`.
- Lỗi hiển thị bằng tiếng Việt dễ hiểu; chi tiết kỹ thuật để trong phần mở rộng / log.
- Job đang chạy khi server tắt → trạng thái "bị gián đoạn" khi khởi động lại (giống `recover_startup` hiện có), cho phép Thử lại.

## 11. Hàng đợi và chạy đồng thời

**[quyết định của người viết spec — vì chưa biết cấu hình card Quadro]**
- `MAX_ACTIVE_JOBS = 3` (cấu hình được): tối đa 3 job ở trạng thái đang chạy cùng lúc; job khác chờ FIFO.
- **GPU lane** với `GPU_SLOTS = 1` mặc định (cấu hình được). Một job phải giữ slot GPU suốt stage `synthesizing_audio`. Job xếp hàng vào GPU theo thứ tự FIFO.
- **Yêu cầu tương tác được ưu tiên**: nghe thử câu và tạo giọng mới cũng cần GPU, nhưng không được chờ hết cả stage sinh giọng của một bài (~15 phút). Chúng phải được phục vụ **giữa hai request slide** của job đang giữ GPU (độ trễ tối đa ~1 request slide, ~20–30 giây trên RTX 3050). Cách cài đặt để builder chọn (vd. khoá ưu tiên lấy/nhả theo từng request VoiceStudio).
- **Slide-export lane**: trên Windows chỉ 1 job xuất slide tại một thời điểm (PowerPoint COM). Trên Linux có thể song song nhưng giữ 1 cho đơn giản.
- Các stage FFmpeg (`rendering_lecture`, `preparing_outro`, `joining`) và tạo phụ đề chạy song song không giới hạn ngoài `MAX_ACTIVE_JOBS`.
- Thứ tự stage hiện tại của `build_narrated_video` là sinh giọng trước, xuất slide sau. Builder **được phép** chạy xuất slide sớm/song song với sinh giọng của chính job đó nếu không phá vỡ cơ chế hiện có; không bắt buộc.
- `QueueController` + `QueueWorker` hiện chỉ cho 1 job chạy (`claim_next` trả `None` nếu đã có job RUNNING). Cần mở rộng hoặc viết scheduler mới cho web; Tkinter không cần dùng scheduler mới.
- Mọi job dùng chung **một** `VoiceStudioSupervisor` / client. Cấu hình VoiceStudio theo job chỉ khác nhau ở `profile_id`.
- Trước khi chốt `GPU_SLOTS` > 1 trên máy Windows: chạy lại benchmark (2–3 request `/generate` đồng thời với `OMNIVOICE_GPU_WORKERS` tương ứng) và chỉ tăng nếu tổng thời gian giảm thật.

## 12. Cài đặt, khởi động, đóng gói

- Logo và outro dùng chung, đọc từ `video_and_cc/state/settings.json` (cùng định dạng `GlobalSettings` hiện có: `logo`, `outro`, `voicestudio_api_url`, `profile_id`, `backend_override`, `inference_config_version`). Lần đầu chưa có thì cho phép khởi tạo bằng tham số dòng lệnh `--logo`, `--outro` hoặc copy từ `.workflow_data/settings.json` cũ nếu tồn tại **[quyết định của người viết spec]**. UI chỉ hiển thị tên file; nếu thiếu logo/outro thì nút Tạo bị khoá kèm hướng dẫn.
- Khởi động: kiểm tra FFmpeg/ffprobe, công cụ xuất slide (PowerPoint trên Windows, LibreOffice + Poppler trên Linux), VoiceStudio (qua supervisor) và báo trạng thái trên UI (dải thông báo đầu trang nếu thiếu).
- Windows: tài liệu hướng dẫn mở cổng trong Windows Firewall; `run-web.bat`; cập nhật `scripts/build_windows.py` để đóng gói thêm entry web + file tĩnh.
- **Cách chạy trên Windows [quyết định của người viết spec]:** web server chạy như chương trình bình thường **trong phiên của người dùng đã đăng nhập**, không phải Windows service (PowerPoint COM không xuất slide được từ service không có desktop). Entry web build **có cửa sổ console** (không `--windowed` như bản Tkinter) để in địa chỉ LAN và log. Muốn tự chạy khi bật máy: hướng dẫn tạo tác vụ Task Scheduler "At log on" (kèm auto-login nếu cần) trong README; không làm service.
- Cập nhật README: cách chạy web, thư mục `video_and_cc`, quy tắc folder lá, cách truy cập từ máy khác.

## 13. Để builder tự quyết

- Cấu trúc module trong `video_workflow/` (vd. `web/app.py`, `web/scheduler.py`, `web/static/…`, `leaf_scan.py`, `vtt.py`).
- Định dạng API JSON nội bộ giữa frontend và backend.
- Cơ chế khoá GPU ưu tiên (§11), miễn đạt yêu cầu độ trễ.
- Có khoá cache TTS theo hash nội dung thay vì đường dẫn hay không (§8).
- Có hiển thị dung lượng `video_and_cc` hay không.
- Chi tiết giao diện (màu, font, icon) trong khuôn khổ bố cục §10.
- Có chạy xuất slide song song với sinh giọng trong cùng job hay không (§11).

## 14. Tiêu chí nghiệm thu (kịch bản demo)

1. Trên máy server, chạy `run-web` → trang mở được ở `http://localhost:8080` và từ một máy khác cùng LAN qua IP của server.
2. Upload một folder gốc chứa 2 folder lá hợp lệ (vd. T40, T41), 1 folder thiếu `.txt`, 1 folder có kịch bản lệch số slide, 1 folder trung gian → danh sách hiện đúng 2 bài hợp lệ, 2 bài lỗi kèm lý do tiếng Việt, folder trung gian không hiện.
3. Bấm tạo 2 video với giọng mặc định → Hàng đợi hiển thị bước, tiến độ slide, ước tính thời gian; job thứ hai chờ GPU trong khi job thứ nhất sinh giọng, và render của job này chạy trong lúc job kia sinh giọng.
4. Mỗi folder lá có `output/` chứa **đúng** `<stem>.mp4` và `vi.vtt`; video đạt các kiểm tra hiện có (1280×720, 24 fps, H.264/AAC, độ dài khớp).
5. `vi.vtt` hợp lệ WebVTT, định dạng như §9, ranh giới slide trùng `start_ms`/`end_ms` trong `report.json`, không có cue sau thời điểm bắt đầu outro; xem trên trang thấy phụ đề chạy khớp lời. Trang không có nút tải `vi.vtt`.
5b. Từ một máy khác trong LAN: tải 1 video riêng lẻ; với batch 2 bài, tải "Tất cả video (.zip)" → zip mở được, chứa đúng 2 file `.mp4` theo đường dẫn tương đối, không có `vi.vtt`.
6. Tab Giọng đọc: upload file ghi âm ~10 giây → giọng mới xuất hiện trong danh sách, tự đọc câu mẫu; nghe thử câu tuỳ ý; đặt làm mặc định; tạo 1 video bằng giọng mới thành công; xoá giọng không phải mặc định.
7. Trong lúc một job đang sinh giọng, bấm "Nghe thử câu" → có audio trong vòng ~1 request slide.
8. Huỷ một job đang chạy → `output/` cũ (nếu có) còn nguyên; Thử lại chạy được.
9. Tắt server giữa chừng rồi bật lại → job đang chạy chuyển "bị gián đoạn", thử lại được.
10. Thử upload với đường dẫn chứa `..` hoặc tên không hợp lệ trên Windows → bị từ chối, không ghi file ra ngoài `uploads/<batch-id>/`.
11. Test tự động (pytest) cho: quét folder lá (các trường hợp ở §6), sinh VTT từ report mẫu (tách câu, chia thời gian, gộp cue ngắn, định dạng), làm sạch đường dẫn upload, scheduler (giới hạn 3 job, 1 slot GPU, ưu tiên yêu cầu tương tác). Toàn bộ test hiện có vẫn pass.
