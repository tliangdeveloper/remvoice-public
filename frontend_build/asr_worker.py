# -*- coding: utf-8 -*-
"""Standalone speech-to-text worker for RemVoice.

The GUI reserves its single-file exe for Tk and the existing TTS client, so the
microphone/ASR runtime lives in the ``a-gen`` virtualenv instead.  The parent
process talks to this worker through stdin/stdout JSON lines:

    -> {"cmd": "start"}
    <- {"event": "recording"}
    -> {"cmd": "stop"}
    <- {"event": "transcribing"}
    <- {"event": "result", "text": "...", "language": "zh"}

Model loading starts in a background thread, so the first press can already
record while faster-whisper is warming up.  Audio is kept in memory and never
written to disk.
"""
import argparse
import json
import os
import sys
import threading
import time

# The parent decodes our protocol as UTF-8; a redirected Windows stdout would
# otherwise default to the active code page and garble Chinese/Japanese text.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAMPLE_RATE = 16000
_WRITE_LOCK = threading.Lock()


def emit(event, **payload):
    payload["event"] = event
    with _WRITE_LOCK:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def log(message):
    try:
        sys.stderr.write(
            "[%s] %s\n" % (time.strftime("%H:%M:%S"), str(message).rstrip())
        )
        sys.stderr.flush()
    except Exception:
        pass


class Recorder:
    """16 kHz mono int16 capture from the default input device."""

    def __init__(self):
        self._stream = None
        self._chunks = []
        self._lock = threading.Lock()
        self.active = False

    def start(self):
        if self.active:
            return
        try:
            import sounddevice as sd
        except Exception as exc:
            raise RuntimeError(
                "麦克风组件 sounddevice 未安装（在 a-gen\\venv 执行 "
                "pip install sounddevice）：%s" % exc
            )
        self._chunks = []
        try:
            self._stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=1600,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise RuntimeError("无法打开麦克风：%s" % exc)
        self.active = True

    def _callback(self, indata, _frames, _time, status):
        if status:
            log("录音状态：%s" % status)
        with self._lock:
            self._chunks.append(bytes(indata))

    def stop(self):
        self.active = False
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            data = b"".join(self._chunks)
            self._chunks = []
        if not data:
            return None
        try:
            import numpy as np

            return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception as exc:
            log("音频解码失败：%s" % exc)
            return None


class ModelLoader:
    """Load SenseVoice (default) or faster-whisper in the background."""

    def __init__(self, engine, model, device, compute_type, download_root):
        self.engine = (engine or "sensevoice").strip().lower()
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root or None
        self.model = None
        self.error = ""
        self.ready = threading.Event()

    def start(self):
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        emit("loading", engine=self.engine)
        try:
            if self.engine == "whisper":
                self._load_whisper()
            else:
                self.engine = "sensevoice"
                self._load_sensevoice()
            log("ASR 模型就绪（%s）" % self.engine)
            emit("ready", model=str(self.model_name), engine=self.engine)
        except Exception as exc:
            self.error = str(exc)
            log("ASR 模型加载失败：%s" % exc)
            emit("error", stage="model", message=self.error, engine=self.engine)
        finally:
            self.ready.set()

    def _load_sensevoice(self):
        from funasr import AutoModel

        log("加载 SenseVoice：%s（%s）" % (self.model_name, self.device))
        self.model = AutoModel(
            model=self.model_name,
            device=self.device,
            disable_update=True,
        )

    def _load_whisper(self):
        from faster_whisper import WhisperModel

        log(
            "加载 Whisper：%s（%s/%s）"
            % (self.model_name, self.device, self.compute_type)
        )
        self.model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
        )

    def transcribe(self, audio, language, beam_size, initial_prompt=""):
        if not self.ready.wait(180):
            raise RuntimeError("ASR 模型加载超时")
        if self.error or self.model is None:
            raise RuntimeError(self.error or "ASR 模型没有加载")
        if self.engine == "whisper":
            return self._transcribe_whisper(
                audio, language, beam_size, initial_prompt
            )
        return self._transcribe_sensevoice(audio, language)

    def _transcribe_whisper(self, audio, language, beam_size, initial_prompt):
        lang = str(language or "").strip().lower()
        language_arg = None if lang in ("", "auto", "none") else language
        segments, info = self.model.transcribe(
            audio,
            language=language_arg,
            beam_size=max(1, int(beam_size)),
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=str(initial_prompt or "").strip() or None,
        )
        text = "".join(segment.text for segment in segments).strip()
        return (
            text,
            getattr(info, "language", ""),
            getattr(info, "language_probability", 0.0),
        )

    def _transcribe_sensevoice(self, audio, language):
        lang = str(language or "").strip().lower()
        language_arg = "auto" if lang in ("", "auto", "none") else language
        result = self.model.generate(
            input=audio,
            language=language_arg,
            use_itn=True,
            batch_size_s=60,
        )
        raw = ""
        if result:
            raw = str(result[0].get("text") or "")
        import re

        tags = re.findall(r"<\|([^|]+)\|>", raw)
        text = re.sub(r"<\|[^|]+\|>", "", raw)
        text = re.sub(r"\s+", " ", text).strip()
        detected = ""
        for tag in tags:
            if tag in ("zh", "en", "ja", "ko", "yue"):
                detected = tag
                break
        return text, detected, 1.0



