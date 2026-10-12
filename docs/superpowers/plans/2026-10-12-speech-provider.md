# Speech Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây layer speech OOP tái sử dụng inference, model management, clone và giọng mẫu VoiceStudio, có API và CLI dùng được độc lập với nghiệp vụ đặt xe.

**Architecture:** Provider là service riêng, project gọi HTTP/WebSocket qua adapter inject vào services. Runtime dùng cấu hình snapshot và lease tài nguyên; management giữ defaults, catalog/job mapping và lifecycle thay đổi model. VoiceStudio là nguồn dữ liệu chính cho weights và voice profiles.

**Tech Stack:** Python 3.12 cho môi trường phát triển; package `src/order_vehicles`; FastAPI/Pydantic 2, HTTPX, websockets asyncio, uv/uvicorn; pytest/pytest-asyncio. `sounddevice`, NumPy và soxr là extra `audio`, phục vụ plan client kế tiếp. Dùng thư viện hiện hành đã resolve trong `uv.lock`, không cài PyTorch/model vào môi trường API project.

**Spec:** [speech-provider-design](../specs/2026-10-12-speech-provider-design.md), [voice-booking-design](../specs/2026-10-12-voice-booking-design.md).

## Global Constraints

- Baseline VoiceStudio: `7703ca66da77b8d130e6d1d3b2e67d979fa5a77f`; pin revision deployment và contract-test trước nâng cấp.
- Không fork UI hoặc sao chép inference/model loaders vào project đặt xe; composition và constructor injection.
- Cài weights giới hạn catalog `KNOWN_MODELS`; file `.pt` không có generic importer.
- Model weights install với `target=local` là cài trên máy chạy VoiceStudio, không phải máy client.
- Provider ID là opaque string; UUID project tách khỏi provider profile ID tám ký tự.
- STT purpose/capture path riêng với batch ASR; không suy luận batch selection áp dụng cho streaming.
- STT/TTS config riêng; snapshot khi mở session; không `/engines/select` theo mỗi lượt.
- `cancel_requested` không đồng nghĩa `cancelled`; không tự download khi list catalog.
- TTS sample rate lấy từ metadata; không hardcode 24 kHz cho `/ws/tts`.
- Loopback/server/admin/local-only checks upstream được giữ nguyên; credential quản trị không đưa vào client.
- Không xóa tài nguyên được lease; đổi global selection khi worker hết phiên đang chạy.

## Review Focus

1. `env_override=true`: lưu lựa chọn không được báo thành công nếu backend thực tế vẫn dùng lựa chọn khác — Task 6.
2. SSE bị mất ngay lúc download xong: refresh catalog phải xác minh thành công, không kẹt hoặc báo thành công giả — Task 4.
3. Hai provider có cùng voice ID `abcd1234`: resolve reference phải giữ namespace — Task 1.
4. Streaming STT chạy fallback khác model yêu cầu: trả degraded hoặc lỗi exact-model, không giấu việc đổi model — Task 2.
5. Hủy client giữa audio và retry: không phát lại bytes hoặc giữ lease/connection vô hạn — Task 3 và Task 6.

## File map và thứ tự

Task 1 → Tasks 2, 3, 4, 5 → Task 6 → Task 7. Các task adapter có thể được review riêng nhưng triển khai mặc định tuần tự.

- `speech/domain/`: DTO, Protocol và errors; không network.
- `speech/providers/voicestudio/`: client, runtime và management adapters.
- `speech/application/`: jobs, resource leases, profiles và runtime/management services.
- `speech/api/`: routes và composition qua `app.py`.
- `clients/python/cli.py`: CLI quản lý; client micro được làm trong plan voice-booking.
- `tests/speech/`: fake upstream, unit/contract/API tests; mọi đường dẫn module dưới `src/order_vehicles/`.

Workspace hiện chỉ có spec, chưa có Git repository. Khi execution bắt đầu, xác minh lại trạng thái; nếu vẫn chưa có Git, khởi tạo repo tại workspace rồi thực hiện commit theo task. Không dùng worktree trước khi có repo. Commit chỉ liệt kê file của task, không `git add .`.

### Task 1: DTO, interfaces và cấu hình dùng chung

**Files:** Create `pyproject.toml`, `uv.lock`, `.gitignore`, `.env.example`, `src/order_vehicles/__init__.py`, `src/order_vehicles/settings.py`, `src/order_vehicles/speech/domain/{models,ports,errors}.py`, `tests/speech/test_contracts.py`.

