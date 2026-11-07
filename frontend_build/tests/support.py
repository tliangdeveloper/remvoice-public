# -*- coding: utf-8 -*-
"""Shared test helpers (not collected as tests)."""
import contextlib
import os
import shutil
import tempfile
import uuid


@contextlib.contextmanager
def scratch():
    """A throw-away directory that also works inside a restricted sandbox.

    ``tempfile.mkdtemp`` creates the directory with mode 0700, which some
    Windows sandboxes deny to the running token: every write inside then fails
    with ``PermissionError``.  A plain ``makedirs`` directory inherits the
    normal ACL and is writable, and ``rmtree(ignore_errors=True)`` still
    cleans up on exit.
    """
    path = os.path.join(
        tempfile.gettempdir(), "remvoice_test_%s" % uuid.uuid4().hex[:10]
    )
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
