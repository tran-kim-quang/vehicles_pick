# Order Vehicles Voice MVP

Python service cung cấp layer STT/TTS tái sử dụng VoiceStudio và workflow hội thoại đặt xe. Baseline VoiceStudio đã review là commit `7703ca66da77b8d130e6d1d3b2e67d979fa5a77f`.

## Cài đặt

```bash
uv sync --group dev
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
uv run ov profile show
```

Nếu bind API ra ngoài loopback, cấu hình `OV_MANAGEMENT_TOKEN`. Các lệnh mutation nhận `--token`; credential VoiceStudio chỉ nằm ở server.

Các bước chuẩn bị model, clone/preview và smoke test nằm tại [speech provider runbook](docs/runbooks/speech-provider.md). Client micro và nghiệp vụ đặt xe được mô tả trong spec và triển khai ở phase kế tiếp của plan.
