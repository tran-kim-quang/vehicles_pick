# Voice Booking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Client Python giao tiếp bằng micro/loa với LLM qua STT/TTS, thu thập đủ dữ liệu đặt xe, xác nhận rồi xuất JSON có UUID từng trường.

**Architecture:** Domain booking giữ field registry, validators, revisions và state machine, độc lập audio/network. VoiceSessionService nối STT summary → LLM extraction → DialogueManager → TTS, dùng generation IDs và playback acknowledgment để xử lý ngắt lời và xác nhận. Client Python giữ audio devices, VAD, bounded buffers và xuất file kết quả.

**Tech Stack:** Package Python 3.12 từ plan speech-provider; FastAPI/Pydantic 2, HTTPX, websockets asyncio; phonenumbers cho validator; sounddevice/NumPy/soxr extra `audio`; pytest/pytest-asyncio. LLM qua HTTP endpoint tương thích chat completions, endpoint/model/credential cấu hình; fake LLM cho kiểm thử không có model. Không cần trình duyệt hoặc tổng đài.

**Spec:** [voice-booking-design](../specs/2026-10-12-voice-booking-design.md), [speech-provider-design](../specs/2026-10-12-speech-provider-design.md).

**Prerequisite:** [speech-provider plan](2026-10-12-speech-provider.md) cung cấp DTO, runtime service, management CLI và app composition. Domain Tasks 1–3 có thể dùng fake dependencies; hoàn tất Task 4 trở đi cần interfaces speech ổn định.

## Global Constraints

- Chỉ xuất JSON sau xác nhận; không tạo đơn đặt xe qua API.
- Bốn nhóm nghiệp vụ: `name`, `phone`, `booking_mode`, `scheduled_at` có điều kiện, `vehicle_type`.
- `booking_mode` là `now` hoặc `scheduled`; `scheduled_at` bắt buộc khi scheduled và phải ở tương lai.
- Múi giờ phục vụ MVP `Asia/Ho_Chi_Minh`, cấu hình được; danh sách xe demo `["bike", "car"]`.
- UUID trường tạo lần đầu ghi nhận hợp lệ, giữ khi sửa; UUID session/request độc lập; revision tăng khi giá trị/hiệu lực thay đổi.
- Trạng thái nghiệp vụ: `COLLECTING`, `AWAITING_CONFIRMATION`, `COMPLETED`, `CANCELLED`, `NEEDS_ASSISTANCE`.
- Chỉ commit transcript `final_kind=summary`; partial/utterance không tự thành lượt nghiệp vụ.
- Input PCM signed 16-bit little-endian, mono, 16 kHz; frame mục tiêu 20 ms; output lấy format/sample rate từ metadata.
- VAD: pre-roll 200 ms, mở sau 200 ms speech, đóng sau 700 ms silence, tối đa 30 giây/lượt.
- Micro callback không gọi network/LLM/inference; overflow làm lượt không hợp lệ, không âm thầm drop rồi chấp nhận.
- Đọc lại từ dữ liệu đã kiểm tra; xác nhận chỉ mở sau `assistant.playback.finished` cho response tương ứng.
- Sau ba lần liên tiếp không làm rõ được cùng trường: `NEEDS_ASSISTANCE`; extraction schema lỗi retry một lần.
- Mất socket: ngừng task, giữ draft tối đa 15 phút trong bộ nhớ một worker; không tự hoàn tất.
- Tai nghe cho phép thử barge-in; chưa coi AEC qua loa ngoài là khả năng đã triển khai.
- Final file `outputs/<request_id>.json` ghi nguyên tử, một kết quả theo request/revision.

## Review Focus

1. Khách đồng ý và sửa số/xe trong cùng lời nói: sửa ưu tiên, phải đọc lại — Task 2.
2. Thời điểm đặt trước đã qua khi khách xác nhận lâu: kiểm tra lại và hỏi thời điểm mới — Task 2.
3. Kết quả LLM/TTS cũ đến sau ngắt lời: không ghi dữ liệu hoặc phát audio cũ — Task 4.
4. Thiết bị không hỗ trợ 16/48 kHz hoặc audio callback overflow: chuyển rate đúng hoặc vô hiệu lượt, không phát sai tốc độ — Task 5.
5. Disk write thất bại hoặc event confirmed bị gửi trùng: không báo lưu thành công giả, không ghi đè kết quả khác — Task 6.

