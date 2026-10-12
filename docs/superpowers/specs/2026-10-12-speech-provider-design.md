# Layer speech tái sử dụng VoiceStudio

Ngày: 2026-10-12. Trạng thái: đã được người dùng duyệt ngày 2026-10-12; chưa triển khai.

## 1. Mục tiêu

Tái sử dụng backend STT/TTS, model lifecycle, voice profiles và catalog giọng mẫu của VoiceStudio. Project đặt xe cung cấp layer OOP có contract ổn định để client Python và app tương lai sử dụng. Bao gồm cài/chọn model được hỗ trợ, tạo giọng clone từ audio hoặc bản ghi micro, nghe thử và chọn giọng. Logic đặt xe không phụ thuộc provider.

Thiết kế này bổ sung bản `2026-10-12-voice-booking-design.md`; không thay quy tắc thu thập dữ liệu, đối chiếu xe, xác nhận và UUID. LLM xử lý hội thoại là dependency riêng, không dùng STT/TTS thay cho LLM.

## 2. Hướng tái sử dụng

Chạy backend VoiceStudio như service riêng. Project sử dụng HTTP/WebSocket qua adapter; không fork toàn bộ app desktop và không sao chép inference/model loaders vào nghiệp vụ đặt xe.

So sánh phương án:

| Phương án | Phạm vi tái sử dụng | Đánh giá |
| --- | --- | --- |
| Adapter gọi backend VoiceStudio — lựa chọn | Inference, model install/cache, profile CRUD, archetypes, streaming | Ít code cần viết, đổi provider được, model lifecycle do service quản lý. |
| Import module nội bộ VoiceStudio | Class STT/TTS trực tiếp trong process project | Phụ thuộc cấu trúc import, global prefs và dependencies inference; nâng cấp khó hơn. |
| Fork app VoiceStudio | Backend và UI desktop | Phạm vi bảo trì lớn hơn nhu cầu client Python/module tích hợp. |

Baseline kiểm tra source: `7703ca66da77b8d130e6d1d3b2e67d979fa5a77f`. Pin revision triển khai và chạy contract tests trước khi nâng cấp.

## 3. Ranh giới layer

```text
Client Python / app tương lai
            ↓ API project
SpeechRuntimeService        SpeechManagementService
 STT, TTS, interrupt         model, voice, clone, preview, defaults
            ↓                        ↓
SpeechRecognizer             ModelManager / VoiceLibrary
SpeechSynthesizer            RuntimeProfileRepository
            ↓                        ↓
VoiceStudioProvider (HTTP/WebSocket + DTO mapping + errors)
            ↓
Backend VoiceStudio: inference, engine registry, cache, profiles, archetypes
```

Runtime và management có timeout, hàng đợi và quyền truy cập riêng. Job cài model hoặc preview không chạy trong callback micro hay chặn event loop của hội thoại.

## 4. Contract OOP

| Interface / service | Thao tác |
| --- | --- |
| `SpeechRecognizer` | `transcribe(audio, config)`, `stream_transcribe(frames, config)` |
| `SpeechSynthesizer` | `synthesize(text, config)`, `stream_synthesize(text, config)` |
| `ModelManager` | `list_engines()`, `list_models()`, `install_model(repo_id)`, `installation_status()`, `watch_installation()`, `cancel_install(repo_id)`, `select_model(selection)` |
| `VoiceLibrary` | `list_voices(filter)`, `create_clone(reference, metadata)`, `update_voice(id, patch)`, `preview_voice(id, text, engine)`, `use_archetype(id)`, `delete_voice(id)` |
| `RuntimeProfileRepository` | Lưu profile cấu hình mặc định và revision; không sao chép audio/model weights. |
| `SpeechRuntimeService` | Kiểm tra profile, mở runtime, trả metadata/audio/transcript; không hiểu trường đặt xe. |
| `SpeechManagementService` | Điều phối quản lý, kiểm tra capabilities, chuẩn hóa trạng thái job, cập nhật defaults. |
| `VoiceStudioProvider` | Adapter triển khai các interface provider, giao tiếp upstream, ánh xạ dữ liệu/lỗi. |

