<div align="center">

# Voxyrn — self-hosted AI auto dialer, voice agent, and CRM

<img src="src/static/voxyrn-logo.svg" alt="Voxyrn icon" width="180" />

**Voxyrn is a self-hosted AI auto dialer and carrier CRM for outbound Google Voice campaigns.**

It dials from a Windows PC or a Linux server, talks with a local or API voice agent, detects voicemail, records the call, and files the lead in the built-in CRM. Speech, answers, and transcripts can stay on the machine, so a local campaign does not add a per-minute voice-API bill. The phone line is the operator's Google Voice account.

This tree is the Voxyrn rebuild of the Routelix console. The operator app stays Python. Each live call's audio gate runs in Rust (`rust/`): a fixed 8-second PCM ring, barge-in, the human-versus-greeting gate, ringback, and the voicemail beep. On a machine with an NVIDIA GPU, local whisper and the GGUF model use CUDA; the ARM server stays on int8 CPU. The plan is in [docs/VOXYRN_PLAN.md](docs/VOXYRN_PLAN.md).

<img src="https://readme-typing-svg.demolab.com?font=Inter&weight=700&size=28&duration=2800&pause=700&color=0EA5E9&center=true&vCenter=true&width=900&lines=Live+AI+Google+Voice+Calling+Console;Realtime+STT+%2B+LLM+%2B+TTS+Outbound+Agent;Windows+Desktop+App+%2B+FastAPI+Operator+Console;Built+for+careful+24%2F7+calling+workflows" alt="Typing SVG" />

<p>
  <img src="https://komarev.com/ghpvc/?username=mafzalkalwardev&label=Project%20Views&color=0ea5e9&style=for-the-badge" alt="Project views" />
  <img src="https://img.shields.io/github/stars/mafzalkalwardev/voxyrn?style=for-the-badge&color=22c55e" alt="GitHub stars" />
  <img src="https://img.shields.io/github/forks/mafzalkalwardev/voxyrn?style=for-the-badge&color=f59e0b" alt="GitHub forks" />
  <img src="https://img.shields.io/github/license/mafzalkalwardev/voxyrn?style=for-the-badge&color=64748b" alt="GitHub license" />
</p>

<p>
  <img src="https://skillicons.dev/icons?i=python,fastapi,selenium,powershell,html,css,js,githubactions,windows" alt="Skill icons" />
</p>

<p>
  <strong>AI sales automation, realtime speech, campaign execution, CRM logging, and Windows app packaging.</strong>
</p>

</div>

## SEO Keywords

`Voxyrn` `self-hosted AI auto dialer` `AI voice agent` `Google Voice auto dialer` `answering machine detection` `voicemail detection` `call recording CRM` `carrier CRM` `lead qualification` `local LLM cold caller` `Python FastAPI dialer` `open source alternative to Vapi` `Kokoro` `Piper TTS` `faster-whisper`

Public page: https://voxyrn.induswebagency.com/

The full search, answer, and generative-engine map, including the question-style queries, is in [docs/VOXYRN_SEO_AEO_GEO.md](docs/VOXYRN_SEO_AEO_GEO.md).

### What is the cheapest AI auto dialer with CRM integration?

Voxyrn. The dialer, leads, recordings, and carrier CRM are in this repository. Local Kokoro or Piper speech, faster-whisper, and a local GGUF model remove the per-minute model bill. Google Voice is the line. There is no setup fee and no contact limit inside the app.

### How to automate cold calling using budget AI agents?

Load the contacts workbook, pass preflight, and start a live run. The agent listens, answers, and writes the lead. Budget means the models run on the same PC.

### Which AI dialer automatically logs calls and records audio?

Voxyrn writes call logs, connected-call rows, transcripts, recordings, leads, and CRM profiles from the same run.

### Best self-hosted or low-cost AI agent for automatic dialing

This Python console. FastAPI serves the operator UI, Selenium drives Google Voice, and the voice agent stays in the repo. It does not require Twilio, Asterisk, or FreeSWITCH.

### Can an AI agent answer incoming return calls automatically?