## File map

Tất cả path source dưới `src/order_vehicles/`. `booking/` giữ domain/dialogue; `llm/` giữ client/extraction; `voice/` giữ protocol và orchestration; `clients/python/` giữ devices/playback/VAD/CLI; `observability/` giữ timing events.

Thứ tự: Tasks 1 → 2 → 3 → 4 → 5 → 6 → 7. Tests domain, protocol, devices giả không cần API key, model weights hoặc micro. Unit tests giữ fixed clock và UUID factory inject được để kiểm tra revisions.

### Task 1: Field registry, patch validation và UUID dữ liệu

**Files:** Create `booking/{models,fields,validators,vehicles}.py`, `tests/booking/test_fields.py`, `tests/booking/conftest.py`; Modify `pyproject.toml`, `uv.lock` thêm phonenumbers.

**Interfaces:** Produces `FieldValue(id:UUID,value:object,valid:bool)`, `ScheduleProposal(date:date|None,relative_days:int|None,hour:int|None,minute:int,period:Literal['am','pm','24h']|None)`, `BookingPatch(name,phone,booking_mode,schedule,vehicle_choice)` với tất cả optional và extra fields forbidden; `ExtractionResult(patch:BookingPatch,intent:Literal['provide','correct','affirm','deny','cancel','unknown'])`. `CallSession(session_id,request_id,fields,field_ids,vehicles_snapshot,revision,status,pending_confirmation_revision,confirmed_revision,confirmation_response_id,playback_finished,attempts_by_field,final_emitted)`. `FieldRegistry.apply(session:CallSession,patch:BookingPatch,now:datetime)->list[str]` trả field errors; `missing_fields(session)->list[str]`. `VehicleCatalog.resolve(choice:str)->str|None`; catalog snapshot trong session. `field_ids` giữ UUID đã tạo dù field sau đó bị vô hiệu.

- [ ] Viết tests `test_complete_now_patch`, `test_scheduled_requires_date_and_time`, `test_vehicle_alias_resolves_to_catalog`, `test_unavailable_vehicle_remains_missing`, `test_empty_catalog_requires_assistance`, `test_invalid_correction_invalidates_old_value`, `test_field_uuid_survives_correction`, `test_relative_day_uses_service_timezone`, `test_eight_without_period_requires_clarification`. Fixture clock `2026-10-12T23:30:00+07:00`, UUID factory deterministic. Assertions gồm `ô tô→car`, `xe máy→bike`, `0901234567→+84901234567`, ngày mai `2026-10-13`, UUID name != UUID phone, mode now không yêu cầu scheduled_at.
  ```python
  assert catalog.resolve('ô tô') == 'car'
  assert session.fields['phone'].value == '+84901234567'
  assert field_id_after_correction == field_id_before_correction
  ```
- [ ] Run `uv run pytest tests/booking/test_fields.py -q`; expected FAIL trước implement.
- [ ] Implement registry định nghĩa field type/required predicate/validator/question, thứ tự hỏi name→phone→booking_mode→scheduled_at→vehicle_type. phonenumbers normalize region VN + validity. Schedule xử lý explicit date hoặc relative_days theo service clock; giờ 1–12 cần period rõ, giờ 0/13–23 có thể là 24h; thiếu ngày/giờ hoặc past giữ invalid. Khi đổi sang now, clear scheduled_at active value. Không fuzzy-match xe, không tạo UUID cho invalid candidate; sửa thành invalid giữ ID lịch sử và tăng revision.
- [ ] Run test command; expected PASS cho tất cả parameterized cases.
- [ ] Commit file task bằng `git add` cụ thể và `git commit -m "feat: validate booking fields and stable field UUIDs"`.

### Task 2: Dialogue state machine, confirmation và serializer

**Files:** Create `booking/{dialogue,confirmation,repository,serializer}.py`, `tests/booking/{test_dialogue,test_confirmation,test_serializer}.py`.

