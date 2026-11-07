# -*- coding: utf-8 -*-
"""Process wrapper around ``asr_worker.py``.

The Tk exe deliberately does not bundle torch/faster-whisper.  This client
starts the worker with the project's ``a-gen`` Python and exchanges one JSON
object per line over stdin/stdout.
"""
import json
import os
import subprocess
import threading


class AsrService:
    def __init__(
        self,
        python,
        script,
        model,
        device="cpu",
        compute_type="int8",
        language="auto",
        beam_size=1,
        download_root="",
        stderr_path="",
        on_event=None,
        log=None,
        engine="sensevoice",
    ):
        self.python = str(python)
        self.script = str(script)
        self.model = str(model)
        self.device = str(device)
        self.compute_type = str(compute_type)
        self.language = str(language)
        self.beam_size = int(beam_size)
        self.download_root = str(download_root or "")
        self.stderr_path = str(stderr_path or "")
        self.on_event = on_event
        self.log = log or (lambda _m: None)
        self.engine = str(engine or "sensevoice")

        self._proc = None
        self._reader = None
        self._stderr = None
        self._closing = False
        self._lock = threading.Lock()

    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def start(self):
        with self._lock:
            if self.alive:
                return True
            if not os.path.exists(self.python):
                raise RuntimeError("找不到 a-gen 的 Python：%s" % self.python)
            if not os.path.exists(self.script):
                raise RuntimeError("找不到 asr_worker.py：%s" % self.script)
            directory = os.path.dirname(self.stderr_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            if self.stderr_path:
                from .storage import rotate_log

                rotate_log(self.stderr_path, 10 * 1024 * 1024, 2)
            self._stderr = open(self.stderr_path, "ab", buffering=0)
            command = [
                self.python,
                self.script,
                "--engine",
                self.engine,
                "--model",
                self.model,
                "--device",
                self.device,
                "--compute-type",
                self.compute_type,
                "--language",
                self.language,
                "--beam-size",
                str(self.beam_size),
            ]
            if self.download_root:
                command += ["--download-root", self.download_root]
            self._closing = False
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONUTF8"] = "1"
            try:
                self._proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=self._stderr,
                    env=env,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=0x08000000,
                )
            except Exception:
                try:
                    self._stderr.close()
                except Exception:
                    pass
                self._stderr = None
                raise
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            return True

    def _read_loop(self):
        proc = self._proc
        try:
            if proc is not None and proc.stdout is not None:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(event, dict) and self.on_event is not None:
                        try:
                            self.on_event(event)
                        except Exception:
                            pass
        finally:
            if not self._closing:
                self._dispatch(
                    {
                        "event": "error",
                        "stage": "process",
                        "message": "语音识别进程已退出，请检查 logs\\asr_stderr.log",
                    }
                )

    def _dispatch(self, event):
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                pass

    def send(self, payload):
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            return True
        except Exception as exc:
            self.log("ASR 发送失败：%s" % exc)
            return False

    def begin(self):
        if not self.start():
            return False
        return self.send({"cmd": "start"})

    def end(self):
        return self.send({"cmd": "stop"})

    def cancel(self):
        return self.send({"cmd": "cancel"})

    def close(self):
        proc = self._proc
        self._closing = True
        if proc is not None and proc.poll() is None:
            self.send({"cmd": "quit"})
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        for stream in (
            getattr(proc, "stdin", None),
            getattr(proc, "stdout", None),
        ):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        try:
            if self._stderr is not None:
                self._stderr.close()
        except Exception:
            pass
        self._stderr = None
        self._proc = None