On a connected outbound call, yes, Voxyrn listens and answers. It does not run a separate inbound queue. Callbacks are stored on the lead and the CRM profile.

---

## Screenshots

Every operator tab, then the CRM list and a profile with sample records.

![Voxyrn console walkthrough](docs/screenshots/voxyrn-console-walkthrough.gif)

![Voxyrn CRM walkthrough](docs/screenshots/voxyrn-crm.gif)

| Dashboard | Preflight | Settings |
| --- | --- | --- |
| ![Dashboard](docs/screenshots/01-dashboard.png) | ![Preflight](docs/screenshots/02-preflight.png) | ![Settings](docs/screenshots/03-settings.png) |

| Contacts | Audio Devices | Live Run |
| --- | --- | --- |
| ![Contacts](docs/screenshots/04-contacts.png) | ![Audio](docs/screenshots/05-audio.png) | ![Live Run](docs/screenshots/06-live-run.png) |

| Call Logs | Leads | Recordings |
| --- | --- | --- |
| ![Call Logs](docs/screenshots/07-call-logs.png) | ![Leads](docs/screenshots/08-leads.png) | ![Recordings](docs/screenshots/09-recordings.png) |

| Connected Calls | Carrier CRM | CRM profile |
| --- | --- | --- |
| ![Connected Calls](docs/screenshots/10-connected-calls.png) | ![Carrier CRM](docs/screenshots/11-carrier-crm.png) | ![CRM profile](docs/screenshots/12-carrier-profile.png) |

---

## Workflow

<p align="center">
  <img src="docs/voxyrn-workflow.svg" alt="Voxyrn sales automation workflow" width="980" />
</p>

---

## Core Capabilities

- Browser-based calling automation using Selenium and persistent Chrome profiles (headless live dials supported).
- Live call-state detection with conservative ringing, connected, ended, failed, and voicemail transitions.
- Realtime speech capture with VAD, local or Groq STT, local or Groq LLM, and Kokoro/Piper/Edge TTS playback.
- Safer mailbox vs person gating: beep/keyword hard proof, short human “hello” lock, and saved-greeting fingerprints that cannot drop a live pickup alone.
- Silent dialing by default with optional **Listen-in** (top bar, Live Run, Audio) so Speakers unmute only when you want to hear both sides; mixed recordings always keep both voices.
- Auto Windows audio route for VB-CABLE (TTS → CABLE Input, Chrome mic → CABLE Output, capture → Speakers loopback).
- Live CRM status banners while a call is active, plus call logs, transcripts, recordings, connected-call archive, failed-call archive, and carrier CRM enrichment.
- Contacts import with MC / legal name / states / address / phone / email columns.
- FastAPI web console for settings, preflight, audio tools, live runs, logs, recordings, leads, and CRM.
- Windows EXE build with PyInstaller.
- Desktop shortcut and Windows Startup shortcut installer.
- Automated tests for state handling, mock dialing, CRM, audio helpers, web routes, greeting library, and realtime loop behavior.

---

## Linux VPS (voxyrn.induswebagency.com)

The operator console can run on Ubuntu 24.04, including aarch64, behind nginx. Google Chrome has no Linux arm64 build, so the server uses Chromium. Windows VB-CABLE support is unchanged.

Exact steps on the server:

```bash
sudo mkdir -p /opt/voxyrn
sudo chown "$USER" /opt/voxyrn
git clone <this-repo> /opt/voxyrn
cd /opt/voxyrn
sudo bash deploy/linux/install.sh
sudoedit /opt/voxyrn/.env   # set VOXYRN_ADMIN_PASSWORD; add GROQ_API_KEY only if you want cloud STT/LLM
sudo systemctl restart voxyrn-audio voxyrn
curl -fsS http://127.0.0.1:8787/healthz
sudo cp deploy/linux/nginx/voxyrn-upgrade.conf /etc/nginx/conf.d/voxyrn-upgrade.conf
sudo cp deploy/linux/nginx/voxyrn.conf /etc/nginx/sites-available/voxyrn
sudo ln -sfn /etc/nginx/sites-available/voxyrn /etc/nginx/sites-enabled/voxyrn
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d voxyrn.induswebagency.com
sudo -u voxyrn bash deploy/linux/gv-login.sh sales_profile
```

