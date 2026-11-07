# -*- coding: utf-8 -*-
"""Playback queue and ffplay streaming behavior (no real sound)."""
import threading
import unittest
from unittest import mock

from remvoice.player import AudioPlayer, StreamingPlayer


class PlayerTests(unittest.TestCase):
    def test_plays_in_order_then_finishes(self):
        played = []
        player = AudioPlayer(play=played.append, stop=lambda: None).start()
        player.put("a.wav")
        player.put("b.wav")
        player.finish()
        self.assertTrue(player.wait(2))
        self.assertEqual(played, ["a.wav", "b.wav"])

    def test_stop_drops_queued_chunks(self):
        played = []
        started = threading.Event()
        release = threading.Event()

        def play(path):
            played.append(path)
            started.set()
            release.wait(1)

        player = AudioPlayer(play=play, stop=release.set).start()
        player.put("a.wav")
        self.assertTrue(started.wait(1))
        player.put("b.wav")
        player.stop()
        self.assertTrue(player.wait(2))
        self.assertEqual(played, ["a.wav"])


class FakeStdin:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self):
        self.stdin = FakeStdin()
        self.terminated = False

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.terminated = True


class StreamingPlayerTests(unittest.TestCase):
    def test_writes_chunks_to_ffplay(self):
        proc = FakeProc()
        with mock.patch(
            "remvoice.player.shutil.which", return_value="ffplay.exe"
        ), mock.patch("remvoice.player.subprocess.Popen", return_value=proc):
            player = StreamingPlayer()
            self.assertTrue(player.available)
            player.start()
            player.write(b"chunk-1")
            player.write(b"chunk-2")
            player.finish()
            self.assertTrue(player.wait(1))
        self.assertEqual(proc.stdin.written, [b"chunk-1", b"chunk-2"])
        self.assertTrue(proc.stdin.closed)

    def test_unavailable_is_safe(self):
        with mock.patch("remvoice.player.shutil.which", return_value=None):
            player = StreamingPlayer()
            self.assertFalse(player.available)
            player.start()
            player.write(b"x")
            player.finish()
            self.assertTrue(player.wait(1))


if __name__ == "__main__":
    unittest.main()
