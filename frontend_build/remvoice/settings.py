# -*- coding: utf-8 -*-
"""Filesystem locations, model selection and generation parameters.

Everything here can be overridden with an optional ``config.json`` placed
next to ``RemVoice.exe`` (or next to ``rem_gui.py`` when running from source).
Only the keys that are present override the defaults, so an absent or empty
file reproduces the original setup exactly.

Example ``config.json``::

    {
      "a_gen": "D:/AI/a-gen",
      "save_dir": "D:/RemVoice/生成音频",
      "gpt_weights": "GPT_weights_v4/remfull-e16.ckpt",
      "sovits_weights": "SoVITS_weights_v4/remfull_e4_s3804_l32.pth",
      "speed": 0.85,
      "keep_engine_running": true
    }
"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = getattr(sys, "_MEIPASS", PROJECT_ROOT)
APP_DIR = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, "frozen", False)
    else PROJECT_ROOT
)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")


def _local_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


_LOCAL = _local_config()


def _text(key, default):
    value = _LOCAL.get(key)
    if value is None or str(value).strip() == "":
        return default
    return os.path.expandvars(os.path.expanduser(str(value)))


def _number(key, default, cast=float):
    try:
        return cast(_LOCAL.get(key, default))
    except (TypeError, ValueError):
        return default


def _flag(key, default):
    value = _LOCAL.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


SOURCE_ROOT = os.path.dirname(PROJECT_ROOT)
DEFAULT_DATA_ROOT = APP_DIR if getattr(sys, "frozen", False) else SOURCE_ROOT
DATA_ROOT = _text("data_root", DEFAULT_DATA_ROOT)

DEFAULT_A_GEN = os.path.join(APP_DIR, "a-gen")
_ENV_A_GEN = str(os.environ.get("REMVOICE_A_GEN") or "").strip()
A_GEN = _text("a_gen", _ENV_A_GEN)
if not A_GEN:
    # Keep the known path working, but also let a moved/portable copy find a
    # sibling a-gen directory instead of failing on a hard-coded absolute path.
    _A_GEN_CANDIDATES = [
        DEFAULT_A_GEN,
        os.path.join(APP_DIR, "a-gen"),
        os.path.join(os.path.dirname(APP_DIR), "a-gen"),
        os.path.join(os.path.expanduser("~"), "a-gen"),
    ]
    A_GEN = next(
        (path for path in _A_GEN_CANDIDATES if os.path.isdir(path)),
        DEFAULT_A_GEN,
    )
REPO = _text("repo", os.path.join(A_GEN, "GPT-SoVITS"))
PYTHON = _text("python", os.path.join(A_GEN, "venv", "Scripts", "python.exe"))
API_SCRIPT = os.path.join(REPO, "api_v2.py")

TONES_JSON = _text("tones_json", os.path.join(A_GEN, "work", "ref_prompts.json"))
CHAT_RECORD_DIR = _text(
    "chat_record_dir", os.path.join(DATA_ROOT, "聊天记录")
)
CHAT_CONFIG = _text(
    "chat_config", os.path.join(CHAT_RECORD_DIR, "chat_config.json")
)
LEGACY_CHAT_CONFIG = _text(
    "legacy_chat_config", os.path.join(A_GEN, "work", "chat_config.json")
)
CHAT_HISTORY = os.path.join(CHAT_RECORD_DIR, "chat_history.json")
CHAT_ARCHIVE = _text(
    "chat_archive", os.path.join(CHAT_RECORD_DIR, "chat_archive.jsonl")
)
MEMORY_PATH = os.path.join(CHAT_RECORD_DIR, "chat_memory.json")
MEMORY_ARCHIVE = os.path.join(CHAT_RECORD_DIR, "chat_memory_full.jsonl")
SAVE_DIR = _text("save_dir", os.path.join(DATA_ROOT, "生成音频"))
PERSONA_CARD = _text("persona_card", os.path.join(APP_DIR, "角色卡", "persona.json"))

LOG_DIR = _text("log_dir", os.path.join(DATA_ROOT, "logs"))
APP_LOG = os.path.join(LOG_DIR, "app.log")
SERVICE_OUT = os.path.join(LOG_DIR, "service_stdout.log")
SERVICE_ERR = os.path.join(LOG_DIR, "service_stderr.log")
SERVICE_TMP = _text("service_tmp", os.path.join(LOG_DIR, "tmp"))
NUMBA_CACHE = os.path.join(LOG_DIR, "numba_cache")
MPL_CACHE = os.path.join(LOG_DIR, "mpl_cache")
SERVICE_CONFIG = os.path.join(LOG_DIR, "tts_infer.yaml")

# Speech-to-text worker.  It runs under the a-gen venv because the Tk exe does
# not bundle faster-whisper/torch.  "small" is downloaded once into
# logs\asr_models; a local model directory can be supplied via "asr_model".
ASR_WORKER = _text("asr_worker", os.path.join(BASE_DIR, "asr_worker.py"))
ASR_ENGINE = _text("asr_engine", "sensevoice")
ASR_MODEL = _text("asr_model", "iic/SenseVoiceSmall")
ASR_MODEL_DIR = _text("asr_model_dir", os.path.join(LOG_DIR, "asr_models"))
ASR_DEVICE = _text("asr_device", "cpu")
ASR_COMPUTE = _text("asr_compute_type", "int8")
ASR_LANGUAGE = _text("asr_language", "auto")
ASR_BEAM = max(1, _number("asr_beam_size", 1, int))
ASR_PRELOAD = _flag("asr_preload", True)
ASR_STDERR = os.path.join(LOG_DIR, "asr_stderr.log")

ASSET_DIR = os.path.join(BASE_DIR, "assets")
AVATAR_ASSET = os.path.join(ASSET_DIR, "rem_avatar.png")
AVATAR_SMALL_ASSET = os.path.join(ASSET_DIR, "rem_avatar_36.png")
PORTRAIT_ASSET = os.path.join(ASSET_DIR, "rem_sidebar.png")

API = _text("api", "http://127.0.0.1:9880")
GPT_W = _text("gpt_weights", "GPT_weights_v4/remfull-e16.ckpt")
SOVITS_W = _text("sovits_weights", "SoVITS_weights_v4/remfull_e4_s3804_l32.pth")

SPEED = _number("speed", 0.85)
TOP_K = _number("top_k", 15, int)
TOP_P = _number("top_p", 1.0)
TEMP = _number("temperature", 1.0)
MAX_HISTORY_TURNS = _number("max_history_turns", 20, int)

# GPT-SoVITS v4 defaults to 32 diffusion steps, which dominates response
# latency on a laptop GPU.  8 steps measured ~6x faster on a short line with
# little text change; raise to 16 if the 8-step samples sound rough.
SAMPLE_STEPS = max(1, min(32, _number("sample_steps", 8, int)))

# GPT-SoVITS normally re-runs the CFM for every sentence (cut5).  For short
# chat lines one pass is both faster and keeps more context: auto = cut0 up
# to 60 chars, cut2 above that.  Set "cut5" to restore the old split.
TEXT_SPLIT_METHOD = _text("text_split_method", "auto")

# Fixed sampling seed.  The user compared seeds on a fixed line and picked 2;
# a fixed seed also makes repeated lines reproducible.  Set -1 for random.
TTS_SEED = _number("seed", 2, int)

# Run one throw-away sentence after the service reports ready.  The first
# inference after a service start pays CUDA/weights initialization (10-20 s);
# paying it while the UI says "warming up" keeps the first chat reply fast.
WARMUP = _flag("warmup", True)

# Stream LLM text and synthesize sentence-by-sentence so the first audio can
# start before the full reply is ready.  Set false to use the old blocking
# "full reply -> one TTS request -> play" path.
STREAM_VOICE = _flag("stream_voice", True)

# GPT-SoVITS streaming_mode: 1 best quality, 2 medium, 3 fastest.  The app
# reassembles the streamed WAV locally; mode 2 measured ~2.1s first chunk and
# ~2.5s total for a 42-char reply, versus ~7.5s non-streaming.
STREAM_TTS_MODE = max(0, min(3, _number("stream_tts_mode", 2, int)))

# The original build kept the inference service alive after closing the
# window (the next launch then reuses it).  Set this to false to stop only the
# service process that this app started on exit.
KEEP_ENGINE_RUNNING = _flag("keep_engine_running", True)