Then from a laptop, `ssh -L 6081:127.0.0.1:6081 <user>@<server>` and open `http://127.0.0.1:6081/vnc.html` to finish the Google Voice sign-in once. Voxyrn uses X display `:98` (`VOXYRN_DISPLAY` in `.env`, or `DISPLAY` when that is unset). Display `:99` is laneora-chrome and is not used. VNC and noVNC default to ports 5902 and 6081. Copying an already signed-in Chromium profile (`python -m src.gv_session import-profile`) is the other supported path. A cookie file is best-effort and Google often rejects it.

`deploy/linux/.env.example` lists every variable. The unit listens on `127.0.0.1:8787` with `MemoryMax=8G` and `Restart=always`. Full notes, including the Pulse/PipeWire route and what still cannot be headless, are in [deploy/linux/README.md](deploy/linux/README.md).

---

## Listen-in and recordings

Live dials mute Speakers by default so you are not forced to hear both sides all day. Capture and the TTS observer still mix both parties into `logs/recordings/`.

Turn listening on when needed:

- Top bar: **Listen-in: Off/On**
- Live Run: **Listen to both sides on Speakers**
- Audio Devices → **Live Listen-In**

```env
LIVE_LISTEN_ENABLED=false
CHROME_HEADLESS=true
STT_BACKEND=local
LLM_BACKEND=local
```

`LIVE_LISTEN_ENABLED=true` unmutes Speakers for monitoring. Recordings and STT keep working either way on the supported Realtek/Windows loopback path.

---

## Local voice stack (Kokoro / Piper / Whisper)

Preferred offline path for 24/7 dialing without per-minute API cost:

```env
TTS_ENGINE=kokoro
STT_BACKEND=local
STT_LOCAL_MODEL=base
LLM_BACKEND=local
```

Kokoro and Piper stay fully local. Piper example:

```env
TTS_ENGINE=piper
PIPER_MODEL_PATH=models/piper/en_US-lessac-medium.onnx
PIPER_LENGTH_SCALE=1.0
PIPER_NOISE_SCALE=0.667
PIPER_NOISE_W_SCALE=0.8
```

`PIPER_LENGTH_SCALE` controls speaking pace; the noise settings control voice variation. If a model or dependency is missing, startup reports the configuration error explicitly.

---

## Why This Exists

Most outbound AI voice demos assume a telephony API. This project is different: it works around the practical reality of Google Voice running in a browser. That means the hard parts are not only AI responses. The hard parts are state detection, browser DOM drift, audio routing, ringing safety, voicemail timing, and long-running operational reliability.

The project is designed around those real-world constraints:

- Never speak until the call is actually connected.
- Never treat immediate DOM noise as a completed call.
- Respect a minimum ringing duration.
- Detect voicemail only after connected evidence plus beep, mailbox phrase, or other hard proof.
- Never treat a short live “hello” as a mailbox just because a saved greeting fingerprint is similar.
- Keep full logs so every call can be audited.
- Provide preflight checks before live dialing.

---

## Current Call-State Safety

The most important production fix is the conservative call-state gate in `src/google_voice.py`, plus the mailbox-vs-person gate in `src/conversation_loop.py`.

Current behavior:

- Ringing cannot instantly transition to ended.
- `min_ring_seconds` must pass before accepting connected, ended, or voicemail transitions.
- Active call controls seen too early are logged and held in ringing.
- Stale ended banners are ignored during the minimum ringing window.
- Voicemail page/DOM cues are ignored while still ringing.
- Connected state waits for stable evidence such as answered controls or call timer signals.
- The AI opening line starts only after confirmed connection.
- `max_ring_seconds` ends the attempt as no answer only after the configured ring timeout.
- Short inbound speech bursts lock human pickup before fingerprint matching can race ahead of STT.
- Saved mailbox greeting fingerprints are a second vote only; beep or mailbox keywords still confirm a drop.
- Logs include current state, elapsed ringing time, DOM cues, answered controls, call-active status, voicemail cues, audio classifier result, and timeout reason.