**Interfaces:** Consumes Task 1. Produces `DialogueDecision(kind:Literal['ask','confirm','complete','cancel','assist'],text:str,field:str|None,payload:dict|None)`. `DialogueManager.handle(session:CallSession,result:ExtractionResult,now:datetime)->DialogueDecision`; `mark_playback_finished(session,response_id:str)->None`, `invalidate_confirmation(session)->None`. `SessionRepository.create(profile_ref:str)->CallSession`, `get(session_id:UUID)->CallSession`, `save(session,expected_revision:int)->None`, `detach(session_id,now)->None`, `expire(now)->None`; bộ nhớ + lock. `BookingSerializer.serialize(session:CallSession,now:datetime)->dict` và set completion key `(request_id,confirmed_revision)`.

- [ ] Viết `test_asks_only_missing_field`, `test_affirm_before_playback_does_not_complete`, `test_affirm_with_correction_requires_new_readback`, `test_confirmation_revision_must_match`, `test_schedule_expired_before_confirmation`, `test_third_failed_answer_requires_assistance`, `test_cancel_never_exports`, `test_serializer_emits_once`, `test_now_omits_scheduled_at`, `test_repository_expires_draft_after_15_minutes`. Readback assertions kiểm tra đầy đủ tên/số/ngày/xe từ fields, không từ LLM narrative.
  ```python
  assert early_affirm.kind != 'complete'
  assert corrected_affirm.kind == 'confirm'
  assert payload['revision'] == payload['confirmed_revision']
  assert 'scheduled_at' not in now_payload['fields']
  ```
- [ ] Run `uv run pytest tests/booking/test_dialogue.py tests/booking/test_confirmation.py tests/booking/test_serializer.py -q`; expected FAIL.
- [ ] Implement patch application trước intent affirm; actual correction invalidates previous confirmation. Readback template tiếng Việt và phone đọc từng chữ số; date/time explicit. Đếm failure theo trường vừa được hỏi, chỉ tăng khi câu trả lời thực sự không làm rõ trường đó, không tăng do silence/provider error; reset khi resolved. Playback ack phải khớp response ID. Final revalidate fields/date/catalog snapshot và serializer shape đúng spec. Repository CAS revision và TTL draft 900 s; phục hồi trong process không phục hồi pending confirmation/playback chưa kiểm chứng.
- [ ] Run test command; expected PASS, final JSON mỗi field `{id,value}`, revision=confirmed_revision.
- [ ] Commit `feat: enforce booking confirmation and JSON completion`.

### Task 3: LLM extraction và sentence streaming

**Files:** Create `llm/{ports,client,extractor,prompts,sentences}.py`, `tests/llm/{test_extractor,test_sentences}.py`; Modify `settings.py`, `.env.example`.

**Interfaces:** Produces `LLMAdapter.extract(transcript:str,context:dict)->ExtractionResult`, `stream_reply(instruction:str,context:dict)->AsyncIterator[str]`; HTTP implementation `ChatCompletionsLLMAdapter`, inject HTTPX client. `InformationExtractor.extract(transcript:str,session:CallSession)->ExtractionResult`; retry schema error once. `SentenceBuffer.push(delta:str)->list[str]`, `flush()->list[str]`. Prompt context có catalog, validated field snapshot, pending question và service timestamp/timezone; không đưa secrets.

- [ ] Viết `test_extracts_multiple_fields_in_one_turn`, `test_schema_retry_once_without_applying_invalid_patch`, `test_extra_fields_are_rejected`, `test_assistant_history_cannot_supply_customer_fields`, `test_cancel_and_correction_intents`, `test_sse_unicode_split_frames`, `test_sentence_buffer_does_not_split_decimal_or_abbreviation`, `test_confirmation_text_bypasses_llm_rewrite`. HTTP fixture có content JSON và streamed chat deltas, không gọi nhà cung cấp thật.
  ```python
  assert extraction.patch.name == 'Nam' and extraction.patch.vehicle_choice == 'ô tô'
  assert request_count == 2  # một request ban đầu và một schema retry
  assert sentence_buffer.push('Anh muốn đặt xe lúc ') == []
  ```
