# Thiết kế MVP hội thoại đặt xe bằng micro với Python

Ngày: 2026-10-12. Trạng thái: đã được người dùng duyệt và triển khai MVP trên nhánh `feat/voice-booking-mvp` ngày 2026-10-12; live inference/micro cần môi trường model và thiết bị để kiểm chứng.

## 1. Mục tiêu và phạm vi đã thống nhất

Người dùng giao tiếp bằng micro. Assistant dùng LLM để hiểu yêu cầu và phản hồi bằng audio. Thu thập đủ dữ liệu hợp lệ, đọc lại, nhận xác nhận rõ ràng rồi xuất JSON có UUID cho từng trường. Không tạo đơn đặt xe qua API.

- Client đầu tiên là Python dùng micro và thiết bị phát audio trên máy, không phụ thuộc trình duyệt.
- Chưa tích hợp tổng đài và chưa xác định nền tảng app đích.
- Tái sử dụng backend inference và API STT/TTS của VoiceStudio.
- Layer speech bao gồm quản lý/cài/chọn model hỗ trợ, clone giọng từ reference audio, nghe thử và chọn giọng mẫu. Chi tiết ở [thiết kế speech provider](2026-10-12-speech-provider-design.md).
- Tách client audio khỏi backend hội thoại để app khác dùng cùng giao thức sau này.
- OOP với interface, composition và dependency injection; nghiệp vụ không phụ thuộc thư viện audio hay module nội bộ VoiceStudio.
- Realtime theo lượt nói: audio truyền liên tục, phản hồi phát dần theo câu, cho phép ngắt lời. Không cam kết độ trễ trước khi đo trên model và phần cứng thực tế.

## 2. Kiến trúc

```text
PythonMicClient (micro, loa, VAD, playback)
       ⇅ WebSocket: sự kiện JSON và frame audio nhị phân
VoiceSessionService
       ├── DialogueManager → InformationExtractor / LLMAdapter
       ├── FieldRegistry / Validators / VehicleCatalog
       ├── SessionRepository / BookingSerializer
       └── SpeechRuntimeService
             ├── VoiceStudioSTTAdapter → VoiceStudio STT WebSocket
             └── VoiceStudioTTSAdapter → VoiceStudio TTS WebSocket

SpeechManagementService → ModelManager / VoiceLibrary
       └── VoiceStudioProvider → model APIs, profiles, archetypes
```

Backend Python có thể chạy cùng máy với client. VoiceStudio chạy như service riêng ở địa chỉ cấu hình, cùng máy hoặc máy inference khác. Không sao chép code model vào project đặt xe.

## 3. Thành phần OOP

| Thành phần | Trách nhiệm và contract |
| --- | --- |
| `AudioInput` / `AudioOutput` | Interface cho frame đầu vào, phát đầu ra, dừng và xóa buffer. Implementation Python dùng sounddevice. |
| `PythonMicClient` | Quản lý thiết bị, giao thức phiên và callback audio; không xử lý nghiệp vụ đặt xe. |
| `VoiceActivityDetector` | Interface xác định lời nói và khoảng im lặng; có thể thay implementation. |
| `TurnController` | Mở/kết thúc lượt nói, pre-roll, giới hạn thời lượng, ngắt phản hồi và kiểm tra lượt hiện tại. |
| `VoiceSessionService` | Điều phối I/O và lifecycle tác vụ bất đồng bộ cho từng phiên. |
| `SpeechRecognizer` | Chuyển stream audio thành transcript; implementation là `VoiceStudioSTTAdapter`. |
| `SpeechSynthesizer` | Trả metadata định dạng và frame audio; implementation là `VoiceStudioTTSAdapter`. |
| `SpeechRuntimeService` | Resolve cấu hình speech của phiên, kiểm tra capabilities, điều phối STT/TTS; không xử lý nghiệp vụ đặt xe. |
| `SpeechManagementService` | Cài/chọn model được hỗ trợ, quản lý clone và giọng mẫu, nghe thử, cập nhật defaults. |
| `ModelManager` / `VoiceLibrary` | Interface quản lý model và giọng; implementation gọi API VoiceStudio. |
| `LLMAdapter` | Giao tiếp với LLM cấu hình qua endpoint, model và credentials. |
| `InformationExtractor` | Trích xuất patch dữ liệu và intent từ lời người dùng; patch phải qua schema và validators. |
| `DialogueManager` | Áp dụng patch hợp lệ, xác định trường thiếu, câu hỏi tiếp theo và bước xác nhận. |
| `FieldRegistry` | Định nghĩa kiểu dữ liệu, điều kiện bắt buộc, validator và cách hỏi. |
| `VehicleCatalog` | Danh sách mã xe hợp lệ và alias cấu hình. |
| `SessionRepository` | Lưu phiên và cập nhật theo revision; bản MVP dùng implementation bộ nhớ cho một worker. |
| `BookingSerializer` | Xuất một kết quả hoàn tất từ revision đã xác nhận; không tự suy luận dữ liệu. |