---

## Architecture

```text
GoogleVoiceAgent-AMD-Piper/
  src/
    main.py                  CLI runner, safe test, batch calling
    google_voice.py          Selenium Google Voice automation and call-state detection
    call_session.py          Call session model and CallState transitions
    conversation_loop.py     Realtime capture, VAD, STT, LLM, TTS, voicemail handoff
    voicemail_detector.py    Audio classifier for greeting/beep/live-call signals
    greeting_library.py      Saved mailbox greeting fingerprints (second-vote only)
    human_detector.py        Short human pickup scoring
    audio_setup.py           VB-CABLE route + Speakers mute / listen-in
    live_call_status.py      Realtime CRM/live-run status feed
    ai_groq.py               Groq-backed AI response behavior
    local_llm.py             Offline GGUF replies
    realtime_tts.py          Kokoro / Piper / Edge TTS playback
    audio_capture.py         Input capture and VAD framing
    audio_routing.py         Windows audio-device discovery and playback routing
    crm.py                   Carrier CRM, connected call archive, transcripts, recordings
    web_app.py               FastAPI operator console and API routes
    desktop_app.py           PyInstaller app entrypoint
  tests/                     Automated test suite
  scripts/                   Build and shortcut installers
  packaging/                 PyInstaller spec
  installer/                 Optional Inno Setup installer config
```

High-level runtime flow:

1. Load `.env`, `dialer_config.json`, contacts, and CRM data.
2. Run preflight checks for Chrome profile, contacts, callback number, and audio devices.
3. Auto-route Windows mic/speakers for VB-CABLE and apply Speakers mute unless listen-in is on.
4. Launch Google Voice with the configured Chrome profile (headless by default for live dials).
5. Prepare opening line and voicemail audio before dialing.
6. Dial one contact.
7. Poll `detect_call_state()` until connected, voicemail, no answer, ended, or failed.
8. Start the realtime conversation loop only after confirmed connection.
9. Lock human pickup on short live speech; drop to voicemail only on beep/keyword or other hard proof.
10. Save call logs, transcript, recording, CRM update, and call archive.
11. Apply cooldown and continue to the next contact.

---

## Tech Stack

| Layer | Tools |
|---|---|
| Language | Python |
| Web console | FastAPI, Jinja2, HTML, CSS, JavaScript |
| Browser automation | Selenium, webdriver-manager, Google Chrome |
| AI provider | Local GGUF (llama.cpp) and optional Groq |
| Speech/TTS | Local faster-whisper, Kokoro, Piper, Edge TTS |
| Audio routing | VB-CABLE, sounddevice, soundcard, soundfile, pycaw listen-in mute |
| Data | CSV/XLSX contacts, JSON config, local CRM artifacts |
| Desktop packaging | PyInstaller, PowerShell |
| CI/testing | pytest, GitHub Actions |

---

## Requirements

- Windows 10/11 recommended.
- Python 3.10+ for source runs.
- Google Chrome.
- Google Voice account logged in through the configured Chrome profile.
- VB-CABLE or similar virtual audio device.
- Groq API key.
- Contacts file or CRM data.
- A callback number configured in Google Voice when required by the account.

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Create local config:

```powershell
Copy-Item .env.example .env
Copy-Item dialer_config.example.json dialer_config.json
```

Then edit `.env` and `dialer_config.json`.

---

## How to Install on New PC

Use this when moving Voxyrn from this laptop to another Windows PC.

### 1. Copy the project folder

Copy the full project folder to the new PC, for example:

```text
C:\Users\YourName\Downloads\Voxyrn
```

Do not copy only `src/`; keep `requirements.txt`, `setup_project.bat`, `setup_project.ps1`, `data/`, and the launcher files together.

### 2. Run the one-click setup

Double-click:

```text
setup_project.bat
```

If Windows blocks dependency install or audio packages, right-click `setup_project.bat` and choose **Run as administrator**.

The setup script will:

