# Piper TTS and Voicemail/Answering-Machine Detection Plan

## Purpose

This document records the research, recommended architecture, rollout plan, and live-testing strategy for adding:

1. Local Piper/VITS-based text-to-speech.
2. Faster answering-machine/voicemail detection (AMD).
3. Measured end-to-end latency across VAD, STT, LLM, TTS, and playback.

The existing working Google Voice dialer must remain untouched until the replacement implementation has passed offline and controlled live tests.

## Current project baseline

The active project is:

```text
D:\Dispatch Softwares\Auto AI Agent Dialer\GoogleVoiceAgent-Active
```

Its current realtime call path is:

```text
Google Voice in Chrome
  -> VB-CABLE / sounddevice audio capture
  -> EnergyVAD
  -> Groq Whisper STT
  -> Groq LLM response
  -> Edge TTS (with optional pyttsx3 fallback)
  -> sounddevice playback
  -> VB-CABLE
  -> Google Voice microphone
```

Relevant existing components:

| Component | Location | Current responsibility |
|---|---|---|
| Realtime TTS | `src/realtime_tts.py` | Edge TTS synthesis, optional SAPI fallback, playback, interruption, recording observer |
| TTS cache | `src/tts_cache.py` | Edge MP3 prewarming for common phrases |
| STT | `src/stt.py` | Groq Whisper transcription of completed audio segments |
| Conversation loop | `src/conversation_loop.py` | Capture, VAD, STT, LLM, TTS, barge-in, recording |
| VAD | `src/vad.py` | 30 ms energy-based speech segmentation |
| Existing voicemail detector | `src/voicemail_detector.py` | FFT beep detection, greeting/silence pattern detection, evidence scoring |
| Google Voice cues | `src/google_voice.py` | DOM call-state and voicemail recording-surface cues |
| Audio playback | `src/voice_playback.py` | Device validation, resampling, channel mapping, sounddevice output |
| Configuration | `src/config.py` | `.env` and JSON settings |
| Runtime paths | `src/paths.py` | Source/frozen runtime directories |
| Packaging | `packaging/IndusDispatchConsole.spec` | PyInstaller collection and application resources |

## Current latency model

The current code does not yet record one complete end-to-end latency value. It records individual state and TTS timing, but the next implementation must add correlated timestamps for every turn.

The current intended VAD endpoint is approximately:

```text
7 silence frames x 30 ms = approximately 210 ms
```

The practical current response-start latency is expected to be approximately:

| Stage | Expected range |
|---|---:|
| VAD trailing silence | 0.21–0.36 s |
| Groq Whisper STT | 0.25–1.2 s |
| Groq LLM first response/clause | 0.15–0.8 s |
| Edge TTS synthesis | 0.35–1.5 s |
| Decode/playback startup | 0.05–0.2 s |
| Typical total | 1.0–3.5 s |

These are planning estimates, not measured production results. Measurements must be collected on the actual Windows/VB-CABLE/Google Voice route.

## Piper/VITS assessment

Piper is a local neural TTS engine using ONNX voice models. It is suitable for replacing only the TTS synthesis layer:

```text
LLM text
  -> Piper ONNX voice
  -> PCM audio
  -> existing voice_playback.py
  -> VB-CABLE
```

Piper does not replace:

- Groq Whisper STT.
- The Groq LLM.
- VAD.
- Google Voice automation.
- Voicemail detection.

Expected Piper behavior after the model is loaded:

| Operation | Planning estimate |
|---|---:|
| Model load, once | 0.5–3 s |
| Short phrase synthesis on CPU | 50–300 ms |
| One short response synthesis | 100–700 ms |
| Playback startup | 10–80 ms |

Compared with Edge TTS, Piper should usually reduce TTS waiting by approximately 0.2–0.9 seconds, although the exact result depends on CPU, model, text length, and concurrent calls.

The model must be loaded once and reused. Loading Piper for every sentence would create unacceptable latency.

## Piper limitations