**Interfaces:** Produces Pydantic DTO `AudioFormat(encoding,sample_rate,channels)`, `AudioPacket(format,data)`, `TranscriptionEvent(kind,text,final_kind,actual_engine)`, `ModelSelection(engine_id,model_id,purpose)`, `SpeechConfig(stt,tts,voice_ref,language,speed,exact_model)`, `EngineInfo`, `ModelInfo`, `VoiceInfo`, `VoiceFilter`, `CloneRequest`, `RuntimeProfile`, `ManagementJob`. `VoiceRef(provider_id,kind,provider_voice_id)` encode/decode bằng base64url JSON. `SpeechError(code,message,retryable)` không chứa secrets. `SpeechRecognizer`, `SpeechSynthesizer`, `ModelManager`, `VoiceLibrary`, `RuntimeProfileRepository` là Protocol; chữ ký cụ thể tại task sử dụng. `Settings` có URL provider, secret, output/state directories và timeout hữu hạn.

- [ ] Chuẩn bị package metadata Python `>=3.12`, pytest runner và extras; `uv sync --group dev` chỉ resolve dependencies project. Nếu chưa có Git, `git init -b main` trước commit đầu tiên.
- [ ] Viết tests `test_voice_reference_preserves_provider_namespace`, `test_profile_id_is_opaque`, `test_secret_is_not_serialized`, `test_defaults_do_not_require_model_imports`; pin assertions:
  ```python
  assert VoiceRef.decode(VoiceRef(provider_id='voicestudio', kind='clone', provider_voice_id='abcd1234').encode()).provider_voice_id == 'abcd1234'
  assert VoiceRef(provider_id='a', kind='clone', provider_voice_id='abcd1234').encode() != VoiceRef(provider_id='b', kind='clone', provider_voice_id='abcd1234').encode()
  ```
- [ ] Run `uv run pytest tests/speech/test_contracts.py -q`; expected FAIL vì domain/Settings chưa triển khai.
- [ ] Implement DTO/Protocol/settings: URL service root tách khỏi `/v1`; connect timeout 10 s, REST timeout 30 s, inference inactivity timeout 60 s, max turn 30 s, profile state dưới `state/`. Unknown capability giữ unknown, không chuyển thành True. Tạo `.env.example` với placeholders credentials rỗng có chủ ý; defaults TTS engine `omnivoice`, language `vi`, exact-model False.
- [ ] Run lại test command; expected PASS, import project không cần model/hardware.
- [ ] Commit bằng `git add` các file task và `git commit -m "feat: define speech contracts and configuration"`.

### Task 2: HTTP client và STT adapter

**Files:** Create `speech/providers/voicestudio/{client,stt}.py`, `tests/speech/{test_client,test_stt}.py`, `tests/speech/fakes.py`.

**Interfaces:** Consumes DTO/Settings Task 1. Produces `VoiceStudioClient.request(method:str,path:str,**kwargs)->httpx.Response`, `iter_sse(path:str)->AsyncIterator[dict]`, `aclose()->None`; inject HTTPX transport và WebSocket connector. `VoiceStudioSTTAdapter.transcribe(audio:AudioPacket,config:SpeechConfig)->TranscriptionEvent`, `stream_transcribe(frames:AsyncIterator[bytes],config:SpeechConfig)->AsyncIterator[TranscriptionEvent]`. Fakes cung cấp scripted frames/request recording cho Tasks 3–7.

- [ ] Viết tests: `test_management_root_is_not_v1`, `test_batch_transcription_wraps_pcm_as_wav`, `test_summary_is_authoritative`, `test_socket_closes_after_summary`, `test_fallback_reports_actual_engine`, `test_exact_model_rejects_fallback`, `test_local_only_403_preserves_reason`, `test_transcript_disconnect_does_not_commit_partial`. Assertions gồm `/v1/audio/transcriptions/stream?pcm=1&sr=16000`, `input_audio.end`, giữ transcript raw thay `refined_text`, chỉ một summary có hiệu lực.
  ```python
  assert summary.text == 'Nam muốn đặt xe'
  assert sent_control == {'type': 'input_audio.end'}
  assert error.code == 'MODEL_MISMATCH'  # exact_model=True, upstream dùng fallback
  ```