- Use `winget` when available to install/check Python 3.12, Google Chrome, Microsoft Visual C++ Redistributable, and FFmpeg.
- Check Python and Python version.
- Create `.venv` if missing.
- Rebuild `.venv` if it was copied from another PC and points to an old Python path.
- Upgrade pip.
- Install `requirements.txt`.
- Create missing runtime folders.
- Create `.env` only if it does not already exist.
- Create `.env.example` and a starter `data/contacts.csv` if missing.
- Check for Chrome, Edge, or Chromium.
- Create launcher files for backend, tests, quickstart, and agent runs.

The installer is safe: it does not delete call data and does not overwrite an existing `.env`.

VB-CABLE still needs manual installation from `https://vb-audio.com/Cable/` if Windows does not already have the virtual audio cable. The installer detects and warns about it, but it does not silently install audio drivers.

### 3. Fill `.env`

Open `.env` and set at least:

```env
CALLBACK_NUMBER=your_google_voice_callback_number
CONTACTS_FILE=data/contacts.csv
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
STT_BACKEND=local
LLM_BACKEND=local
TTS_ENGINE=kokoro
LIVE_LISTEN_ENABLED=false
CHROME_HEADLESS=true
# Optional cloud fallback:
# GROQ_API_KEY=your_real_groq_key
```

For Google Voice calling, install/configure VB-CABLE and make sure Chrome has permission to use the correct microphone.

### 4. Run the backend or agent

Start the web console:

```text
run_backend.bat
```

Then open:

```text
http://127.0.0.1:8000
```

Run tests:

```text
run_tests.bat
```

Run the dialer agent directly:

```text
run_agent.bat
```

Run the existing quickstart through the virtual environment:

```text
run_quickstart.bat
```

Before live calling, open the Chrome profile, log into Google Voice, and run the app preflight checks.

### Troubleshooting New PC Setup

PowerShell execution policy blocked:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_project.ps1
```

winget not available:

- Install **App Installer** from Microsoft Store, or install Python 3.12 and Google Chrome manually.
- Then run `setup_project.bat` again.

Python 3.12 not found:

- Install Python 3.12 from `https://www.python.org/downloads/release/python-312/`.
- During install, check **Add Python to PATH**.
- Close and reopen Command Prompt or PowerShell.
- Run `setup_project.bat` again.
- Do not use Python 3.14 for this project; some dependencies may not support it yet.

Copied `.venv` from another computer fails with `No Python at old path`:

- This happens because virtual environments store absolute paths to the Python install that created them.
- The installer now checks `.venv\Scripts\python.exe`; if it cannot run or is not Python 3.12, it removes and recreates `.venv` automatically.
- If automatic recreation fails, close all terminals using the project and run `setup_project.bat` again.
- Manual fix:

```powershell
Remove-Item -Recurse -Force .\.venv
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

pip install failed:

- Check internet connection.
- Run `setup_project.bat` as administrator.
- Confirm Python 3.12 is installed:

```powershell
py -3.12 --version
```

- Try:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

- If audio wheels fail, install Microsoft Visual C++ Redistributable and retry.

sounddevice or soundcard issue:

- Confirm the packages installed:

```powershell
.\.venv\Scripts\python.exe -m pip show sounddevice soundcard soundfile
```

- Reinstall if needed:

```powershell
.\.venv\Scripts\python.exe -m pip install sounddevice soundcard soundfile
```

- Run the app Audio page or:

```powershell
.\.venv\Scripts\python.exe -m src.main --list-audio-devices
```

Chrome driver or Selenium issue:

- Install/update Google Chrome.
- Close all Chrome windows using the same profile.
- Delete only stale `DevToolsActivePort` files if Chrome crashed, not the whole profile.
- Re-run setup so `webdriver-manager` is installed.
- Open Google Voice manually once and log in before automation.

Microphone or audio device issue:

- Install VB-CABLE.
- Set agent playback to `CABLE Input`.
- Set Chrome/Google Voice microphone to `CABLE Output`.
- Keep `CAPTURE_DEVICE=default` for simple testing, or use a dedicated second cable for cleaner caller capture.
- Run:

```powershell
.\.venv\Scripts\python.exe -m src.main --audio-route-test
```

If the app starts but Google Voice does not dial, first confirm the saved Chrome profile is logged into Google Voice on the new PC.

---

## Configuration

Common `.env` values:

```env
GROQ_API_KEY=your_key_here
GOOGLE_VOICE_URL=https://voice.google.com/u/0/calls
CHROME_PROFILE_DIR=chrome_profiles/sales_profile
PLAYBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
CALLBACK_NUMBER=your_callback_number
```

Common `dialer_config.json` values:

```json
{
  "min_ring_seconds": 2,
  "max_ring_seconds": 45,
  "voicemail_detect_seconds": 15,
  "silence_does_not_end_call": true,
  "call_cooldown_seconds": 10,
  "n_slots": 1,
  "max_concurrent_dials": 5,
  "force_requested_slots": false,
  "allow_shared_audio_routes": false,
  "parallel_profiles": [],
  "tts_warmup": true,
  "stt_retry_count": 2,
  "vad_silence_frames": 12,
  "vad_speech_frames": 2
}
```

For 24/7 calling, keep `min_ring_seconds` above zero, use a cooldown, and do not rapid-fire failed numbers.

### One Google Voice account

Settings has a single account, Main line. It places one live call. The dashboard reports CPU and RAM headroom after the reserved budget, and that headroom does not start a second line.

Use **Connect / Verify** once to establish the Chrome session. Saved passwords are protected with Windows DPAPI on Windows and are never returned by the web API. A Google verification step still has to be finished in the browser window.

---

## Audio Routing

Minimum VB-CABLE setup:

- Agent playback device: `CABLE Input`.
- Chrome microphone input: `CABLE Output`.
- Chrome speaker output: a device the agent can capture.

Recommended unattended setup:

- Use separate routes for agent TTS and inbound call capture.
- Avoid capturing the same speaker output that plays the agent voice.
- Keep Windows default audio devices stable.
- Re-run audio discovery after Windows updates or driver changes.

Useful commands:

```powershell
python -m src.main --list-audio-devices
python -m src.main --audio-route-test
python -m src.main --preflight
```

Important: `CAPTURE_DEVICE=default` uses Windows speaker loopback. It works for testing, but for nonstop calling a dedicated capture route is more reliable because it reduces echo and self-transcription risk.

---

## Run From Source

Preflight:

```powershell
python -m src.main --preflight
```

Safe one-number live test:

```powershell
python -m src.main --safe-test +15551234567
```

Start normal calling:

```powershell
python -m src.main
```

Start the web console:

```powershell
python -m src.web_app
```

Open:

```text
http://127.0.0.1:8000/run
```

---

## Run Like A Windows App

Build the EXE:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1
```

Fast rebuild after tests already passed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1 -SkipTests
```

Build outputs:

- `dist\IndusDispatchConsole.exe`
- `release\IndusDispatchConsole-portable.zip`

Install Desktop and Windows Startup shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_shortcuts.ps1
```

Manual app launch:

```powershell
.\dist\IndusDispatchConsole.exe
```

Custom port:

```powershell
.\dist\IndusDispatchConsole.exe --port 8787
```

