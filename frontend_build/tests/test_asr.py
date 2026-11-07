# -*- coding: utf-8 -*-
"""AsrService protocol tests use a tiny fake worker (no mic, no torch)."""
import os
import sys
import threading
import unittest

from remvoice.asr import AsrService
from support import scratch


FAKE_WORKER = r'''
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        command = json.loads(line).get("cmd")
    except Exception:
        continue
    if command == "start":
        print(json.dumps({"event": "recording"}), flush=True)
    elif command == "stop":
        print(json.dumps({"event": "result", "text": "你好", "language": "zh"}), flush=True)
    elif command == "quit":
        break
'''


class AsrServiceTests(unittest.TestCase):
    def test_json_line_protocol_and_close(self):
        with scratch() as tmp:
            script = os.path.join(tmp, "fake_worker.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write(FAKE_WORKER)
            events = []
            seen = threading.Event()

            def on_event(event):
                events.append(event)
                if event.get("event") == "result":
                    seen.set()

            service = AsrService(
                sys.executable,
                script,
                "fake",
                stderr_path=os.path.join(tmp, "worker.err"),
                on_event=on_event,
            )
            try:
                self.assertTrue(service.start())
                self.assertTrue(service.begin())
                self.assertTrue(service.end())
                self.assertTrue(seen.wait(5))
            finally:
                service.close()
            self.assertEqual(events[0]["event"], "recording")
            self.assertTrue(
                any(
                    item.get("event") == "result" and item.get("text") == "你好"
                    for item in events
                )
            )
            self.assertFalse(service.alive)


if __name__ == "__main__":
    unittest.main()