- [ ] Run `uv run pytest tests/speech/test_client.py tests/speech/test_stt.py -q`; expected FAIL.
- [ ] Implement client lifecycle/bearer headers/errors và STT adapter. Một socket upstream cho một lượt; gửi/nhận qua hai task trong cùng lifecycle, cancellation đóng cả hai. Gắn model query chỉ cho Sherpa selection đã xác minh; cấu hình capture khác kiểm tra qua metadata/readiness thay vì tự dựng query engine không được hỗ trợ. Phân biệt partial, utterance và summary; adapter có thể emit cả ba, consumer chỉ commit summary. Giới hạn response/error size và không retry audio tự động.
- [ ] Run command trên; expected PASS, request metadata đúng và connection được đóng trên mọi exit.
- [ ] Commit `feat: add VoiceStudio client and STT adapter` với đúng file task.

### Task 3: TTS streaming và audio metadata

**Files:** Create `speech/providers/voicestudio/tts.py`, `tests/speech/test_tts.py`.

**Interfaces:** Consumes client/connector Task 2. Produces `VoiceStudioTTSAdapter.synthesize(text:str,config:SpeechConfig)->AudioPacket`, `stream_synthesize(text:str,config:SpeechConfig)->AsyncIterator[AudioPacket]`; packets giữ cùng format theo metadata, errors theo Task 1.

- [ ] Viết `test_start_metadata_48000_is_preserved`, `test_audio_before_start_is_error`, `test_routing_event_is_handled`, `test_error_before_audio`, `test_cancel_closes_upstream`, `test_new_response_never_receives_old_bytes`, `test_odd_pcm_byte_is_buffered_until_next_frame`; assertions:
  ```python
  assert packets[0].format.sample_rate == 48000
  assert b''.join(p.data for p in packets) == expected_pcm
  assert connector.open_connections == 0
  ```
- [ ] Run `uv run pytest tests/speech/test_tts.py -q`; expected FAIL.
- [ ] Implement `/ws/tts` requests với provider profile ID hoặc preset đã resolve, text, engine, language, speed. Parse `start/routing/done/error`; dữ liệu trước metadata là protocol error. Giữ byte lẻ qua frame tiếp theo, byte lẻ khi stream kết thúc là truncated PCM error. Mỗi response dùng socket riêng để cách ly audio cũ. `synthesize` có byte cap và gom từ stream; không giả định HTTP chunk đồng nghĩa progressive inference.
- [ ] Run test command; expected PASS, cancellation không tái sử dụng socket cũ.
- [ ] Commit `feat: stream VoiceStudio TTS with format metadata`.

### Task 4: Catalog và model installation jobs

**Files:** Create `speech/providers/voicestudio/models.py`, `speech/application/jobs.py`, `tests/speech/{test_models,test_jobs}.py`.

**Interfaces:** Produces `VoiceStudioModelManager.list_engines()->list[EngineInfo]`, `list_models()->list[ModelInfo]`, `install_model(repo_id:str)->str`, `installation_status()->list[dict]`, `watch_installation()->AsyncIterator[dict]`, `cancel_install(repo_id:str)->None`, `select_model(selection:ModelSelection)->dict`. `ModelJobService.start(repo_id:str)->ManagementJob`, `get(job_id:str)->ManagementJob`, `cancel(job_id:str)->ManagementJob`, `close()->None`; inject monotonic clock/client.

- [ ] Viết tests `test_catalog_listing_has_no_post_requests`, `test_unknown_model_is_rejected`, `test_install_target_is_local`, `test_cancel_is_requested_until_upstream_finishes`, `test_sse_reconnect_refreshes_catalog`, `test_completion_requires_complete_weights`, `test_duplicate_install_reuses_running_job`, `test_model_id_only_sent_to_supported_engine`. Catalog fixture phân biệt installed/incomplete/available/loaded.
  ```python
  assert job.status == 'cancel_requested'
  assert install_body['target'] == 'local'
  assert catalog_after_reconnect[0].installation_state == 'installed'
  ```
- [ ] Run `uv run pytest tests/speech/test_models.py tests/speech/test_jobs.py -q`; expected FAIL.
- [ ] Implement mapping GET catalogs, POST install/cancel và SSE incremental parser không giả định một event trên một network chunk. Job ID project độc lập repo ID; refresh status/catalog sau mất SSE hoặc terminal event. Một install job đang chạy mỗi repo, timeout install theo dõi cấu hình 1800 s; timeout tracking là failed/unknown upstream completion được báo rõ, không suy ra đã hủy. Engine select luôn đọc lại actual selection; lease gate ở Task 6 trước khi mutation.
- [ ] Run test command; expected PASS, tracking không tạo download lại.
- [ ] Commit `feat: manage supported model installations and progress`.

