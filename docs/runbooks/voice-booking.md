# Voice booking runbook

## Chuẩn bị

Profile dùng large-v3: `stt.engine_id=faster-whisper`, `stt.model_id=Systran/faster-whisper-large-v3`, `language=vi`. Không dùng tên hiển thị `Whisper large-v3` làm ID. Backend chọn Faster-Whisper với cùng model trong ASR; patch WebSocket truyền language vào cả partial/final, bỏ qua sherpa khi engine được chỉ định và báo lỗi nếu engine/model thực tế không khớp.

STT tiếng Việt: project gửi `language=vi` và model ID trong WebSocket. Bản VoiceStudio gốc bỏ qua language trên đường sherpa offline; áp dụng `git -C VoiceStudio apply ../patches/voicestudio-stt-language.patch` từ thư mục repo đặt xe (chỉ cho clone chưa áp dụng patch), rồi khởi động lại VoiceStudio. Patch đặt ngôn ngữ theo từng stream Whisper, không đổi lựa chọn mặc định của phiên khác. `language=vi` trong profile TTS đơn thuần không đủ để ép STT nếu chưa có patch backend.

VoiceStudio phải chạy đúng revision đã review:

```text
7703ca66da77b8d130e6d1d3b2e67d979fa5a77f
```

Cài API và audio extra. Project này không cài PyTorch hay weights của model; inference nằm trong VoiceStudio.

```bash
uv sync --extra audio --group dev
cp .env.example .env
```

Cấu hình tối thiểu trong `.env`:

```dotenv
OV_VOICESTUDIO_URL=http://127.0.0.1:3900
OV_LLM_BASE_URL=http://127.0.0.1:11434/v1
OV_LLM_MODEL=<model-chat-completions>
OV_LLM_API_KEY=
OV_SERVICE_TIMEZONE=Asia/Ho_Chi_Minh
OV_VEHICLES_AVAILABLE=["bike","car"]
```

LLM endpoint phải tương thích `POST /chat/completions`. Không có provider mặc định: server chỉ bật voice booking khi cả URL và model đã được cấu hình. Nếu backend hỗ trợ JSON mode, đặt `OV_LLM_STRUCTURED_OUTPUT=true`; kết quả vẫn được Pydantic và domain validators kiểm tra tại service.

Với Groq, đặt `OV_LLM_BASE_URL=https://api.groq.com/openai/v1` (không thêm `/chat/completions`). LLM này vừa trích xuất dữ liệu vừa diễn đạt lời đáp: ghi nhận thông tin mới và hỏi trường còn thiếu. Domain vẫn quyết định bước hỏi/xác nhận; bản đọc lại xác nhận đầy đủ được nối vào lời dẫn của LLM. Nếu sinh lời đáp lỗi hoặc hết thời gian, bot dùng câu mẫu. Số điện thoại sai được phản hồi bằng câu sửa lỗi do domain kiểm soát. Điểm đón `pickup` và điểm đến `destination` được lưu, đọc lại và xuất JSON; khi đã cung cấp một đầu thì đầu còn lại bắt buộc. Các phiên không cung cấp tuyến đường vẫn giữ luồng cũ. Địa điểm chỉ được kiểm tra không rỗng, chưa xác minh bằng bản đồ. Khởi động lại `ov server` sau khi cập nhật mã hoặc `.env`.

## Chuẩn bị speech profile và giọng

Kiểm tra/cài model, sau đó chọn engine/model trong runtime profile:

```bash
uv run ov models list
uv run ov models install k2-fsa/OmniVoice
uv run ov models status <job-id>
uv run ov profile show
```

Có thể dùng voice clone, archetype hoặc preset mà VoiceStudio trả về. Reference clone được thu riêng, không lấy từ cuộc gọi đặt xe:

```bash
uv run ov voices record --name "Giọng hỗ trợ" --seconds 8 --ref-text "Xin chào quý khách"
uv run ov voices list --language vi
uv run ov voices from-archetype <archetype-id>
uv run ov voices preview <voice-ref> "Tôi xin xác nhận thông tin" --output preview.wav
```

VieNeu-TTS hoặc provider bên thứ ba cần adapter thực thi cùng `SpeechSynthesizer` contract. Không đưa một checkpoint `.pt` tùy ý vào VoiceStudio nếu engine không có loader tương ứng với kiến trúc đó.

## Chạy

### VieNeu Cloud TTS + VoiceStudio STT

Greeting đầu phiên: `xin chào, mình có thể giúp gì cho bạn ạ`. WAV tạo sẵn ở `assets/greeting.wav`, có thể đổi đường dẫn bằng `OV_GREETING_AUDIO_PATH`. Server nạp file lúc khởi động để phát ngay và không gọi cloud mỗi phiên; nếu chưa có file thì dùng TTS. Sau khi đổi voice, cần tạo lại greeting và khởi động lại server. PCM gửi tới client được chia thành các gói tối đa 100 ms để phù hợp hàng đợi loa hai giây.

