# Voice booking runbook

## Chuẩn bị

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