Startup without opening the browser immediately:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_shortcuts.ps1 -StartupNoBrowser
```

---

## Web Console

The FastAPI console provides operational pages for:

- Live run control.
- Preflight checks.
- Settings.
- Contacts upload/review.
- Audio diagnostics.
- Logs.
- Leads.
- Recordings.
- Connected calls.
- Carrier CRM.

Primary API and page routes live in `src/web_app.py`.

---

## Testing

Run the full suite:

```powershell
python -m pytest tests -q
```

Compile check:

```powershell
python -m compileall src tests
```

Recommended before live calling:

```powershell
python -m src.main --preflight
python -m src.main --safe-test +15551234567
python -m pytest tests -q
```

Current local verification:

```text
231 tests passed
Packaged EXE served /run with HTTP 200
Desktop and Startup shortcuts installed successfully
```

---

## Logs And Runtime Data

Runtime files are intentionally not committed:

- `.env`
- `dialer_config.json`
- `logs/`
- `connected_calls/`
- `failed_calls/`
- `voicemail_calls/`
- `audio/voicemails/`
- `audio/scripts/`
- `chrome_profiles/`
- `dist/`
- `build/`
- `release/`

These can contain secrets, phone numbers, recordings, transcripts, local browser sessions, and generated build artifacts.

---

## 24/7 Operations

For long-running calling:

- Keep the Chrome profile persistent.
- Confirm Google Voice login before dialing batches.
- Use conservative cooldowns between calls.
- Avoid repeated attempts to failed or busy numbers.
- Monitor Groq rate limits.
- Rotate logs and archive recordings.
- Use a dedicated audio route instead of default speaker loopback.
- Watch Google Voice account health.
- Keep a manual Chrome fallback for login challenges.
- Restart the app on a schedule if the browser session drifts.

Recommended future upgrades:

- Silero VAD for stronger speech detection.
- Deepgram for lower-latency realtime STT.
- Langfuse for AI prompt/response tracing.
- OpenTelemetry for runtime metrics.

Tools to delay unless the architecture changes:

- WhisperX is better for offline recordings than live low-latency calls.
- pyannote-audio is useful for diarization, but it adds compute cost.
- LiveKit is powerful, but it would be a larger move away from browser-based Google Voice automation.

---

## Troubleshooting

Call ends while still ringing:

- Check `min_ring_seconds`.
- Review `CALL_STATE` logs.
- Confirm Google Voice DOM did not change.
- Confirm no stale ended banner is being treated as the current call.

AI speaks before pickup:

- Confirm connected evidence appears before `ConversationLoop` starts.
- Run a safe test and inspect state transition logs.

Voicemail triggers too early:

- Confirm voicemail cues are ignored while ringing.
- Confirm voicemail is only accepted after beep, mailbox keyword, or other hard proof — not a short live hello.

No audio into Google Voice:

- Set Chrome microphone to `CABLE Output`.
- Set agent playback to `CABLE Input`.
- Run `--audio-route-test`.

Agent hears itself:

- Avoid `CAPTURE_DEVICE=default` for unattended runs.
- Use a dedicated inbound capture path.

Chrome login expires:

- Open the configured Chrome profile manually.
- Log into Google Voice.
- Run `python -m src.main --preflight`.

---

## Manual Live-Call Test

Use a number you own or have permission to call:

1. Run `python -m src.main --preflight`.
2. Run `python -m src.main --safe-test +15551234567`.
3. Let the phone ring for several seconds.
4. Confirm the AI does not speak while ringing.
5. Answer the call.
6. Confirm the AI speaks only after connection.
7. Say a short phrase and confirm it responds.
8. Decline/send one call to voicemail.
9. Confirm a live hello stays connected, and voicemail drops only after beep or mailbox keyword.
10. Review logs, transcript, recording, and call archive.

---

## Compliance and Responsible Use

This project can place real outbound calls through Google Voice. It is intended for lawful, permission-based business workflows only. Users are responsible for:

- **Consent** and permission-based outreach before calling
- **Do Not Call** lists and applicable telemarketing regulations
- **Caller ID** accuracy and business identification
- **Recording disclosure** where required by law
- **Google Voice terms**, carrier/account limits, and spam prevention
- Local privacy, employment, and telecommunications laws

This software must not be used for spam, harassment, deceptive robocalling, or unauthorized calling. The maintainers are not responsible for misuse.

**Status:** Client-ready · Windows desktop · use demo contacts for testing

---

## Repository Hygiene

The repository keeps source, tests, packaging scripts, workflows, and examples. It intentionally excludes local runtime data, browser profiles, build folders, logs, recordings, generated voicemails, secrets, and private contact data.

Local editor folders, generated test caches, temporary build folders, private runtime data, and one-off planning notes are not part of the product repository.

---

<div align="center">
  <strong>Built for practical AI calling automation: careful state detection, real logs, real audio routing, and a Windows app path.</strong>
</div>