Ví dụ cấu trúc code dự kiến: `speech/domain/` cho DTO/interface; `speech/application/` cho service; `speech/providers/voicestudio/` cho adapters; `speech/api/` cho route; `clients/python/` cho micro/playback và CLI quản lý. Composition/injection qua constructor, không tạo base class chứa toàn bộ nghiệp vụ provider.

## 5. DTO và định danh

- `EngineInfo`: provider_id, engine_id, family (`stt`/`tts`), availability, supported_languages, capabilities và availability_reason.
- `ModelInfo`: provider_id, model_id/repo_id, compatible_engine_ids, installation_state, runtime_dependency_state. Phân biệt engine dependencies, weights đã cài và model đã được nạp; không gọi mọi trạng thái này là ready.
- `VoiceInfo`: provider_id, provider_voice_id, kind (`clone`, `design`, `archetype`, `engine_preset`, `alias`), name, language, compatibility, preview_state.
- `RuntimeProfile`: profile_id UUID của project, revision, provider_id, endpoint_ref, STT route/engine/model selection, TTS engine/model selection, voice reference, language, speed, capability fingerprint.
- `ManagementJob`: job_id UUID của project, upstream repo/task reference, operation, status và progress. Trạng thái project: `queued`, `running`, `succeeded`, `failed`, `cancel_requested`, `cancelled`; không báo cancelled khi upstream chưa dừng.

ID của VoiceStudio được giữ như opaque string: profile hiện có thể chỉ là chuỗi tám ký tự, không ép thành UUID. UUID trường đặt xe vẫn độc lập. VoiceStudio là nguồn dữ liệu chính cho models và voice profiles; project chỉ lưu runtime defaults, mapping/job references và cache metadata có thể làm mới.

## 6. API VoiceStudio đã kiểm tra

Các path sau nằm ở service root, không tự thêm `/v1` cho route management:

| Chức năng | API upstream |
| --- | --- |
| Engine STT/TTS và capabilities | `GET /engines/asr`, `GET /engines/tts` |
| Danh mục weights và trạng thái cài | `GET /models` |
| Cài/hủy/trạng thái model | `POST /models/install`, `POST /models/install/cancel`, `GET /models/install/status` |
| Tiến độ cài model | SSE `GET /setup/download-stream` |
| Chọn backend/model hỗ trợ | `POST /engines/select` với `family`, `backend_id`, và `model_id` khi engine hỗ trợ |
| Model cho live dictation | `GET /dictation/models`; cấu hình qua `POST /dictation/prefs` hoặc query `model=<id>` trên STT socket đối với model Sherpa được hỗ trợ |
| Liệt kê/tạo profile | `GET /profiles`, multipart `POST /profiles` |
| Sửa/xóa profile | `PUT /profiles/{id}`, `DELETE /profiles/{id}` |
| Catalog giọng mẫu | `GET /archetypes` có tìm kiếm, bộ lọc và phân trang |
| Nghe thử giọng mẫu | `GET /archetypes/{id}/preview/state`, `GET /archetypes/{id}/preview` |
| Dùng giọng mẫu | `POST /archetypes/{id}/use` trả `profile_id` |
| Profiles/alias phục vụ API speech | `GET /v1/audio/voices` |
| Inference STT/TTS | STT `WS /v1/audio/transcriptions/stream`; TTS `WS /ws/tts`; HTTP `/v1/audio/transcriptions`, `/v1/audio/speech` |

Một số thao tác upstream yêu cầu admin/server configuration, một số route dictation hoặc runtime installation chỉ dùng local. Adapter kiểm tra khả năng deployment và trả trạng thái khả dụng; không giả định cứ gửi API key là vượt được mọi local-only gate. Model weights install với `target=local` là cài trên máy chạy VoiceStudio, không phải máy client. Với deployment từ xa cần thao tác local-only, dùng worker management trên chính host VoiceStudio; không bỏ access checks upstream. Client nhận API project và không giữ credential quản trị VoiceStudio.