- [ ] Run `uv run pytest tests/llm -q`; expected FAIL.
- [ ] Implement base URL/model/key required cho live backend, không âm thầm chọn hosted provider. `/chat/completions` với explicit JSON schema hướng dẫn; nếu server có native structured output, bật qua config; luôn local-validate Pydantic. Requests extract non-streaming, replies streaming SSE incremental UTF-8. LLM chỉ nhận user transcript như dữ liệu; patch không trở thành hợp lệ trước validators. Câu hỏi có thể diễn đạt tự nhiên nhưng confirmation và kết thúc lấy template. Gom deltas theo kết thúc câu `.?!` có xử lý decimal và danh sách abbreviation cấu hình; flush khi stream xong. Extraction timeout 30 s, reply idle timeout 30 s; lỗi schema retry tối đa tổng hai request.
- [ ] Run test command; expected PASS, schema lỗi không tăng domain revision.
- [ ] Commit `feat: extract structured booking data from configurable LLM`.

### Task 4: Voice protocol và session orchestration

**Files:** Create `voice/{protocol,session,tasks,api}.py`, `tests/voice/{test_protocol,test_session}.py`, `tests/voice/fakes.py`; Modify `app.py` composition.

**Interfaces:** Consumes `SpeechRuntimeService.open()` từ speech plan, Task 2 dialogue/repository và Task 3 extractor/LLM. Produces Pydantic JSON events protocol `voice.booking.v1` và `VoiceSessionService.run(socket:SessionSocket)->None`; `SessionSocket` Protocol async receive/send_json/send_bytes/close. `ResponseTasks.start(response_id:str,work:Awaitable)->None`, `interrupt()->None`, `close()->None`; một serialized writer. Route `WS /v1/voice/sessions` inject service. `resume` hỗ trợ session_id+resume token ngẫu nhiên từ session.started trong TTL; không resume bằng session UUID đơn lẻ.

- [ ] Viết `test_session_start_validates_audio_format`, `test_summary_creates_one_domain_turn`, `test_binary_outside_open_input_turn_is_rejected`, `test_old_llm_result_after_interrupt_is_ignored`, `test_old_audio_cannot_cross_new_response_boundary`, `test_input_turns_are_serial`, `test_playback_ack_unlocks_confirmation`, `test_disconnect_stops_tasks_and_keeps_draft`, `test_invalid_resume_token_is_rejected`, `test_provider_timeout_does_not_commit_partial`. Fake runtime kiểm soát completion order để tạo race, không dùng sleep cố định.
  ```python
  assert extractor.calls == 1  # partial + utterance + summary của cùng một lượt
  assert session.revision == revision_before_stale_llm_result
  assert active_socket_count == 0 and active_lease_count == 0  # sau disconnect
  ```
- [ ] Run `uv run pytest tests/voice -q`; expected FAIL.
- [ ] Implement receive loop không chặn trong lúc awaiting LLM/TTS. Session start pin profile và tạo token; input_audio.start/end đánh dấu frame ownership; upstream một STT session mỗi lượt. Chỉ summary invokes extraction. Generation token kiểm tra trước mọi update/output; dùng một writer queue để giữ JSON/binary ordering. Interrupt vô hiệu response, hủy/await sender, drain bytes cũ trước audio.start response mới; client cũng discard đến metadata mới. TTS câu tuần tự, audio.end khi transmission xong; hoàn tất phải chờ playback.finished. Provider error/silence chỉ hỏi lại khi có thể, không làm domain data hợp lệ. Close app/socket cancellation release runtime leases.
- [ ] Run test command; expected PASS cho race tests và không leak async tasks.
- [ ] Commit `feat: orchestrate interruptible voice booking sessions`.

### Task 5: Audio devices, bounded playback và TurnController

**Files:** Create `clients/python/{audio,playback,vad,turns,transport}.py`, `tests/client/{test_audio,test_vad,test_turns,test_playback}.py`; Modify `pyproject.toml`, `uv.lock` extra audio.