Trong `.env`, đặt `OV_TTS_PROVIDER=vieneu`, `OV_VIENEU_MODEL=vieneu-v4`, `OV_VIENEU_VOICE=Ngọc Lan`. Key đọc từ `OV_VIENEU_API_KEY` hoặc biến hiện có `TTS_API_KEY`; URL đọc từ `OV_VIENEU_BASE_URL` hoặc `BASE_URL_TTS` (API base chính thức: `https://api.vieneu.io/api/v1`). Không đưa key vào file mẫu hoặc log. Danh sách voice: `GET https://api.vieneu.io/api/v1/voices?engine=v4`.

STT vẫn lấy từ `state/speech-profile.json`. Khi chọn VieNeu, runtime thay TTS và voice theo `.env`; voice ID VoiceStudio như `36fc8123` không dùng được cho VieNeu. Speech-management CLI vẫn quản lý VoiceStudio. Muốn quay lại TTS local, đặt `OV_TTS_PROVIDER=voicestudio` và khởi động lại server.

VieNeu trả WAV hoàn chỉnh qua `POST /api/v1/audio/speech`; adapter lấy sample rate từ header WAV rồi chuyển PCM16 cho client. Hiện chưa streaming từng phần. Thời gian chờ mặc định `OV_VIENEU_TIMEOUT_SECONDS=120`. VieNeu Cloud tính phí ký tự theo tài khoản; khóa và hạn mức do VieNeu quản lý.

Lịch đặt trước giữ giờ/phút qua các lượt; chỉ nói giờ thì phút mặc định 00 và bot hỏi ngày. Ngày không kèm tháng/năm dùng tháng/năm hiện tại theo `OV_SERVICE_TIMEZONE`. Backend từ chối lịch quá khứ và lịch thuộc tháng/năm khác. JSON xác nhận có `booking_at` dạng ISO 8601 với offset timezone: thời điểm xác nhận cho đi ngay, lịch đã kiểm tra cho đặt trước. Voice đang chọn nằm trong `state/speech-profile.json` (`voice_ref.provider_voice_id`, `voice_ref.kind`, `language`); nên cập nhật qua `ov profile set` khi server đang chạy.

Terminal server:

```bash
uv run ov server
```

Terminal client:

```bash
uv run ov devices
uv run ov mic --input-device <id> --output-device <id>
```

Trong môi trường ồn, chỉnh `--vad-threshold` hoặc dùng `--push-to-talk`. Push-to-talk dùng Enter để mở và đóng từng lượt. Dùng tai nghe khi thử barge-in; MVP chưa có acoustic echo cancellation cho loa ngoài.

Client chỉ ghi `outputs/<request_id>.json` sau khi nghe xong bản đọc lại và khách xác nhận. Kết quả phải có `status=confirmed`, `revision=confirmed_revision`, UUID hợp lệ cho từng field; `scheduled_at` không xuất khi đặt ngay. Service không gọi API tạo chuyến xe.

## Acceptance offline

```bash
uv run pytest tests/booking tests/llm tests/voice tests/client tests/integration/test_booking_acceptance.py -q
uv run pytest tests/speech -q
```

Các scenario gồm đủ field trong một lượt, đặt trước thiếu giờ, xe ngoài catalog, sửa trong lúc xác nhận, hủy, clone/archetype, timeout và race khi ngắt lời.

## Live micro và timing

Voice `design` dùng `POST /generate` với `profile_id` để backend phục hồi reference, chỉ dẫn và seed đã lưu giống giao diện. Audio được trả sau khi tạo xong toàn câu; không có streaming từng câu như `/ws/tts`. Voice clone vẫn dùng `/ws/tts`. Chọn `language=vi` để đọc tiếng Việt; mẫu thiết kế bằng tiếng Anh có thể ảnh hưởng giọng/phát âm và cần nghe đối chiếu với reference.

Chuẩn bị model STT/TTS, LLM, giọng, tai nghe và thiết bị audio. Chạy test tương tác sau, nói đủ tên, số điện thoại, đặt ngay/đặt trước và loại xe, rồi xác nhận:

```bash
RUN_LIVE_VOICE=1 \
VOICE_BOOKING_WS_URL=ws://127.0.0.1:8000/v1/voice/sessions \
VOICE_INPUT_DEVICE=<id> \
VOICE_OUTPUT_DEVICE=<id> \
uv run pytest tests/integration/test_live_voice.py -q -s
```

Lặp ba phiên liên tiếp; trong ít nhất một phiên hãy nói khi assistant đang phát để kiểm tra audio cũ dừng ngay. Đạt khi nghe được audio tiếng Việt, response cũ không phát tiếp, không có JSON trước xác nhận, và mỗi phiên tạo đúng một JSON hợp lệ.

Server và client đo bằng đồng hồ monotonic riêng. `ov mic` in timing phía client khi phiên kết thúc; endpoint sau chỉ tổng hợp timing phía server. Cả hai chỉ tính duration trong cùng process, không trừ timestamp tuyệt đối giữa hai máy:

```bash
curl http://127.0.0.1:8000/v1/voice/timings
```

Ghi p50/p95 cùng engine, model và phần cứng thực tế. Các số đo này là benchmark môi trường đang chạy, không phải SLA của upstream.