## 7. Thêm model

MVP hỗ trợ cài checkpoint nằm trong `GET /models`: chọn catalog entry → cài → theo dõi tiến độ → kiểm tra weights/dependencies → chọn selection phù hợp → warmup và smoke test → lưu runtime profile.

`POST /models/install` hiện kiểm tra `KNOWN_MODELS` và từ chối repo không thuộc catalog. Không thiết kế nút nhận bất kỳ file model/repo HF rồi khẳng định sẽ chạy được.

Có hai đường mở rộng:

1. Checkpoint mới có kiến trúc tương thích: dùng configuration/selection mà engine đó hỗ trợ; phần chọn `model_id` không có contract chung cho mọi engine. Nếu cần model mới ngoài catalog, mở rộng catalog/loader của provider và kiểm thử riêng.
2. Engine/kiến trúc mới: viết backend tương thích `TTSBackend` hoặc `ASRBackend` trong provider, đăng ký metadata/capabilities, dependencies và contract tests. Đây là extension có code, không chỉ upload weights.

Model selection cho live STT phải bám route capture thực tế. `/engines/asr` và engine của batch transcription không tự bảo đảm model của dictation/capture socket. Adapter ghi route purpose và xác minh engine/model thực tế; nếu route fallback/demote, báo selection degraded hoặc từ chối policy yêu cầu exact model.

## 8. Clone giọng mới

Client chọn file audio hoặc ghi clip micro riêng, nhập name/language và transcript tham chiếu nếu có. Recording clone là thao tác management riêng, không tự lấy lời khách đang đặt xe làm giọng mới.

Luồng: kiểm tra input/audio và engine hỗ trợ cloning → multipart `POST /profiles` với `kind=clone`, `name`, `ref_audio`, `ref_text`, `language` → nhận provider profile ID → tổng hợp câu thử qua cùng engine TTS → nghe thử → chọn làm mặc định hoặc gán cho phiên mới.

Backend profile upload lưu reference conditioning; không mặc định là train/fine-tune model mới. `GET /profiles/{id}/audio` có thể chỉ trả reference/sample; nghe reference không thay thế phép thử synthesize giọng clone. Khi chưa có transcript, VoiceStudio có thể dùng ASR đã cài để tạo; adapter vẫn cho nhập/sửa transcript.

Kiểm tra `supports_cloning` trên engine/model đang chọn và hỗ trợ ngôn ngữ; không tự đổi sang giọng default khi cloning không hỗ trợ. Thời lượng/chất lượng reference theo metadata và yêu cầu engine, không áp một con số cho tất cả engine.

## 9. Giọng mẫu và chọn giọng

VoiceLibrary gom bốn nguồn vào DTO nhưng giữ rõ kind:

- Profiles đã lưu: clone/design, chọn bằng provider ID ổn định.
- Archetypes: tìm/lọc/nghe thử; gọi `/use` để tạo hoặc tái sử dụng profile rồi dùng profile ID cho TTS. Archetype recipe chưa materialize không tự được coi là một voice ID trực tiếp của `/ws/tts`.
- Engine presets: danh sách phụ thuộc engine. Provider sử dụng descriptor được kiểm tra cho các engine hỗ trợ; không giả định có endpoint public chung liệt kê đầy đủ mọi preset. Khi metadata chưa có, chỉ cung cấp default và các lựa chọn đã xác minh.
- Alias như `alloy`: metadata API nói chúng ánh xạ về default voice của active engine. Không hiển thị như nhiều danh tính giọng khác nhau và không dùng alias làm khóa cho saved profile có tên trùng.

Chọn giọng phải kiểm tra tương thích engine/model/ngôn ngữ. Preview câu tiếng Việt dùng đúng engine, model và voice dự định chạy. Đổi voice bằng runtime profile cho phiên mới; MVP không đổi giữa một response đang phát.