**Interfaces:** Produces `SoundDeviceInput.open(device:int|None)->None`, `frames()->AsyncIterator[bytes]`, `close()->None`; `SoundDeviceOutput.open(format:AudioFormat,device:int|None)->None`, `enqueue(response_id:str,packet:AudioPacket)->None`, `clear()->None`, `close()->None`, `finished()->AsyncIterator[str]`. `EnergyVAD.is_speech(pcm:bytes)->bool`; `TurnEvent(kind:Literal['start','frame','end','overflow'],pcm:bytes|None,elapsed_ms:int)`; `TurnController.feed(frame:bytes)->list[TurnEvent]`, `finish()->list[TurnEvent]`; elapsed_ms của end là trailing silence, của start là elapsed speech. `PythonSessionTransport.connect(url:str)->AsyncContextManager[SessionSocket]`. Device factory/resampler injectable trong tests.

- [ ] Viết `test_vad_200ms_start_700ms_end`, `test_preroll_preserves_first_syllable`, `test_turn_maximum_30s`, `test_unsupported_device_rate_resamples_statefully`, `test_output_48000_is_not_played_as_24000`, `test_callback_overflow_invalidates_turn`, `test_clear_prevents_buffered_old_response_playback`, `test_ack_after_device_playback_not_network_done`, `test_no_network_calls_inside_callback`. Dùng 20 ms frames và fake device callback, fake clock; verify sample duration trước/sau conversion.
  ```python
  assert audio_format.sample_rate == 48000
  assert end_event.elapsed_ms == 700  # tính từ silence bắt đầu
  assert not playback_finished_before_last_sample_is_played
  ```
- [ ] Run `uv run pytest tests/client -q`; expected FAIL.
- [ ] Implement RawInputStream/RawOutputStream với lazily imported sounddevice; queues giới hạn 100 frames input (2 s) và 2 s output theo actual sample rate. Callback chuyển PCM/buffer trong thời gian ngắn; producer network bị chậm gây overflow báo error thay drop kín. Resample stateful `soxr.ResampleStream` từ native device rate sang16k, output sang rate device hỗ trợ. Format s16le được explicit convert, không mặc định host byte order. VAD energy threshold config/CLI calibration, defaults spec; manual end/interrupt cho push-to-talk. Flush/end/reset resampler đúng lượt, output generation ID kiểm tra trong callback. Ack sau buffer cuối thực sự được phát, dùng device stream timing; không ack lúc vừa dequeue cuối.
- [ ] Run command; expected PASS khi không có PortAudio/micro trên host test. Audio extra import failure thông báo installation cần thiết thay lỗi import của core API.
- [ ] Commit `feat: add Python microphone and interruptible audio playback`.

### Task 6: PythonMicClient, management recording và lưu JSON

**Files:** Create `clients/python/{microphone,recording,results}.py`, `tests/client/{test_microphone,test_results,test_recording}.py`; Modify `clients/python/cli.py`, `clients/python/management.py`, `README.md`.

**Interfaces:** Produces `PythonMicClient.run(url:str,input_device:int|None,output_device:int|None,push_to_talk:bool)->None`, `record_reference(seconds:float,device:int|None)->bytes` trả WAV, `AtomicBookingWriter.write(payload:dict)->Path`. Extend CLI `ov mic`, `ov devices`, `ov voices record --name NAME --seconds N`; clone upload đi qua management service speech plan. Inject I/O và writer để test không dùng mic.

- [ ] Viết `test_fake_microphone_completes_booking`, `test_client_clears_audio_before_sending_interrupt`, `test_recorded_clone_is_separate_from_booking_session`, `test_duplicate_confirmed_event_keeps_same_file`, `test_write_failure_is_visible_and_retryable`, `test_request_uuid_controls_safe_filename`, `test_ctrl_c_closes_devices_and_socket`. Assert không file trước confirmed; final field IDs parse UUID; output collision khác payload không silently overwrite.
  ```python
  assert list(output_dir.glob('*.json')) == []  # trước confirmed
  assert writer.write(payload) == writer.write(payload)
  assert 'lưu thành công' not in failed_write_output
  ```
