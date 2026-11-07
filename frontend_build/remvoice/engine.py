# -*- coding: utf-8 -*-
"""Local GPT-SoVITS service control and audio I/O (stdlib only)."""
import array
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import wave
from io import BytesIO

try:
    import winsound
except ImportError:  # pragma: no cover - Windows only app
    winsound = None

from .settings import (
    A_GEN,
    API,
    API_SCRIPT,
    APP_LOG,
    GPT_W,
    LOG_DIR,
    MPL_CACHE,
    NUMBA_CACHE,
    PYTHON,
    REPO,
    SAVE_DIR,
    SERVICE_CONFIG,
    SERVICE_ERR,
    SERVICE_OUT,
    SERVICE_TMP,
    SAMPLE_STEPS,
    SOVITS_W,
    TEMP,
    TEXT_SPLIT_METHOD,
    TONES_JSON,
    TOP_K,
    TOP_P,
    TTS_SEED,
    STREAM_TTS_MODE,
)
from .storage import append_log, rotate_log, tail_file

def http_get(path, params, timeout=10):
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(API + path + "?" + qs, timeout=timeout) as resp:
        return resp.read()


def append_log(path, text):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {str(text).rstrip()}\n")
    except Exception:
        pass


def tail_file(path, max_lines=14, max_chars=1800):
    try:
        with open(path, "rb") as f:
            data = f.read()
        text = data.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    text = "\n".join(lines[-max_lines:])
    if len(text) > max_chars:
        text = "…" + text[-max_chars:]
    return text


def service_env():
    """Give the venv Python a clean environment, not PyInstaller's runtime hooks."""
    env = dict(os.environ)
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "TCL_LIBRARY",
        "TK_LIBRARY",
        "_MEIPASS",
        "_MEIPASS2",
    ):
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    # Some launch contexts cannot write the user's %TEMP%; keep the service's
    # temporary files next to our own logs, which are already writable.
    os.makedirs(SERVICE_TMP, exist_ok=True)
    env["TMP"] = SERVICE_TMP
    env["TEMP"] = SERVICE_TMP
    env["TMPDIR"] = SERVICE_TMP
    # The frozen app's child token may not be allowed to write into
    # site-packages. Numba's default in-tree cache then retries forever on
    # __pycache__ permissions, so keep its cache inside our writable folder.
    os.makedirs(NUMBA_CACHE, exist_ok=True)
    os.makedirs(MPL_CACHE, exist_ok=True)
    env["NUMBA_CACHE_DIR"] = NUMBA_CACHE
    env["MPLCONFIGDIR"] = MPL_CACHE
    return env


def service_ready():
    try:
        http_get("/set_gpt_weights", {"weights_path": GPT_W}, timeout=4)
        return True
    except Exception:
        return False


def port_open():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", 9880))
        return True
    except OSError:
        return False
    finally:
        s.close()


def port_owner_pids(port):
    """Return PIDs listening on a local TCP port (IPv4 or IPv6)."""
    pids = []
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except Exception:
        return pids
    suffix = ":" + str(port)
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 2 or not parts[1].endswith(suffix):
            continue
        if parts[-1].isdigit():
            pids.append(parts[-1])
    return pids


def kill_port_owner(port):
    """Stop whatever listens on the given local port (used only to recover
    from a stale inference service)."""
    for pid in port_owner_pids(port):
        subprocess.run(
            ["taskkill", "/F", "/PID", pid],
            capture_output=True,
            timeout=15,
        )


def stop_service(pid):
    """Stop the service process tree started by this app.

    ``venv\\Scripts\\python.exe`` can re-exec ``api_v2.py`` in a child
    interpreter, so the PID that listens on 9880 is not always the PID we
    started.  ``/T`` stops the launcher and that child together.
    """
    if not pid:
        return
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        timeout=15,
    )