Các interface được inject qua constructor. Không dùng singleton giữ dữ liệu của tất cả khách hoặc tạo cây kế thừa nhiều tầng.

## 4. Client Python và audio

Python client sử dụng `sounddevice.RawInputStream` và `RawOutputStream`. Callback thiết bị chỉ đưa/lấy audio qua buffer giới hạn kích thước; không gọi HTTP, WebSocket, LLM hoặc inference trong callback.

- Đầu vào chuẩn của giao thức MVP: PCM signed 16-bit little-endian, mono, 16 kHz. Nếu thiết bị không hỗ trợ, thu ở sample rate được hỗ trợ rồi chuyển đổi trước khi gửi.
- Đầu ra: PCM16 mono; sample rate lấy từ metadata TTS, không hardcode 24 kHz cho `/ws/tts`. Chuyển đổi nếu thiết bị phát không hỗ trợ sample rate thực tế.
- Frame đầu vào mục tiêu 20 ms; được ghép/chia lại để phù hợp backend. Buffer hữu hạn; overflow làm lượt hiện tại không hợp lệ và yêu cầu nói lại, không âm thầm bỏ mẫu rồi chấp nhận thông tin sai.
- Client có chọn thiết bị, bắt đầu phiên, kết thúc phiên và ngắt phản hồi thủ công. CLI hiển thị transcript, trạng thái và đường dẫn JSON.
- VAD MVP dựa trên năng lượng với ngưỡng cấu hình/hiệu chỉnh tiếng nền. Pre-roll 200 ms, mở lượt sau 200 ms lời nói, kết thúc sau 700 ms im lặng, tối đa 30 giây/lượt; các giá trị là mặc định để thử nghiệm và điều chỉnh.
- Dùng tai nghe cho phép thử ngắt lời của MVP. Loa ngoài có thể gây tiếng vọng; chưa coi full-duplex qua loa ngoài là tiêu chí đạt khi chưa có implementation AEC và kiểm thử phù hợp. Có chế độ nhấn để nói/ngắt thủ công khi VAD không phù hợp môi trường.

## 5. Giao thức phục vụ app sau này

Endpoint của project: `WS /v1/voice/sessions`. Đây là API dự kiến xây dựng của project đặt xe, không phải API đã tồn tại trong VoiceStudio.

1. Client gửi `session.start` với phiên bản giao thức và định dạng input; backend trả `session.started` và `session_id`.
2. Client tạo `turn_id` UUID cho từng lượt, gửi `input_audio.start`, frame nhị phân rồi `input_audio.end`.
3. Audio đầu vào trên một socket chỉ thuộc một lượt đang mở. Bản MVP không có nhiều lượt input chồng nhau trên cùng socket.
4. Backend trả `transcript.partial`, `transcript.final`, `assistant.text`, `assistant.audio.start`, các frame audio rồi `assistant.audio.end`.
5. Client gửi `assistant.playback.finished` khi đã phát hết buffer. Kết thúc truyền audio không đồng nghĩa kết thúc phát trên thiết bị.
6. Client gửi `response.interrupt` khi bắt đầu nói trong lúc assistant đang phát; dừng/xóa playback ngay tại client, không chờ backend xác nhận.
7. Kết quả cuối là `booking.confirmed` chứa JSON; các trạng thái khác qua `session.state`, lỗi qua `error`, đóng phiên qua `session.end`.