### Task 5: Voice library, clone và archetype resolution

**Files:** Create `speech/providers/voicestudio/{voices,presets}.py`, `tests/speech/test_voices.py`.

**Interfaces:** Produces `VoiceStudioVoiceLibrary.list_voices(filter:VoiceFilter)->list[VoiceInfo]`, `create_clone(reference:bytes,metadata:CloneRequest)->VoiceInfo`, `update_voice(ref:VoiceRef,patch:dict)->VoiceInfo`, `preview_voice(ref:VoiceRef,text:str,config:SpeechConfig)->AsyncIterator[AudioPacket]`, `use_archetype(id:str)->VoiceInfo`, `delete_voice(ref:VoiceRef)->None`. Preview consumes Task 3; capability checker consumes Task 4.

- [ ] Viết `test_clone_uses_multipart_reference`, `test_reference_playback_is_not_synthesis_preview`, `test_clone_unsupported_fails_before_upload`, `test_archetype_use_resolves_profile_id`, `test_profiles_and_aliases_are_distinct`, `test_pagination_collects_matching_archetypes`, `test_unknown_preset_not_advertised`, `test_unicode_voice_name_does_not_change_id`; pin `kind=clone`, `ref_audio`, `ref_text`, `language`, opaque ID `abcd1234`.
  ```python
  assert clone.provider_voice_id == 'abcd1234'
  assert clone.kind == 'clone'
  assert resolved_voice.kind == 'design'  # archetype /use tạo saved profile
  ```
- [ ] Run `uv run pytest tests/speech/test_voices.py -q`; expected FAIL.
- [ ] Implement profile CRUD, archetype filters/pagination/use, provider-scoped references và synthesize preview trên đúng config. Clone capability phải được xác nhận; unknown trả unsupported/unverified. Descriptor presets chỉ cho engine được kiểm tra, default khi chưa biết; aliases đánh dấu default aliases, không giả là nhiều giọng độc lập. Upload giới hạn project 20 MiB và audio extensions WAV/FLAC/MP3/OGG/M4A; lỗi upstream phải được giữ mã chuẩn hóa. Không đọc file path do remote client cung cấp.
- [ ] Run command; expected PASS; mock xác minh preview gọi TTS, không chỉ GET profile audio.
- [ ] Commit `feat: manage cloned voices and reusable voice presets`.

### Task 6: Runtime profiles, resource leases và service lifecycle

**Files:** Create `speech/application/{profiles,leases,runtime,management}.py`, `tests/speech/{test_profiles,test_leases,test_services}.py`.

**Interfaces:** Produces `JsonRuntimeProfileRepository.get()->RuntimeProfile`, `save(profile:RuntimeProfile,expected_revision:int)->RuntimeProfile`; `ResourceLeases.acquire(profile:RuntimeProfile)->AsyncContextManager[None]`, `assert_mutable(ref:str)->None`; `SpeechRuntimeService.open(profile:RuntimeProfile|None=None)->AsyncContextManager[SpeechRuntime]` với `SpeechRuntime.transcribe(frames)->AsyncIterator[TranscriptionEvent]`, `speak(text)->AsyncIterator[AudioPacket]`; `SpeechManagementService` delegates Task 4/5 và `update_profile(profile,expected_revision)->RuntimeProfile`. Provider resolver lấy `provider_id`; baseline đăng ký VoiceStudio, không hardcode provider trong dialogue.

- [ ] Viết `test_session_keeps_snapshot_after_default_change`, `test_busy_worker_blocks_global_select`, `test_voice_delete_blocks_while_leased`, `test_env_override_mismatch_is_conflict`, `test_cancel_and_exception_release_lease`, `test_concurrent_profile_update_uses_revision`, `test_profile_survives_repository_recreation`, `test_configured_model_without_weights_is_not_ready`.
  ```python
  assert active_snapshot.revision == 1 and repository.get().revision == 2
  assert mutation_error.code == 'RESOURCE_IN_USE'
  assert mismatch_error.code == 'MODEL_MISMATCH'
  ```