1. Voice quality depends strongly on the selected model.
2. Pronunciation of names, abbreviations, phone numbers, and trucking terms may require text normalization.
3. Piper is not automatically word-streaming; the first implementation should synthesize a complete clause.
4. CPU usage increases with simultaneous calls.
5. Piper models must be distributed as sidecar files rather than hidden inside the PyInstaller executable.
6. Google Voice phone audio is narrowband, so studio-quality model output will be limited by the call codec.
7. Piper has fewer expressive/emotional controls than hosted neural TTS.
8. A separate Piper instance or controlled synthesis/playback queue is required for concurrent calls to prevent cross-call audio.

## Existing voicemail detection

The active project already performs multi-signal detection:

```text
Raw audio frames
  -> FFT beep detector
  -> sustained greeting/silence pattern
  -> optional STT voicemail keywords
  -> human-response classifier
  -> Google Voice DOM cues
  -> conservative multi-confirmation policy
```

Important current behavior:

- Beep frequency range is approximately 700–1600 Hz.
- A beep must persist for approximately 350 ms.
- Greeting analysis requires sustained prior speech.
- The combined evidence gate requires answer elapsed time of at least 7 seconds.
- The active voicemail evidence window is configured at 15 seconds.
- Cached evidence can be evaluated for up to approximately 30 seconds.
- Google Voice DOM voicemail scanning has a short post-connect cap.

The current detector is conservative by design: incorrectly treating a human as voicemail is more damaging than waiting a little longer on a machine.

## GitHub AMD research

### 1. Recommended first evaluation: `rixwankhan/whisper-vm-finetune`

Repository:

```text
https://github.com/rixwankhan/whisper-vm-finetune
```

Approach:

- Fine-tuned Whisper-small for short early audio.
- Text classifier for `human` versus `machine`.
- Approximately 2-second early decision mode.
- Streaming WebSocket input.
- CTranslate2 inference option for CPU/GPU.
- Full-transcript confirmation mode.

Why it fits:

- Python-based.
- Accepts PCM audio.
- Conceptually compatible with the existing 16 kHz capture path.
- Can be added as an independent detector without removing current rules.

Cautions:

- Reported latency and accuracy are repository claims, not validation on this Google Voice route.
- Real performance may change with Google Voice codec, VB-CABLE routing, noise, and mixed audio.
- It should initially run in shadow mode only.

### 2. Best architecture reference: `arnau01/stentor`

Repository:

```text
https://github.com/arnau01/stentor
```

Approach:

- Incremental streaming DSP features.
- Speech onset timing.
- Energy dynamics and spectral features.
- Optional ONNX scorer.
- Asymmetric decision thresholds.
- Dead zone where the system intentionally does not decide.
- Latched decisions.
- Beep treated as later confirmation rather than the primary early detector.

The most useful design principle is:

```text
machine threshold: high
human threshold: conservative
uncertain range: keep listening
```

Its README states that a voicemail recording often begins 50–200 ms after answer, while a human may take 500–1500 ms to respond. This is a useful hypothesis, but it must be validated on the target carrier/audio path.

The repository is oriented toward Rust/streaming telephony media and is not a direct drop-in Python dependency.

### 3. Wav2Vec2 experiment: `himanshu-dugar/Automatic-Machine-Detection`

Repository:

```text
https://github.com/himanshu-dugar/Automatic-Machine-Detection
```

Approach:

- Fine-tuned Wav2Vec2 sequence classifier.
- First approximately 5 seconds of audio.
- Human versus voicemail classification.

Reported results:

- Approximately 86% test accuracy.
- Approximately 0.89 voicemail precision and 0.86 voicemail recall.
- Approximately 42 ms GPU inference.
- Approximately 880 ms CPU inference.

Cautions:

- It expects a particular recording/channel format.
- It is more useful as a training/reference experiment than as a production drop-in.
- It may require GPU for comfortable realtime use.

### 4. Older reference: `nexmo-community/AnsweringMachineDetection`

Repository:

```text
https://github.com/nexmo-community/AnsweringMachineDetection
```

This is useful for classical feature extraction and model-training ideas, but it is older and should not be the primary implementation.

## Recommended AMD architecture for this project