## 10. Cấu hình runtime và concurrency

Khi mở hội thoại, resolve và pin snapshot `RuntimeProfile`. Management cập nhật defaults chỉ áp dụng phiên mới. Cấu hình lưu STT selection và TTS selection riêng; voice selection không làm thay đổi model STT.

VoiceStudio có các lựa chọn backend/prefs toàn process. Không gọi `/engines/select` cho mỗi lượt hoặc đồng thời mỗi session. MVP một worker voice sử dụng một selection STT/TTS chung đã kiểm chứng; engine/model/profile thay đổi có ảnh hưởng global được thực hiện khi worker hết phiên đang chạy. Vô hiệu hóa xóa voice/model đang được lease bởi runtime; chỉ xóa khi không còn phiên/job sử dụng.

Scale thành worker pools theo deployment/engine/model; router gán session vào worker tương thích. TTS có tham số engine per request nhưng không coi điều đó là bảo đảm cùng một process có thể giữ mọi model đồng thời. Model load, install và preview có concurrency giới hạn riêng.

## 11. API project và client MVP

Các endpoint sau là contract dự kiến xây mới của project, không phải API VoiceStudio:

- `GET /v1/speech/engines`, `GET /v1/speech/models`.
- `POST /v1/speech/model-jobs`, `GET /v1/speech/jobs/{id}`, `POST /v1/speech/jobs/{id}/cancel`.
- `GET /v1/speech/voices`, multipart `POST /v1/speech/voices/clone`.
- `POST /v1/speech/voices/from-archetype`, `POST /v1/speech/voices/{id}/preview`.
- `PATCH /v1/speech/voices/{id}`, `DELETE /v1/speech/voices/{id}`; ID ở API project là provider-scoped reference được encode an toàn.
- `GET /v1/speech/runtime-profile`, `PUT /v1/speech/runtime-profile`.
- Voice runtime đặt xe tiếp tục dùng `WS /v1/voice/sessions`.

CLI Python MVP có thao tác list/install/status/cancel model, list/filter/preview voices, record/upload clone, select runtime profile và bắt đầu hội thoại micro. Các thao tác gọi service management chung; không dựa vào UI Electron hoặc trình duyệt. Không tự download weights chỉ vì liệt kê catalog.

## 12. Kiểm chứng

Contract tests bằng fake upstream cho catalog, DTO, multipart clone, SSE progress, cancel_requested, profile CRUD và lỗi unsupported capability. Kiểm tra alias không biến thành nhiều giọng giả, archetype phải resolve profile, upstream ID không bị ép thành UUID, local-only error, STT route selection khác batch, env override, unknown model và giữ lease khi đổi defaults/xóa tài nguyên.

Integration smoke tests với revision pin: cài một model được catalog hỗ trợ, đọc lại selection thực tế, tạo clone bằng reference, synthesize preview, chọn archetype, mở session dùng giọng đã chọn. Xác minh metadata sample rate và khả năng tiếng Việt bằng audio thật. Download/training/hardware benchmark không được mô phỏng là đã chạy khi chỉ kiểm tra source.

## 13. Nguồn đã đọc

- Model catalog/install: `backend/api/routers/setup/models.py`, `backend/api/routers/setup/download.py`.
- Engine selection/capabilities: `backend/api/routers/engines.py`, `backend/services/tts_backend.py`, `backend/services/asr_backend.py`.
- Voice profiles: `backend/api/routers/profiles.py`.
- Archetypes: `backend/api/routers/archetypes.py`.
- Voice aliases: `backend/api/routers/openai_compat.py`.
- Capture model selection: `backend/api/routers/capture_ws.py`, `backend/api/routers/dictation.py`.
- Deployment access: `backend/api/dependencies.py`.

Các file trên thuộc https://github.com/debpalash/VoiceStudio/tree/7703ca66da77b8d130e6d1d3b2e67d979fa5a77f.
