# RemVoice frontend

The desktop shell for a local GPT-SoVITS voice companion. It deliberately keeps
the Tk exe small: the inference service and the ASR worker run in the
`a-gen` virtualenv, and the exe only speaks HTTP/stdin-stdout to them.

## Structure

| File | Purpose |
|---|---|
| `rem_gui.py` | Tk window, pages, dialogs, app lifecycle |
| `remvoice/chat.py` | ChatSession + one chat turn (streaming/blocking) |
| `remvoice/engine.py` | GPT-SoVITS service control, `/tts`, WAV I/O, playback |
| `remvoice/llm.py` | OpenAI-compatible client, Japanese translation, model list |
| `remvoice/memory.py` | Decaying memory card + summary + archive |
| `remvoice/storage.py` | Atomic JSON, logs, chat archive |
| `remvoice/secrets.py` | Windows DPAPI wrapper for the API key |
| `remvoice/asr.py` | Process wrapper for `asr_worker.py` |
| `asr_worker.py` | Standalone mic capture + SenseVoice/faster-whisper |
| `remvoice/text.py` | Action/speech parsing, punctuation, TTS text prep |
| `remvoice/theme.py`, `widgets.py`, `chat_ui.py` | UI layer |
| `tests/` | 103 pure-logic tests |

## Chat configuration

Stored in `<data_root>\聊天记录\chat_config.json`; edit it in the app:

- connection: `api_base`, `api_key`, `model`, `temperature`, `max_tokens`
- names: `user_name`, `user_short`
- persona: `persona` (generic default; put your own character text here)
- voice: `voice_lang`, `auto_speak`, `display_turns`
- memory: `memory_*`
- translation: `translate_api_base`, `translate_model`, `translate_api_key`
- ASR: `asr_engine`, `asr_model`, `asr_device`, `asr_compute_type`,
  `asr_language`, `asr_preload`

The API key is written as `api_key_enc` using Windows DPAPI. Old plaintext
configs are migrated on load, and the `.bak` plaintext key is scrubbed.

## Speech input

- Hold the「按住说话」button or `F2`; release to transcribe.
- Text is placed in the composer, not sent automatically.
- Starting to speak stops the current reply and playback (barge-in).
- SenseVoice is the default (`iic/SenseVoiceSmall`); it downloads once to the
  ModelScope cache. Faster-whisper can be selected with
  `{"asr_engine": "whisper", "asr_model": "base"}`.
- The worker uses `a-gen\venv`; install `sounddevice`, `funasr`, `modelscope`
  with `requirements_asr.txt`. Audio is kept in memory only.

## Logs and data

Defaults live under `data_root` (exe directory when frozen):

```text
聊天记录/     chat_config.json, chat_history.json, chat_archive.jsonl, memory
生成音频/     generated WAVs
logs/         app.log, service logs, asr_stderr.log, asr_models/
```

`logs/` and `聊天记录/` are ignored by git.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The script auto-detects `a-gen\venv`, embeds `asr_worker.py`, and optionally
uses `assets/` and `remvoice_icon.ico` if present.

## Tests

```powershell
python -m unittest discover -s tests
```

The suite covers text/chat/streaming, memory, storage/archive, DPAPI migration,
ASR protocol, local endpoints and voice routing.