Do not replace the current detector with one model. Add a detector cascade:

```text
Capture 20–30 ms frames
  -> existing FFT/greeting/silence detector
  -> optional lightweight AMD model
  -> existing human-response/STT evidence
  -> Google Voice DOM evidence
  -> asymmetric decision policy
```

Proposed decision states:

```text
UNKNOWN
PROBABLE_HUMAN
PROBABLE_MACHINE
CONFIRMED_HUMAN
CONFIRMED_MACHINE
```

Suggested policy:

```text
machine probability >= 0.90 plus supporting evidence:
    allow voicemail flow

human probability <= 0.30:
    continue listening; do not interrupt the caller

between thresholds:
    remain UNKNOWN

confirmed human STT or strong live speech evidence:
    cancel machine candidate immediately

confirmed beep or Google Voice recording cue:
    allow machine confirmation
```

Decisions must latch once voicemail playback starts. The system must not begin a voicemail message and then switch back to a live conversation.

## Recommended TTS architecture

Add a selectable engine while preserving the `RealtimeTTS` public interface:

```text
TTS_ENGINE=edge
TTS_ENGINE=piper
TTS_ENGINE=sapi
```

Recommended constructor shape:

```python
RealtimeTTS(
    device_index=loopback_device_index,
    engine=cfg.tts_engine,
    voice=cfg.tts_voice,
    piper_model=cfg.piper_model,
    piper_use_cuda=cfg.piper_use_cuda,
    use_cache=cfg.tts_warmup,
)
```

Piper model files:

```text
models\piper\voice.onnx
models\piper\voice.onnx.json
```

Recommended behavior:

- Load Piper once during `RealtimeTTS` initialization.
- Return NumPy float32 PCM and sample rate.
- Reuse existing `_play_numpy_to_device`.
- Reuse the existing audio observer for recording.
- Disable Edge MP3 cache for Piper or create a decoded PCM cache.
- Pre-generate the voicemail message before dialing.
- Keep Edge available as an explicitly logged fallback during testing.

## Voicemail playback optimization

Piper will not directly improve voicemail classification. It can reduce the delay after classification:

```text
Before dialing:
    generate voicemail text
    synthesize voicemail WAV with Piper

After voicemail is confirmed:
    wait for Google Voice recording-ready cue
    play the already-generated WAV immediately
```

Expected improvement after confirmation:

```text
approximately 0.3–1.5 seconds compared with on-demand Edge synthesis
```

The main detection delay must be addressed by AMD/audio-classifier work, not TTS.

## End-to-end latency instrumentation

Before tuning thresholds, add a per-call/per-turn timing record with:

```text
call_connected_at
speech_started_at
speech_ended_at
stt_started_at
stt_completed_at
llm_started_at
llm_first_clause_at
llm_completed_at
tts_started_at
tts_audio_ready_at
tts_playback_started_at
tts_finished_at
voicemail_candidate_at
voicemail_confirmed_at
voicemail_playback_started_at
```

Derived metrics:

```text
VAD endpoint latency
STT latency
LLM first-token/first-clause latency
TTS synthesis latency
audio playback startup latency
speech-end to first-agent-audio latency
answer-to-voicemail-decision latency
voicemail-confirmation to-message-start latency
```

Thresholds to monitor:

| Metric | Target | Warning | Degraded mode |
|---|---:|---:|---:|
| Speech end to first agent audio | <2.0 s | 2.5 s | 3.5 s |
| Piper synthesis | <0.4 s | 0.75 s | 1.5 s |
| AMD decision | 1–2 s | 3 s | 5 s |
| Voicemail confirmation to message start | <0.3 s with prewarm | 0.75 s | 1.5 s |

## Safe rollout and repository strategy

### Phase 0: Preserve the working version

Do not edit `GoogleVoiceAgent-Active` directly for experimental work.

Create a dated backup or read-only archive of:

```text
D:\Dispatch Softwares\Auto AI Agent Dialer\GoogleVoiceAgent-Active
```

The backup should include:

- source files
- `.env.example`
- configuration examples
- requirements
- packaging files
- tests
- model/runtime instructions