- [ ] Run `uv run pytest tests/speech/test_profiles.py tests/speech/test_leases.py tests/speech/test_services.py -q`; expected FAIL.
- [ ] Implement atomic JSON profile writes (temp file rồi replace), revision guard và worker resource count dưới lock. Resolve actual catalog/capture readiness before accepting profile; smoke synth/transcribe fixtures inject được. Update giọng default không sửa session snapshot; global select chỉ khi không còn live lease. Preview giữ lease và concurrency semaphore riêng. Session runtime đóng task/socket và release lease qua async context manager. Jobs/service shutdown có drain timeout 5 s rồi cancellation.
- [ ] Run command; expected PASS, profile mismatch không được báo saved-and-ready.
- [ ] Commit `feat: pin speech profiles and protect active resources`.

### Task 7: Management API, CLI và provider smoke harness

**Files:** Create `src/order_vehicles/app.py`, `speech/api/{models,voices,profiles,dependencies}.py`, `clients/python/{cli,management}.py`, `tests/speech/test_api.py`, `tests/integration/test_speech_smoke.py`, `README.md`, `docs/runbooks/speech-provider.md`; Modify `pyproject.toml` entrypoint `ov`.

**Interfaces:** Produces `create_app(settings:Settings,provider_overrides:dict|None=None)->FastAPI`, `main()->None`; app state chứa services đã inject/lifespan. Routes đúng spec §11, `POST voices/{ref}/preview` trả WAV streaming header metadata thực tế để CLI đọc; model jobs trả 202 + job ID; conflicts 409; upstream unavailable 503, unsupported 422. CLI `ov server`, `ov models list/install/status/cancel`, `ov voices list/clone/preview/from-archetype/update/delete`, `ov profile show/set`. Native recording và `ov mic` thuộc plan kế tiếp.

- [ ] Viết API tests cho upload clone, lọc voices, preview, status/cancel jobs, profile revisions, local-only upstream failure và resource in-use; CLI tests inject fake client và kiểm tra exit codes. Error JSON không chứa credential. `POST/PATCH/PUT/DELETE` management yêu cầu token riêng khi cấu hình; default host `127.0.0.1`; non-loopback host đòi cấu hình management token. Giữ auth tests gắn đúng route.
  ```python
  assert install_response.status_code == 202
  assert delete_leased_response.status_code == 409
  assert configured_secret not in error_response.text
  ```
- [ ] Run `uv run pytest tests/speech/test_api.py -q`; expected FAIL.
- [ ] Implement API/CLI/lifespan/dependency wiring và env docs. Smoke harness yêu cầu `RUN_SPEECH_SMOKE=1`, VoiceStudio revision baseline, model đã chuẩn bị và reference fixture được chỉ định; không download khi test suite mặc định. Include catalog → clone/preview → archetype use → profile → STT/TTS thực tế; report model, sample rate, timing và error nếu chưa có prerequisite. Dùng `ov models install` riêng để operator chuẩn bị checkpoint thay vì hidden download trong test.
- [ ] Run `uv run pytest tests/speech -q` và `uv run ov --help`; expected PASS và danh sách lệnh. Khi prerequisites có, run `RUN_SPEECH_SMOKE=1 uv run pytest tests/integration/test_speech_smoke.py -q`; expected PASS, hoặc báo prerequisite missing rõ ràng và giữ checklist smoke chưa hoàn thành. Không dùng SKIP như bằng chứng inference đã hoạt động.
- [ ] Commit `feat: expose speech management API and Python CLI`.

## Handoff và self-review

- [x] Map spec §1–6 sang Tasks 1–3/7; model §7 sang Task 4; clone/preset §8–9 sang Task 5; concurrency §10 sang Task 6; API §11 sang Task 7; kiểm chứng §12 vào tests và smoke.
- [x] Xác nhận chữ ký service, VoiceRef và AudioPacket nhất quán giữa tất cả task.
- [ ] Chuyển sang [voice-booking plan](2026-10-12-voice-booking.md) sau khi Tasks 1–7 có verification phù hợp.

Model baseline là OmniVoice qua VoiceStudio. Provider resolver cho phép triển khai adapter VieNeu/service bên thứ ba sau này theo contract đã thống nhất; hai spec hiện tại không yêu cầu fork VoiceStudio để đăng ký VieNeu bên trong engine registry.

Nguồn API thư viện cho implementer: [FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/), [HTTPX async](https://www.python-httpx.org/async/), [websockets asyncio client](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html).