def _read_wav(path):
    try:
        import numpy as np
        import soundfile as sf
    except Exception as exc:
        raise RuntimeError("读取 WAV 需要 soundfile：%s" % exc)
    data, rate = sf.read(path, dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        if len(data) == 0:
            return data
        target = max(1, int(round(len(data) * SAMPLE_RATE / float(rate))))
        source = np.arange(len(data), dtype=np.float64)
        positions = np.linspace(0, len(data) - 1, target, dtype=np.float64)
        data = np.interp(positions, source, data).astype(np.float32)
    return data.astype(np.float32)


def _run_file(args, loader):
    audio = _read_wav(args.file)
    if audio is None or len(audio) == 0:
        emit("result", text="", language="", reason="empty")
        return 0
    emit("transcribing")
    try:
        text, language, probability = loader.transcribe(
            audio, args.language, args.beam_size, args.initial_prompt
        )
    except Exception as exc:
        emit("error", stage="transcribe", message=str(exc))
        return 1
    emit(
        "result",
        text=text,
        language=language,
        probability=round(float(probability or 0), 3),
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default="sensevoice")
    parser.add_argument("--model", default="iic/SenseVoiceSmall")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--download-root", default="")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--beam-size", default=1, type=int)
    parser.add_argument("--initial-prompt", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--mic-test", type=float, default=0.0)
    args = parser.parse_args(argv)

    loader = ModelLoader(
        args.engine,
        args.model,
        args.device,
        args.compute_type,
        args.download_root,
    )
    if args.file or args.mic_test:
        loader._load()
        if args.file:
            return _run_file(args, loader)
        recorder = Recorder()
        try:
            recorder.start()
            emit("recording")
            time.sleep(max(0.2, args.mic_test))
            audio = recorder.stop()
        except Exception as exc:
            emit("error", stage="recording", message=str(exc))
            return 1
        if audio is None or len(audio) < SAMPLE_RATE * 0.2:
            emit("result", text="", language="", reason="empty")
            return 0
        emit("transcribing")
        try:
            text, language, probability = loader.transcribe(
                audio, args.language, args.beam_size, args.initial_prompt
            )
        except Exception as exc:
            emit("error", stage="transcribe", message=str(exc))
            return 1
        emit(
            "result",
            text=text,
            language=language,
            probability=round(float(probability or 0), 3),
        )
        return 0

    loader.start()
    recorder = Recorder()
    emit("starting")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception:
            continue
        command = str(request.get("cmd") or "")
        if command == "start":
            try:
                recorder.stop()
                recorder.start()
                emit("recording")
            except Exception as exc:
                emit("error", stage="recording", message=str(exc))
        elif command == "stop":
            audio = recorder.stop()
            if audio is None or len(audio) < SAMPLE_RATE * 0.25:
                emit("result", text="", language="", reason="too_short")
                continue
            emit("transcribing")
            try:
                text, language, probability = loader.transcribe(
                    audio, args.language, args.beam_size, args.initial_prompt
                )
            except Exception as exc:
                emit("error", stage="transcribe", message=str(exc))
                continue
            emit(
                "result",
                text=text,
                language=language,
                probability=round(float(probability or 0), 3),
            )
        elif command == "cancel":
            recorder.stop()
            emit("cancelled")
        elif command == "quit":
            recorder.stop()
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
