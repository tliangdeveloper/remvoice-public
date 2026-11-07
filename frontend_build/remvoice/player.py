# -*- coding: utf-8 -*-
"""Sequential audio playback on a background thread.

Chat synthesis produces several WAV chunks (first sentence, then grouped
sentences).  The chat worker keeps synthesizing while this player drains the
queue, so playback of sentence 1 overlaps synthesis of sentence 2.
"""
import queue
import shutil
import subprocess
import threading

from .engine import play_wav, stop_playback


class AudioPlayer:
    def __init__(self, play=play_wav, stop=stop_playback):
        self._play = play
        self._stop_fn = stop
        self._queue = queue.Queue()
        self._stopped = threading.Event()
        self._done = threading.Event()
        self._thread = None

    def start(self):
        self._queue = queue.Queue()
        self._stopped.clear()
        self._done.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        try:
            while True:
                path = self._queue.get()
                if path is None:
                    break
                if self._stopped.is_set():
                    continue
                try:
                    self._play(path)
                except Exception:
                    # A single failed chunk must not kill the turn.
                    pass
        finally:
            self._done.set()

    def put(self, path):
        if not self._stopped.is_set():
            self._queue.put(path)

    def finish(self):
        self._queue.put(None)

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    def stop(self):
        self._stopped.set()
        try:
            self._stop_fn()
        except Exception:
            pass
        # Drain queued chunks and wake the worker.
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(None)


class StreamingPlayer:
    """Feed streaming WAV chunks to ffplay so audio starts on the first chunk.

    GPT-SoVITS returns a WAV header plus raw PCM chunks.  ffplay can consume
    that growing stream on stdin; if ffplay is unavailable the caller falls
    back to the file-based AudioPlayer.
    """

    def __init__(self):
        self._ffplay = shutil.which("ffplay")
        self._proc = None
        self._stopped = False

    @property
    def available(self):
        return bool(self._ffplay)

    def start(self):
        self._stopped = False
        if not self._ffplay:
            return self
        try:
            self._proc = subprocess.Popen(
                [
                    self._ffplay,
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "quiet",
                    "-f",
                    "wav",
                    "-i",
                    "pipe:0",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,
            )
        except Exception:
            self._proc = None
        return self

    def write(self, chunk):
        if self._stopped or self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write(chunk)
            self._proc.stdin.flush()
        except Exception:
            self._proc = None

    def finish(self):
        if self._proc is not None and self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass

    def wait(self, timeout=None):
        if self._proc is None:
            return True
        try:
            self._proc.wait(timeout=timeout)
            return True
        except Exception:
            return False

    def stop(self):
        self._stopped = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