Mọi sự kiện sau handshake có `session_id`. Sự kiện theo lượt có `turn_id`; câu trả lời có `response_id`. Backend và client giữ response đang hiệu lực. Frame audio đi theo metadata response vừa mở, không trộn hai response. Khi ngắt, kết quả cũ bị bỏ; audio đã xếp hàng của response cũ phải được drain hoặc đóng socket trước khi mở luồng mới để không gán nhầm bytes cho response mới.

## 6. Tích hợp VoiceStudio đã kiểm tra

Baseline source: commit `7703ca66da77b8d130e6d1d3b2e67d979fa5a77f`. Chọn và pin revision triển khai sau khi kiểm thử contract thực tế.

- STT: `WS /v1/audio/transcriptions/stream?pcm=1&sr=16000`; gửi binary PCM và `{"type":"input_audio.end"}` khi hết lượt.
- Mỗi lượt nghiệp vụ dùng một session STT riêng. Chốt từ `final_kind=summary`; không áp dụng lại `utterance` và `summary` như hai lượt khác nhau. Không giả định socket tồn tại sau final; mở socket mới cho lượt tiếp theo.
- Partial chỉ hiển thị. Trường `refined_text` nếu có không được tự động thay thế transcript dùng để trích xuất tên/số điện thoại; dữ liệu quan trọng được đọc lại cho khách xác nhận.
- STT HTTP `POST /v1/audio/transcriptions` dùng cho kiểm thử file/recovery; không sử dụng `stream=true` trên route này.
- TTS: `WS /ws/tts`, request có `text`, `voice`, `engine`, `language`. Đọc event `start` để biết sample rate/channels/format, sau đó audio binary và `done` hoặc `error`; xử lý cả event `routing`.
- TTS WebSocket hiện tổng hợp theo câu rồi phát chunks; HTTP `POST /v1/audio/speech` hiện tổng hợp/encode hoàn chỉnh trước khi trả chunks.
- Phản hồi tự nhiên của LLM được gom thành câu hoàn chỉnh và đưa vào hàng đợi TTS tuần tự, không gửi mỗi token và không chạy nhiều job TTS đồng thời cho một response.
- Khi ngắt, hủy consumer, đóng stream TTS hiện tại và bỏ response cũ. Không giả định đóng socket bảo đảm dừng ngay computation đang chạy trên GPU.
- Tham số URL, credential, engine, voice và language nằm trong cấu hình. Model STT/TTS phải được nạp sẵn và kiểm tra tiếng Việt trước phiên thử.
- Phiên pin snapshot cấu hình speech khi bắt đầu. STT streaming có selection/capture path riêng cần kiểm tra; không mặc định model batch được chọn sẽ là model của socket. Defaults chỉ đổi cho phiên mới; thay selection global khi worker hết phiên, hoặc dùng worker pool tương thích. Xem [speech provider](2026-10-12-speech-provider-design.md) cho giới hạn cài model, profile cloning, giọng mẫu và lifecycle tài nguyên.

## 7. Dữ liệu và kiểm tra

Bốn nhóm nghiệp vụ được biểu diễn thành năm trường:

| Trường | Bắt buộc | Quy tắc |
| --- | --- | --- |
| `name` | Luôn | Tên không rỗng do khách cung cấp; không đoán tên. |
| `phone` | Luôn | Chuẩn hóa theo thị trường cấu hình; mặc định MVP Việt Nam. Đọc lại chữ số để xác nhận. |
| `booking_mode` | Luôn | `now` hoặc `scheduled`. |
| `scheduled_at` | Khi `scheduled` | ISO 8601 có offset, đủ ngày và giờ, phải ở tương lai. Múi giờ phục vụ MVP `Asia/Ho_Chi_Minh`, cấu hình được. |
| `vehicle_type` | Luôn | Mã chuẩn phải thuộc snapshot `vehicles_available` của phiên. |

Alias xe là bảng cấu hình rõ ràng: ví dụ `ô tô` → `car`, `xe máy` → `bike`. Không tự động suy diễn lựa chọn mơ hồ. Lựa chọn không thuộc danh sách làm trường này thiếu/chưa hợp lệ; hỏi lại và nêu các lựa chọn hợp lệ. Danh sách rỗng chuyển sang `NEEDS_ASSISTANCE`, không hỏi lặp vô hạn.

Danh sách xe mặc định cho demo là `["bike", "car"]`, có thể thay bằng cấu hình. Đây là loại xe được phép chọn, không đại diện tồn kho xe thực tế.

LLM chỉ tạo patch và intent. Code kiểm tra schema, giá trị và điều kiện bắt buộc. Không lấy dữ liệu do assistant nói làm dữ liệu khách đã cung cấp. Một lượt có thể cập nhật nhiều trường. Trường có thay đổi chưa hợp lệ không được giữ giá trị cũ như thể khách chưa sửa; phải đánh dấu cần làm rõ.

Ngày tương đối dựa trên thời điểm lượt nói trong múi giờ phục vụ. Nếu thiếu ngày hoặc giờ, giờ sáng/tối mơ hồ hoặc kết quả ở quá khứ, hỏi rõ. Không tự chọn ngày mới. Khi khách đổi sang `now`, `scheduled_at` được loại khỏi JSON cuối; giữ lịch sử trong phiên nếu cần.

## 8. Trạng thái, xác nhận và UUID

Trạng thái nghiệp vụ: `COLLECTING`, `AWAITING_CONFIRMATION`, `COMPLETED`, `CANCELLED`, `NEEDS_ASSISTANCE`. Trạng thái audio như listening/thinking/speaking tách riêng, không dùng thay trạng thái nghiệp vụ.

1. Thu patch từ lượt nói đã chốt và kiểm tra.
2. Hỏi một trường còn thiếu hoặc mơ hồ; không hỏi lại trường hợp lệ đã có.
3. Khi đủ dữ liệu, dựng bản đọc lại từ snapshot đã kiểm tra bằng template, không để LLM tự viết lại số/ngày/xe.
4. Ghi `pending_confirmation_revision`. Chỉ mở nhận xác nhận sau khi client báo đã phát hết bản đọc lại và câu hỏi xác nhận.
5. Lời đồng ý mơ hồ hoặc không rõ ngữ cảnh cần hỏi lại. Nếu lời khách vừa đồng ý vừa sửa thông tin, xử lý sửa và đọc lại; không xuất JSON.
6. Mọi thay đổi dữ liệu làm mất hiệu lực xác nhận đang chờ. Chỉ hoàn tất khi xác nhận đúng revision và kiểm tra lại toàn bộ dữ liệu, gồm thời điểm đặt trước vẫn ở tương lai.

`request_id`, `session_id` và UUID các trường độc lập. UUID trường tạo khi lần đầu ghi nhận hợp lệ và giữ ổn định khi sửa; `revision` tăng khi giá trị hoặc hiệu lực dữ liệu thay đổi. Không tạo UUID mới cho mỗi partial. Trường đặt trước không áp dụng thì không xuất. Final JSON có `schema_version`, `request_id`, `status=confirmed`, `revision`, `confirmed_revision`, `timezone`, `fields`; mỗi trường có `{id, value}`.

Sau xác nhận, CLI nhận sự kiện kết quả và lưu vào `outputs/<request_id>.json` bằng ghi nguyên tử; serializer phát kết quả một lần theo request/revision. Không phát âm rằng chuyến xe đã được tạo. Đóng phiên khi đã bàn giao JSON và phát xong lời kết thúc.