def start_service():
    """Start api_v2.py with real file handles for stdout/stderr.

    A PyInstaller --windowed process has no console handles. Passing
    subprocess.DEVNULL is not enough here: the console Python child needs a
    valid stdout/stderr from the first import. We also keep the child's output
    in logs so a failed wake-up is diagnosable.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    cmd = [PYTHON, API_SCRIPT, "-a", "127.0.0.1", "-p", "9880"]
    # GPT-SoVITS persists its active TTS config on startup and when weights
    # change. Its own configs folder may be read-only to the child process in
    # some launch environments, so run against a writable project-local copy.
    source_config = os.path.join(REPO, "GPT_SoVITS", "configs", "tts_infer.yaml")
    if os.path.exists(source_config):
        try:
            shutil.copyfile(source_config, SERVICE_CONFIG)
            cmd.extend(["-c", SERVICE_CONFIG])
            append_log(APP_LOG, f"服务配置副本：{SERVICE_CONFIG}")
        except OSError as exc:
            append_log(APP_LOG, f"复制 TTS 配置失败，使用原路径：{exc}")
    header = (
        "\n"
        + "=" * 70
        + "\n"
        + time.strftime("%Y-%m-%d %H:%M:%S")
        + " start api_v2.py\n"
        + "=" * 70
        + "\n"
    ).encode("utf-8")
    # The service appends forever; rotate old logs before opening new handles.
    rotate_log(SERVICE_OUT)
    rotate_log(SERVICE_ERR)
    with open(SERVICE_OUT, "ab", buffering=0) as out, open(
        SERVICE_ERR, "ab", buffering=0
    ) as err:
        out.write(header)
        err.write(header)
        proc = subprocess.Popen(
            cmd,
            cwd=REPO,
            env=service_env(),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=0x08000000,
        )
    append_log(APP_LOG, f"已启动推理服务，PID {proc.pid}")
    return proc


def ensure_service(log=print):
    """Reuse a running service or start one and wait until weights load."""
    if service_ready():
        log("推理服务已在运行，直接复用")
        return None
    if port_open():
        log("端口被残留进程占用，先清理")
        kill_port_owner(9880)
        time.sleep(2)
    log("正在启动本地推理服务…")
    proc = start_service()
    log(f"服务进程已拉起（PID {proc.pid}），正在加载模型…")
    for _ in range(150):
        time.sleep(1)
        if service_ready():
            log("服务启动完成")
            return proc
        code = proc.poll()
        if code is not None:
            tail = tail_file(SERVICE_ERR) or tail_file(SERVICE_OUT)
            detail = f"推理服务进程提前退出（代码 {code}）"
            if tail:
                detail += "：" + tail
            append_log(APP_LOG, detail)
            raise RuntimeError(detail)
    raise RuntimeError(
        "推理服务启动超时（150 秒）。请查看 logs\\app.log 和 logs\\service_stderr.log"
    )



def apply_weights():
    http_get("/set_gpt_weights", {"weights_path": GPT_W}, timeout=120)
    http_get("/set_sovits_weights", {"weights_path": SOVITS_W}, timeout=120)


def load_tones():
    """Load reference-audio tones, with a placeholder for a fresh install."""
    try:
        with open(TONES_JSON, encoding="utf-8-sig") as f:
            lib = json.load(f)
        tones = {}
        for key, item in lib["tones"].items():
            tones[key] = {
                "label": item["label"],
                "audio": os.path.join(A_GEN, lib["source_dir"], item["audio"]),
                "prompt": item["text"],
                "lang": item["lang"],
            }
        return tones, lib["default_tone"]
    except Exception:
        return {
            "default": {
                "label": "默认参考",
                "audio": "",
                "prompt": "",
                "lang": "ja",
            }
        }, "default"


def _assemble_stream_wav(chunks):
    """Rebuild a normal WAV from GPT-SoVITS streaming chunks.

    The first chunk is a 44-byte WAV header with a zero data length, followed
    by raw PCM in later chunks.  Rebuild the header so the saved file is
    seekable and replayable.
    """
    if not chunks:
        raise ValueError("TTS 流没有返回音频")
    first = chunks[0]
    data_at = first.find(b"data")
    if data_at < 0:
        raise ValueError("TTS 流不是 WAV 格式")
    header = first[: data_at + 8]
    raw = first[data_at + 8:] + b"".join(chunks[1:])
    with wave.open(BytesIO(header), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
    buffer = BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(raw)
    return buffer.getvalue()


def synth(
    text,
    tone,
    speed,
    sample_steps=None,
    timeout=600,
    text_split_method=None,
    seed=None,
    streaming_mode=0,
    text_lang="auto",
    on_chunk=None,
    cancel=None,
):
    """Synthesize one line.

    ``sample_steps`` is the CFM diffusion step count; the GPT-SoVITS default
    of 32 dominates latency.  Lower values are faster with a small quality
    trade-off, so it is configurable in ``settings.SAMPLE_STEPS``.

    ``text_split_method`` controls how often the CFM is re-run.  ``auto``
    keeps short chat lines in one pass (``cut0``) and only splits longer
    lines, which avoids paying the fixed cost once per sentence.
    """
    method = text_split_method or TEXT_SPLIT_METHOD
    if method == "auto":
        method = "cut0" if len(text) <= 60 else "cut2"
    body = {
        "text": text,
        "text_lang": text_lang or "auto",
        "ref_audio_path": tone["audio"],
        "prompt_text": tone["prompt"],
        "prompt_lang": tone["lang"],
        "media_type": "wav",
        "streaming_mode": int(streaming_mode or 0),
        "speed_factor": speed,
        "top_k": TOP_K,
        "top_p": TOP_P,
        "temperature": TEMP,
        "text_split_method": method,
        "sample_steps": int(
            SAMPLE_STEPS if sample_steps is None else sample_steps
        ),
        "seed": int(TTS_SEED if seed is None else seed),
    }
    req = urllib.request.Request(
        API + "/tts",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    if int(streaming_mode or 0):
        chunks = []
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            while True:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("语音生成已取消")
                chunk = resp.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if on_chunk is not None:
                    on_chunk(chunk)
        return _assemble_stream_wav(chunks)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def warmup_tts(tone, log=print):
    """Pay the first-inference initialization cost before the user asks.

    The first /tts call after a service start spends ~10-20 s setting up CUDA
    kernels and model state.  A one-word request here moves that wait into the
    "正在唤醒引擎" phase; failures are logged but never block readiness.
    """
    if not tone:
        return False
    log("正在预热语音…")
    try:
        synth(
            "你好。",
            tone,
            1.0,
            sample_steps=SAMPLE_STEPS,
            timeout=180,
            streaming_mode=STREAM_TTS_MODE,
        )
    except Exception as exc:
        log(f"语音预热失败，首句可能较慢：{exc}")
        return False
    log("语音预热完成")
    return True


def save_audio(data, prefix=""):
    """Write a WAV with a timestamp name, never overwriting an earlier take."""
    os.makedirs(SAVE_DIR, exist_ok=True)
    stamp = prefix + time.strftime("%Y%m%d_%H%M%S")
    millis = int(time.time() * 1000) % 1000
    path = os.path.join(SAVE_DIR, f"{stamp}_{millis:03d}.wav")
    suffix = 1
    while os.path.exists(path):
        path = os.path.join(SAVE_DIR, f"{stamp}_{millis:03d}_{suffix}.wav")
        suffix += 1
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return path


def merge_wavs(paths, prefix="聊天_"):
    """Concatenate same-format WAV chunks into one replayable file."""
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        return ""
    if len(paths) == 1:
        return paths[0]
    chunks = []
    params = None
    for path in paths:
        with wave.open(path, "rb") as w:
            current = (
                w.getnchannels(),
                w.getsampwidth(),
                w.getframerate(),
            )
            if params is None:
                params = current
            elif current != params:
                raise ValueError("WAV 片段格式不一致，无法合并")
            chunks.append(w.readframes(w.getnframes()))
    buffer = BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        out.writeframes(b"".join(chunks))
    return save_audio(buffer.getvalue(), prefix=prefix)



def convert_for_playback(src_path, dst_path, target_rate=44100):
    """Convert a WAV to 16-bit mono 44.1 kHz for winsound.

    The old code stepped to 16 kHz with nearest-neighbour sampling, which is
    audible on speech.  Prefer audioop's proper resampler and only fall back
    to the simple loop if it is unavailable.
    """
    with wave.open(src_path, "rb") as w:
        params = w.getparams()
        frames = w.readframes(params.nframes)
    src_ch, src_width, src_rate = params.nchannels, params.sampwidth, params.framerate
    if src_width != 2:
        raise ValueError("仅支持 16 位 WAV 转换")
    if src_ch not in (1, 2):
        raise ValueError("仅支持单声道或立体声 WAV")

    out_frames = None
    try:
        import audioop

        if src_ch == 2:
            frames = audioop.tomono(frames, 2, 0.5, 0.5)
        out_frames, _ = audioop.ratecv(frames, 2, 1, src_rate, target_rate, None)
    except Exception:
        out_frames = None

    if out_frames is None:
        samples = array.array("h", frames)
        if src_ch == 2:
            mono = array.array(
                "h",
                (
                    samples[i] // 2 + samples[i + 1] // 2
                    for i in range(0, len(samples) - 1, 2)
                ),
            )
        else:
            mono = samples
        ratio = src_rate / float(target_rate)
        out_frames = array.array(
            "h",
            (
                mono[min(len(mono) - 1, int(i * ratio))]
                for i in range(max(1, int(len(mono) / ratio)))
            ),
        ).tobytes()

    with wave.open(dst_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(target_rate)
        w.writeframes(out_frames)


convert_to_16k_mono = convert_for_playback



def play_wav(path):
    """Play a WAV, converting only when the device rejects the source format."""
    if winsound is not None:
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME)
            return
        except RuntimeError:
            pass
    tmp = os.path.join(
        SERVICE_TMP, f"_play_{os.getpid()}_{int(time.time() * 1000)}.wav"
    )
    try:
        os.makedirs(SERVICE_TMP, exist_ok=True)
        convert_for_playback(path, tmp)
        if winsound is not None:
            winsound.PlaySound(tmp, winsound.SND_FILENAME)
            return
    except Exception:
        pass
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    os.startfile(path)


def stop_playback():
    """Interrupt the currently playing winsound buffer, if any."""
    if winsound is not None:
        try:
            winsound.PlaySound(None, 0)
        except RuntimeError:
            pass

