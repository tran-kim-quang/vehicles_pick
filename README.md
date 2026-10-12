# Order Vehicles Voice MVP

Python service cung cấp layer STT/TTS tái sử dụng VoiceStudio và workflow hội thoại đặt xe. Baseline VoiceStudio đã review là commit `7703ca66da77b8d130e6d1d3b2e67d979fa5a77f`.

## Cài đặt

```bash
uv sync --extra audio --group dev
cp .env.example .env
uv run ov server
```

Service mặc định lắng nghe `127.0.0.1:8000`, VoiceStudio tại `127.0.0.1:3900`. Không có model nào được tải ngầm khi khởi động hoặc khi list catalog.

## Speech management

```bash
uv run ov models list
uv run ov models install k2-fsa/OmniVoice
uv run ov voices list --language vi
uv run ov voices clone reference.wav "Giọng tổng đài" --ref-text "Xin chào"
uv run ov voices record --name "Giọng tổng đài" --seconds 8
uv run ov profile show
```

Nếu bind API ra ngoài loopback, cấu hình `OV_MANAGEMENT_TOKEN`. Các lệnh mutation nhận `--token`; credential VoiceStudio chỉ nằm ở server.

## Client micro đặt xe

Cấu hình `OV_LLM_BASE_URL` và `OV_LLM_MODEL`, sau đó chạy server và client ở hai terminal:

```bash
uv run ov server
uv run ov devices
uv run ov mic
```

Client gửi PCM16 mono 16 kHz, phát đúng sample rate server trả về, hỗ trợ barge-in bằng cách xóa audio cũ trước khi mở lượt nói mới, và chỉ ghi `outputs/<request_id>.json` sau sự kiện xác nhận. Dùng tai nghe để barge-in ổn định vì MVP chưa có acoustic echo cancellation cho loa ngoài.

Các bước chuẩn bị model, clone/preview và smoke test nằm tại [speech provider runbook](docs/runbooks/speech-provider.md).