## 9. Lỗi và mở rộng

- Audio không có lời hoặc transcript rỗng: yêu cầu nói lại, không cập nhật dữ liệu.
- Sau ba lần liên tiếp không làm rõ được cùng trường: chuyển `NEEDS_ASSISTANCE`, không xuất kết quả hoàn tất.
- LLM trả schema sai: thử trích xuất lại một lần; vẫn sai thì trả lỗi có thể phục hồi, không áp dụng patch.
- STT/TTS mất kết nối hoặc timeout: tác vụ có timeout hữu hạn cấu hình; lượt thất bại không được coi là thành công. Có thể mở kết nối mới sau khi kết quả cũ đã bị vô hiệu.
- TTS quá tải: không tự xếp hàng nhiều phút hoặc retry vô hạn; báo trạng thái, cho thử lại/ngắt phiên. Không phát trùng audio đã phát.
- Mất socket client: ngừng tác vụ và giữ bản nháp trong bộ nhớ tối đa 15 phút. MVP một worker không bảo đảm phục hồi sau restart; không tự hoàn tất phiên mất kết nối.
- Mỗi session có hàng đợi và điều phối tuần tự; các session khác nhau độc lập. Khi mở rộng nhiều worker, thay repository bằng kho dùng chung có compare-and-set revision và định tuyến socket theo phiên.
- Worker voice/GPU mở rộng độc lập; giới hạn concurrency theo khả năng đo được. Không suy luận rằng async đồng nghĩa GPU xử lý song song không giới hạn.

## 10. Kiểm chứng và tiêu chí đạt

Kiểm thử nghiệp vụ không cần model: đủ dữ liệu một lượt; thiếu từng trường; xe không hợp lệ và danh sách rỗng; đặt ngay/đặt trước; thời gian mơ hồ/quá khứ; sửa khi xác nhận; giữ UUID khi sửa; không xuất JSON trước xác nhận hoặc xác nhận revision cũ.

Kiểm thử protocol bằng adapter giả: partial/final trùng; socket đóng sau summary; metadata TTS có sample rate khác 24 kHz; kết quả cũ sau ngắt; audio backlog; báo playback xong; timeout và overflow.

Kiểm thử thực tế bằng micro và VoiceStudio: tiếng Việt, tên riêng, đọc số điện thoại, nhiều lượt liên tiếp, ngắt lời bằng tai nghe, kết thúc phiên và JSON hợp lệ. Đo thời gian từ hết lời đến transcript final, LLM, first audio và bắt đầu phát thực tế; báo p50/p95, thời gian chờ inference và model/phần cứng dùng để đo. Chưa đặt SLA tốc độ hay số session hỗ trợ trước benchmark.

## 11. Tài liệu nguồn

- VoiceStudio provider: https://github.com/debpalash/VoiceStudio/blob/7703ca66da77b8d130e6d1d3b2e67d979fa5a77f/docs/agentic-voice.md
- STT protocol: https://github.com/debpalash/VoiceStudio/blob/7703ca66da77b8d130e6d1d3b2e67d979fa5a77f/docs/speech-platform.md
- TTS WS implementation: https://github.com/debpalash/VoiceStudio/blob/7703ca66da77b8d130e6d1d3b2e67d979fa5a77f/backend/api/routers/tts_stream.py
- STT implementation: https://github.com/debpalash/VoiceStudio/blob/7703ca66da77b8d130e6d1d3b2e67d979fa5a77f/backend/api/routers/capture_ws.py
- HTTP audio implementation: https://github.com/debpalash/VoiceStudio/blob/7703ca66da77b8d130e6d1d3b2e67d979fa5a77f/backend/api/routers/openai_compat.py
- Python sounddevice: https://python-sounddevice.readthedocs.io/en/latest/
- Raw audio streams: https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html