Do not copy real credentials, browser session secrets, or private Chrome profiles into a public repository.

### Phase 1: Obtain the online repository

The user should provide the canonical repository URL. Then clone it into a new sibling directory, for example:

```text
D:\Dispatch Softwares\Auto AI Agent Dialer\GoogleVoiceAgent-AMD-Piper
```

Do not clone over the active directory.

Verify immediately:

```text
git remote -v
git status
git log -1
```

Record the base commit and branch before changes.

### Phase 2: Baseline verification

On the cloned copy:

1. Create a fresh Python virtual environment.
2. Install the existing requirements.
3. Run the existing unit tests.
4. Run audio-device/preflight checks.
5. Verify the original Edge TTS flow.
6. Verify a safe non-production call/simulation.
7. Save baseline logs and latency measurements.

No Piper or AMD changes should be mixed into baseline verification.

### Phase 3: Add instrumentation only

Add timing and structured event logging without changing call decisions.

Run controlled simulations and collect:

- 20+ live-human examples.
- 20+ voicemail examples.
- short and long greetings.
- noisy and quiet calls.
- male/female/older voices where available.
- different carriers/routes if available.

### Phase 4: Add Piper behind configuration

Implement Piper behind:

```env
TTS_ENGINE=edge
```

Keep Edge as the default until Piper passes:

- audio-device playback tests
- pronunciation tests
- interruption/barge-in tests
- recording observer tests
- voicemail pre-generation tests
- concurrent-call isolation tests

### Phase 5: Add AMD in shadow mode

The AMD model receives the same captured frames but cannot change call behavior.

Log:

```text
AMD prediction
existing detector prediction
human STT evidence
Google Voice DOM evidence
later beep confirmation
final call outcome
```

Use the beep/DOM outcome and confirmed human conversation as labels for local evaluation.

### Phase 6: Enable gated AMD decisions

Enable the model only when:

- false-machine rate is acceptably low.
- missed-machine rate is measured.
- CPU/GPU usage is acceptable.
- decision latency is within target.
- behavior is stable over long runs.

Start with a high machine threshold, such as 0.90, and a wide uncertainty zone.

### Phase 7: Live testing

Use staged live tests:

1. Manual operator observation.
2. One call at a time.
3. Small controlled batch.
4. Longer soak test.
5. Multiple concurrent slots only after isolation is verified.

Never start with unattended bulk calling.

## Acceptance criteria

Piper:

- Does not cross-route audio between calls.
- Starts playback within the target after synthesis.
- Does not break barge-in or stop behavior.
- Produces intelligible audio through Google Voice.
- Does not regress recording or transcript behavior.
- Has explicit model-missing and synthesis-failure errors.

AMD:

- Does not mark a confirmed human as voicemail in controlled tests.
- Produces a probability/confidence and reason.
- Has an UNKNOWN/dead-zone state.
- Latches decisions after voicemail playback begins.
- Continues to work with the existing rule-based detector if the model is unavailable.
- Logs enough data for threshold tuning.

Overall:

- Existing tests pass.
- Baseline Edge mode remains available.
- New behavior is configuration-gated.
- No credentials or browser profiles are copied into source control.
- The original working directory remains untouched.

## Final recommendation

The safest and most useful implementation order is:

1. Clone the online repository into a new sibling directory.
2. Verify the original project in the clone.
3. Add end-to-end latency instrumentation.
4. Add Piper as an optional TTS backend.
5. Use Piper to pre-generate voicemail audio.
6. Add `whisper-vm-finetune` as a shadow AMD detector.
7. Borrow Stentor’s asymmetric, latched decision policy.
8. Compare AMD predictions against real Google Voice outcomes.
9. Enable AMD only after controlled validation.
10. Keep Edge TTS and the existing voicemail detector as fallbacks.

The best first production candidate is therefore not “Piper alone” and not “one voicemail model alone.” It is:

```text
Piper for local low-latency TTS
  +
existing rule/DOM/beep detector
  +
shadow/evaluated Whisper-based AMD
  +
conservative streaming decision policy
  +
full latency instrumentation
```

