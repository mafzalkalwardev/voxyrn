# Voxyrn on Ubuntu 24.04 (aarch64 or amd64)

The public site is [https://voxyrn.induswebagency.com](https://voxyrn.induswebagency.com). nginx terminates TLS and proxies to the app on `127.0.0.1:8787`. Ports 1080, 5001, 9222, 12525, and 27017 are left alone.

Google Chrome has no Linux arm64 build. The installer uses Chromium from the xtradeb PPA or Playwright's Chromium, plus chromedriver. It does not use the snap.

## 1. DNS and the checkout

Point `voxyrn.induswebagency.com` at the VPS, then:

```bash
sudo mkdir -p /opt/voxyrn
sudo chown "$USER" /opt/voxyrn
git clone <this-repo> /opt/voxyrn
cd /opt/voxyrn
sudo bash deploy/linux/install.sh
```

`install.sh` installs apt packages, creates the `voxyrn` user, builds `.venv` from `requirements-linux.txt`, downloads the Piper lessac voice, the faster-whisper `base` model (int8 at runtime), and a Qwen2.5 1.5B Q4 GGUF. Skip the GGUF with `sudo SKIP_LLM_DOWNLOAD=1 bash deploy/linux/install.sh` if you will use Groq for answers.

Kokoro is supported (`TTS_ENGINE=kokoro`) but the ONNX files are not downloaded automatically. Place them at the paths in `.env`.

## 2. Environment

```bash
sudoedit /opt/voxyrn/.env
```

Set at least:

- `VOXYRN_SECRET_KEY` (install.sh fills a random value when it creates the file)
- `VOXYRN_ADMIN_PASSWORD` (10 characters or more)
- `CALLBACK_NUMBER`
- `GROQ_API_KEY` only if you want cloud speech or answers. Then set `STT_BACKEND=groq` and/or `LLM_BACKEND=groq`. With the key empty, keep `STT_BACKEND=local` and `LLM_BACKEND=local`.

Every variable is listed in `deploy/linux/.env.example`.

## 3. Services

```bash
sudo systemctl restart voxyrn-audio voxyrn
sudo systemctl status voxyrn --no-pager
curl -fsS http://127.0.0.1:8787/healthz
```

`voxyrn.service` sets `MemoryMax=8G`, `Restart=always`, and listens on `127.0.0.1:8787`. `voxyrn-audio.service` runs PipeWire (or PulseAudio) as the `voxyrn` user so Chromium and the dialer share `/run/voxyrn`.

## 4. nginx and the certificate

```bash
sudo cp /opt/voxyrn/deploy/linux/nginx/voxyrn-upgrade.conf /etc/nginx/conf.d/voxyrn-upgrade.conf
sudo cp /opt/voxyrn/deploy/linux/nginx/voxyrn.conf /etc/nginx/sites-available/voxyrn
sudo ln -sfn /etc/nginx/sites-available/voxyrn /etc/nginx/sites-enabled/voxyrn
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d voxyrn.induswebagency.com
```

Certbot adds the TLS server block. The upgrade map is there for WebSockets. The live run uses server-sent events (`/api/run/stream`); the site config turns proxy buffering off for that stream.

Open `https://voxyrn.induswebagency.com/`, then log in at `/login`.

## 5. Google Voice sign-in (manual, once per profile)

Connect types the email and password. Google's password page is `/signin/challenge/pwd`; that URL is the password form, not a second factor. An authenticator prompt, phone prompt, device prompt, security key, or captcha still has to be finished by a person. Two supported options:

**Interactive noVNC, localhost only**

```bash
sudo systemctl start voxyrn-audio
sudo -u voxyrn bash /opt/voxyrn/deploy/linux/gv-login.sh sales_profile
```

From your laptop:

Display `:99` belongs to laneora-chrome. Voxyrn defaults to `:98`. Set `VOXYRN_DISPLAY` in `.env` (or `DISPLAY` if `VOXYRN_DISPLAY` is unset). `gv-login.sh` reads that value, then `DISPLAY`, then `:98`. VNC and noVNC default to `5902` and `6081` (`VOXYRN_VNC_PORT`, `VOXYRN_NOVNC_PORT`).

```bash
ssh -L 6081:127.0.0.1:6081 <user>@<server>
```

Open `http://127.0.0.1:6081/vnc.html`, sign in at voice.google.com, and confirm the dial pad is visible. Stop the headed Chromium. Later live runs reuse `chrome_profiles/sales_profile` with `CHROME_HEADLESS=true`.

**Copy a profile that is already signed in**

On a machine where Chromium is already logged into Google Voice, copy that user-data directory to the server and:

```bash
sudo -u voxyrn /opt/voxyrn/.venv/bin/python -m src.gv_session import-profile \
  --from /path/to/chromium-user-data --name sales_profile
```

A JSON cookie export is only a best-effort staging file:

```bash
sudo -u voxyrn /opt/voxyrn/.venv/bin/python -m src.gv_session import-cookies \
  --from cookies.json --name sales_profile
```

Google often rejects imported session cookies. If the dial pad does not appear, use noVNC.

## 6. Call audio

`sounddevice` only lists `pulse` and `default` on this stack, so TTS does not look up `voxyrn_to_gv` as a PortAudio device. It plays with `paplay`/`pacat --device=voxyrn_to_gv` and `PULSE_SINK`. Speech-to-text records with `parec` and `PULSE_SOURCE=voxyrn_from_gv.monitor`.

Two PipeWire issues block that playback until the deploy files are installed:

- A system service has no interactive logind seat. WirePlumber's default `with-logind=true` makes it disconnect, the null sink stays `SUSPENDED`, and `paplay` waits forever. `bluetooth.lua.d/51-no-logind.lua` turns that off.
- Idle sinks are suspended after a few seconds. The null sinks are created with `session.suspend-timeout-seconds=0` (space-separated `sink_properties`; commas are ignored). WirePlumber 0.4 and 0.5 drop-ins repeat that rule.

`voxyrn-audio.service` starts a session D-Bus, then PipeWire, WirePlumber, and pipewire-pulse. It loads `voxyrn_to_gv` and `voxyrn_from_gv` only when those sink names are absent, and sets them as the default sink and the default source (`voxyrn_from_gv.monitor`).

`voxyrn.service` keeps `PrivateTmp=true` and bind-mounts `/tmp/.X11-unix` so Chromium can open Xvfb. The unit defaults `DISPLAY` to `:98` before `EnvironmentFile=`, so `.env` can set `VOXYRN_DISPLAY` or `DISPLAY`. Do not point Voxyrn at `:99`; that screen is laneora-chrome. `NoNewPrivileges=true` blocks Chromium's setuid sandbox, so the unit sets `VOXYRN_CHROMIUM_NO_SANDBOX=1`.

A saved Windows device name is rewritten on Linux: `CABLE-A Input` and `CABLE Input` become `voxyrn_to_gv` (TTS playback, the VB-CABLE input). `CABLE-A Output` and `CABLE Output` become `voxyrn_from_gv.monitor` (call capture, the VB-CABLE output). Numbered per-agent sinks are left as they are.

After pulling this update on a box that already ran the audio service:

```bash
sudo bash deploy/linux/install.sh
sudo systemctl restart voxyrn-audio voxyrn
sudo -u voxyrn env XDG_RUNTIME_DIR=/run/voxyrn PULSE_SERVER=unix:/run/voxyrn/pulse/native \
  /opt/voxyrn/.venv/bin/python -m src.main --audio-route-test
```

Preflight fails if a short `pacat` write to `voxyrn_to_gv` times out, which is the suspended-sink failure.

Before the first live run the process creates two null sinks:

- `voxyrn_to_gv` — Piper/Kokoro/Edge playback. Chromium's microphone is `voxyrn_to_gv.monitor`.
- `voxyrn_from_gv` — Chromium's speaker. Speech-to-text records `voxyrn_from_gv.monitor`.

Run Preflight, then Audio → test TTS and test capture, before a campaign. A VPS has no room speakers. Listen-in cannot play into a physical speaker unless the machine has one; use the Recordings tab. If Chromium does not attach to the sinks, the call will be one-way or silent even though the console is up. That is a browser/Pulse limitation, not something the installer can guarantee.

## 7. Concurrent agents

Voxyrn uses one Google Voice account, the Main line. That account can be signed in on several Chrome profiles (`VOXYRN_LINES`, ceiling 7), and each profile places one live call. A second Google Voice account is not admitted. The dashboard reports host headroom after `VOXYRN_RESERVED_CORES` (default 1) and `VOXYRN_RESERVED_RAM_GB` (default 8). On a 4-core / 24 GB host the measured sustainable count is 4. Arming more than that, up to 7, shows a warning.

The Main line uses `voxyrn_to_gv` for speech playback and `voxyrn_from_gv.monitor` for capture. Speech-to-text uses `STT_LOCAL_MODEL=tiny` and `STT_THREADS=1` (measured 26x realtime for an 8 second clip on one x86 thread; about 16x on ARM at the default 0.6 scale). The local LLM is one shared queue (`LLM_PARALLEL=1`, `LLM_THREADS=2`), or `LLAMA_SERVER_URL` if you run `llama-server --parallel 1`.

Calls on the Main line wait `ACCOUNT_MIN_GAP_SECONDS` (45) and stop after `ACCOUNT_MAX_CALLS_PER_HOUR` (30).

## 7b. Several Chrome lines, one Google Voice account

`VOXYRN_LINES` is how many Chrome profiles place a call at the same time on the Main line. The ceiling is 7. Each line is one live call. It does not add a second Google Voice account.

| Line | Profile | Audio | Display | VNC / noVNC |
| --- | --- | --- | --- | --- |
| 1 | `chrome_profiles/tony` if that session is already there | `voxyrn_to_gv_1` / `voxyrn_from_gv_1.monitor` | `:98` | 5911 / 6091 |
| 2 | `tony_2`, cloned from `tony` when the folder is missing | `voxyrn_to_gv_2` / `voxyrn_from_gv_2.monitor` | `:98`, separate window | 5912 / 6092 |
| N | `tony_N` | `voxyrn_to_gv_N` / `voxyrn_from_gv_N.monitor` | `:98`, or `:100+` if `VOXYRN_SEPARATE_DISPLAYS=1` | 5910+N / 6090+N |

These ports are not 5901, 5902, 6080, or 6081. Display `:99` is laneora-chrome and is not used. Line 1 is ready when `chrome_profiles/tony/.gv_session_ok` exists. The older `.voxyrn_session` file still counts. Startup copies tony into `tony_2` through `tony_N`. The lines page can clone again while that Chrome is closed, or open the profile on `:98` for a sign-in. Do not delete `chrome_profiles/tony` during a deploy.

`voxyrn-audio.service` loads `.env`, so `VOXYRN_LINES` creates the numbered sinks. On a 4-core host with 1 core reserved, the measured sustainable count is 4. The lines page lets an admin arm up to 7 and shows a warning past that count. This measurement is the x86 per-call cost (about 0.7 core and 500 MB for Chromium plus the worker) scaled for the ARM host. It is not a seven-way live call captured on the A1 box.

## 7c. Moving this build onto /opt/voxyrn

Keep the existing checkout, Google Voice session, `data/`, and scripts. From the new tree:

```bash
sudo systemctl stop voxyrn
cd /opt/voxyrn
sudo -u voxyrn git pull
sudo bash deploy/linux/install.sh
```

Add these to `/opt/voxyrn/.env` if they are missing. Leave `VOXYRN_DISPLAY=:98`.

```bash
VOXYRN_LINES=1
VOXYRN_SEPARATE_DISPLAYS=0
```

Raise `VOXYRN_LINES` only after the lines page shows the warning you are willing to accept. Then:

```bash
sudo systemctl restart voxyrn-audio voxyrn
```

`voxyrn-audio` creates `voxyrn_to_gv_1` through `voxyrn_to_gv_N` without removing `voxyrn_to_gv`. The first start creates Ashley if she is not already a user. Her password is written to `/opt/voxyrn/data/ashley.password` and is not printed. Lock that file to root:

```bash
sudo chown root:root /opt/voxyrn/data/ashley.password
sudo chmod 400 /opt/voxyrn/data/ashley.password
```

`data/users.json`, `data/lead_crm.json`, `data/call_scripts.json`, and `chrome_profiles/tony` stay in place. Confirm tony is still the line 1 profile before the first multi-line run. Open Admin CRM and Lines after login. Set Ashley active if a previous user row was pending, assign `truck-dispatch-sales` (Spanish calls can use `truck-dispatch-sales-es`), and import her CSV with the list name `truck-dispatch`.

## 8. Operators

The admin created from `VOXYRN_ADMIN_PASSWORD` opens **Users** and adds operator accounts. Passwords are PBKDF2-SHA256 hashes in `data/users.json`. Login attempts are limited to 8 failures per 15 minutes per IP in this process.

## 9. Scripts and extra voices

The call script is edited at `/scripts`. The only built-in template is the generic B2B appointment script. New script saves another script you write, and "Use this script on live calls" is what the voice agent follows (`ACTIVE_SCRIPT_ID` still overrides that choice). The page can type or hold-to-speak as the prospect. Nothing on that page dials.

`install.sh` fetches Piper lessac medium. More English and Spanish voices:

```bash
bash deploy/linux/download_voices.sh en_US-lessac-medium en_GB-alba-medium es_MX-ald-medium es_ES-davefx-medium
```

Files land in `models/piper` and are not committed. The catalog is English and Spanish only. Kokoro needs its own model download and was not timed on this host. Lessac medium measured 47.6x realtime on a 4-vCPU x86 box (12.3 seconds of speech in 0.26 seconds). `/voices` shows that factor and an estimate for the engines that were not timed.
