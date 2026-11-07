# RemVoice

A local-first desktop voice companion shell: Tk UI, local GPT-SoVITS TTS, an
OpenAI-compatible chat backend, long-term memory, chat archive and push-to-talk
speech input.

This repository contains **code only**. It does **not** contain character art,
persona text, voice models, training audio, chat records, or API keys. The
default assistant is a generic "助手" (Assistant); replace it with a character
you have the rights to use.

## Features

- Voice studio: multi-line scripts, local GPT-SoVITS synthesis, recent audio.
- Chat: streamed LLM replies, action/speech parsing, Japanese translation mode,
  auto-speak, replay, regenerate/delete/export.
- Memory: decaying memory card + rolling summary + append-only full archive.
- Chat archive viewer: search/copy raw turns beyond the recent 200-message window.
- Speech input: hold the button / `F2`, release to transcribe; speaking stops
  the current reply (barge-in). SenseVoice by default, faster-whisper optional.
- Local-first data: chat history, memory and audio stay on this machine.
- API key encrypted at rest with Windows DPAPI.

## Requirements

- Windows 10/11, Python 3.12 (the Tk exe itself is built with PyInstaller).
- A local GPT-SoVITS checkout with `api_v2.py`, weights and `a-genenv`.
- `ffmpeg`/`ffplay` for the best playback path (optional fallback exists).
- For speech input: `sounddevice` plus FunASR/SenseVoice, installed into the
  `a-gen` virtualenv:
  `a-gen\venv\Scripts\python.exe -m pip install -r frontend_build\requirements_asr.txt`

## Run from source

```powershell
cd frontend_build
..\..\a-gen\venv\Scripts\python.exe rem_gui.py --ui-preview
```

`--ui-preview` starts the UI without loading GPT-SoVITS. For the real voice
engine, run `rem_gui.py` normally after setting `a_gen` in `config.json`.

## Configure

Place `config.json` next to `RemVoice.exe` (or in `frontend_build` when running
from source):

```json
{
  "a_gen": "D:/AI/a-gen",
  "data_root": "D:/RemVoice",
  "gpt_weights": "GPT_weights_v4/your_model.ckpt",
  "sovits_weights": "SoVITS_weights_v4/your_model.pth",
  "speed": 0.85,
  "sample_steps": 8,
  "seed": 2,
  "asr_engine": "sensevoice",
  "asr_model": "iic/SenseVoiceSmall",
  "asr_device": "cpu",
  "asr_preload": true
}
```

`data_root` decides the default location of `聊天记录` / `生成音频` / `logs`.
`a_gen` is auto-detected from the config, `REMVOICE_A_GEN`, or a sibling `a-gen`
directory. API settings and persona live in
`<data_root>\聊天记录\chat_config.json` and are edited in the app's Settings
panel; the API key is encrypted with DPAPI and never committed.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File .\frontend_build\build_exe.ps1
```

The build script finds `a-gen\venv` automatically (or uses
`REMVOICE_A_GEN`). It works without `assets/` and `remvoice_icon.ico`; drop in
your own images if you want a custom avatar.

## Tests

```powershell
cd frontend_build
python -m unittest discover -s tests
```

103 pure-logic tests; no GPU, network or Tk required.

## Privacy and rights

- Do not commit `config.json`, `chat_config.json`, `聊天记录/`, `logs/`,
  `生成音频/`, model weights or reference audio.
- Voice cloning and character voices are subject to the rights of the original
  performers/rights holders. This repository ships no voice data or character
  assets; use audio you are allowed to use.
- The code is MIT-licensed (see `LICENSE`); third-party dependencies and model
  weights keep their own licenses (see `NOTICE.md`).

## Layout

```text
frontend_build/
  rem_gui.py          Tk window and page wiring
  asr_worker.py       standalone SenseVoice/faster-whisper worker
  remvoice/           text, storage, secrets, engine, llm, chat, memory, asr, UI
  tests/              103 unit tests
  build_exe.ps1       PyInstaller build
```
