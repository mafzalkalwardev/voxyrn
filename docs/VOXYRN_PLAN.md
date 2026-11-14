# Voxyrn plan

Voxyrn is the next dialer. It starts from the Routelix operator console (FastAPI, one Google Voice account, up to seven Chrome profiles, English and Spanish voices, the script builder, and the admin CRM) and moves the realtime path into Rust. The Oracle ARM VPS has no NVIDIA GPU, so CUDA is not part of that host.

## What runs now

- The Python console, scripts, CRM, lines, and Pulse sinks, rebranded from Routelix.
- `rust/crates/voxyrn-audio`: one call engine with a fixed 8-second PCM ring (512 KB, it does not grow), barge-in, the first-turn gate, 440+480 Hz ringback, and the voicemail beep. Sentence chunking starts TTS on the first clause. The capture loop calls this through `voxyrn_core` when that module is built.
- CUDA is a real optional build of the per-frame front end (`cuda/front_end.cu`, `cargo build --features cuda`, needs `nvcc`). It is not compiled on this host or on the ARM VPS. When `nvidia-smi` lists a GPU, faster-whisper loads with float16 on CUDA and the local GGUF model sets `n_gpu_layers=-1`. `VOXYRN_CUDA=0` forces the CPU path.

## What comes next, in this order

1. Stream the first sentence of a reply to Piper or Kokoro while the rest of the reply is still generating. The chunker is in place. Play a cached filler if the model is still working after 600 ms.
2. Replace Selenium DOM polling for pickup with a Chrome DevTools hook on `RTCPeerConnection`. The Rust ringback detector is already in the capture path. Keep the DOM path until that hook is proven on a live Google Voice call.
3. Send TTS into the tab and capture the remote track with an AudioWorklet, so each Chrome profile no longer needs a virtual cable. Pulse sinks stay until that path is measured.
4. Move leads, call logs, and transcripts to SQLite in WAL mode. Keep the JSON files as the import source for the first migration.
5. A Tauri shell and a hardware-id license check, after the call loop is the Rust one. Do not package a PyInstaller executable for Voxyrn.

## CUDA

On a machine with an NVIDIA GPU, faster-whisper, ONNX Runtime, and llama.cpp can use CUDA. That is the path for sub-second turns. The current 4-core ARM VPS stays on int8 whisper and Piper on CPU. Do not install a CUDA stack there.

## Laneora

Laneora is a separate DAT session client. Its useful ideas for Voxyrn are a small Rust service, SQLite, and a native Windows shell. The Surfshark WireGuard proxy in the Laneora notes is for DAT, not for Google Voice. Do not put the dialer's Google Voice traffic through that tunnel.

## Hostname and the VPS

`https://voxyrn.induswebagency.com` is only the renamed placeholder in the landing page. It is not a registered site and it is not deployed. The Linux kit still installs like the Routelix kit, with `voxyrn` unit names and `voxyrn_to_gv` sinks, so it can sit beside the existing Routelix install. This agent cannot SSH to the VPS, so nothing has been restarted there.
