# Speech provider runbook

## Điều kiện

- Chạy VoiceStudio từ revision `7703ca66da77b8d130e6d1d3b2e67d979fa5a77f`.
- Chuẩn bị Python API bằng `uv sync --group dev`; không cài PyTorch vào môi trường này.
- Đặt `OV_VOICESTUDIO_URL` về service root, không thêm `/v1`.
- Khi API không chỉ bind loopback, đặt `OV_MANAGEMENT_TOKEN` và truyền token vào CLI.

## Chuẩn bị model

Kiểm tra catalog trước. Lệnh list chỉ đọc trạng thái và không tải weights.

```bash
uv run ov models list
uv run ov models install k2-fsa/OmniVoice
uv run ov models status <job-id>
```

Model chỉ được cài từ catalog được hỗ trợ với `target=local` trên máy chạy VoiceStudio. File `.pt` không phải định dạng import chung; model mới cần adapter/loader đúng kiến trúc.

## Clone và preview

Reference nên là audio sạch, đúng extension WAV/FLAC/MP3/OGG/M4A, tối đa 20 MiB. Clone là profile conditioning, không phải job train.

```bash
uv run ov voices clone ./reference.wav "Giọng hỗ trợ" --ref-text "Xin chào quý khách"
uv run ov voices list --language vi
uv run ov voices preview <voice-ref> "Tôi xin xác nhận thông tin" --output preview.wav
```

Sample rate của preview lấy từ metadata TTS. Không phát file ở sample rate mặc định 24 kHz nếu header trả giá trị khác.

## Smoke test thật

Smoke test không chạy mặc định và không tải model. Sau khi model đã sẵn sàng:

```bash
RUN_SPEECH_SMOKE=1 \
VOICESTUDIO_REVISION=7703ca66da77b8d130e6d1d3b2e67d979fa5a77f \
SPEECH_REFERENCE_AUDIO=./reference.wav \
uv run pytest tests/integration/test_speech_smoke.py -q
```

Ghi lại engine/model, GPU/CPU, sample rate thực tế và thời gian clone/first audio. Nếu thiếu prerequisite, smoke test phải fail rõ ràng; kết quả unit test không được dùng để tuyên bố inference hoặc tiếng Việt đã hoạt động.
