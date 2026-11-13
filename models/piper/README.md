# Piper voice models

Place a Piper voice model and its matching JSON metadata file in this directory.

Example:

```text
models/piper/en_US-lessac-medium.onnx
models/piper/en_US-lessac-medium.onnx.json
```

Voice model files are intentionally not committed to this public repository. Set
`TTS_ENGINE=piper` and `PIPER_MODEL_PATH` in `.env` after downloading a compatible
Piper voice model.

The current local test voice is `en_US-lessac-medium`, downloaded from the
`rhasspy/piper-voices` Hugging Face repository. Keep the `.onnx` and matching
`.onnx.json` files together; both are required at runtime.

Download them with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\download_piper_voice.ps1
```