- [ ] Run `uv run pytest tests/client/test_microphone.py tests/client/test_results.py tests/client/test_recording.py -q`; expected FAIL.
- [ ] Implement client event pump, turn→input events, reply→playback events và barge-in local clear tức thì. CLI state/transcript không cần GUI; Ctrl+C graceful shutdown. Native record lưu WAV riêng rồi upload, không lấy audio từ session đặt xe. Writer kiểm tra schema/request UUID, ghi temp cùng filesystem và replace; cache completion key; nếu file đã có cùng payload trả path, khác payload báo conflict. Disk failure hiển thị lỗi và giữ payload trong bộ nhớ để retry; không báo đã lưu thành công. End session sau kết quả và lời kết đã playback hoặc explicit user end.
- [ ] Run command trên, `uv run ov mic --help`, `uv run ov voices record --help`; expected PASS và flags device/PTT đúng.
- [ ] Commit `feat: ship Python voice client and confirmed booking export`.

### Task 7: Acceptance, timing và runbook

**Files:** Create `observability/timing.py`, `tests/integration/{test_booking_acceptance,test_live_voice}.py`, `docs/runbooks/voice-booking.md`; Modify `README.md`, `.env.example`, `app.py`, `voice/session.py`.

**Interfaces:** Produces `TurnTiming.mark(stage:str,at:float)->None`, `metrics()->dict`; stage names input.end/stt.final/llm.first_text/tts.first_audio/playback.first_audio. Inject monotonic clock; client reports actual playback time. Recorder tổng hợp p50/p95 không log audio/secrets; provider model/hardware metadata do live run lưu rõ.

- [ ] Viết fake end-to-end acceptance scenarios: all-fields one turn, scheduled thiếu giờ, unavailable xe, correction khi confirm, cancellation, voice profile đã clone/archetype, interruption race và upstream unavailable. `test_timing_separates_transmission_from_playback` assert playback timestamp lớn hơn hoặc bằng first audio received và tính elapsed theo clock phù hợp từng process, không trừ monotonic timestamp của hai máy khác nhau.
  ```python
  assert booking['fields']['vehicle_type']['value'] == 'car'
  assert booking['revision'] == booking['confirmed_revision']
  assert local_playback_first_at >= local_audio_received_at
  ```
- [ ] Run `uv run pytest tests/integration/test_booking_acceptance.py -q`; expected FAIL cho wiring còn thiếu.
- [ ] Implement timing hooks và runbook: `uv sync --extra audio --group dev`, cấu hình VoiceStudio revision/engine/model/voice và LLM endpoint/model, `ov server`, `ov devices`, `ov mic`. Live test opt-in `RUN_LIVE_VOICE=1` có prerequisite model đã nạp, LLM hoạt động, reference/audio thiết bị/tai nghe; ghi tiếng Việt, tên riêng, số điện thoại, ngày giờ, ba lượt liên tiếp và ngắt lời. Timing trong từng process hoặc dùng elapsed durations được truyền, không so absolute monotonic clocks qua network. Không cam kết SLA từ số benchmark upstream.
- [ ] Run `uv run pytest tests/booking tests/llm tests/voice tests/client tests/integration/test_booking_acceptance.py -q` và `uv run pytest tests/speech -q`; expected PASS. Run live opt-in khi có prerequisites; tiêu chí đủ là nghe được audio, barge-in dừng response cũ, UUID/JSON đúng và không xuất trước confirm. Nếu thiếu model/hardware, report đúng check chưa chạy; giữ live check pending.
- [ ] Commit `test: verify voice booking acceptance and document operation`.

## Self-review và giới hạn scope

- [x] Spec §1–3 được cover bởi domain/LLM/runtime composition; audio §4 qua Task 5/6; protocol §5 qua Task 4; upstream §6 dùng speech plan; data/state §7–8 qua Task 1/2; lỗi/scale §9 qua Task 2/4/5; verification §10 qua Task 7.
- [x] Kiểm tra interfaces giữa SpeechRuntime, CallSession, ExtractionResult, DialogueDecision, AudioPacket và writer khớp trước code.
- [ ] Baseline là một API worker và bộ nhớ session; persistent shared session store/worker router là extension được boundaries hỗ trợ, không triển khai hệ phân tán trong MVP.
- [ ] Kết quả triển khai phải nêu riêng tests offline, inference smoke và live micro đã chạy; không gọi mọi thứ complete chỉ vì unit tests xanh.

Nguồn audio: [sounddevice raw streams](https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html), [stateful resampler](https://python-soxr.readthedocs.io/en/latest/soxr.html).
