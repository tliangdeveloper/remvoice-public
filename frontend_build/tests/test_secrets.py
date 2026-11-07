# -*- coding: utf-8 -*-
"""Windows DPAPI wrapper tests (skipped elsewhere)."""
import unittest

from remvoice import secrets


class DpapiTests(unittest.TestCase):
    @unittest.skipUnless(secrets.available(), "DPAPI is Windows-only")
    def test_roundtrip(self):
        token = secrets.protect("sk-test-123")
        self.assertTrue(token)
        self.assertEqual(secrets.unprotect(token), "sk-test-123")

    def test_invalid_token_returns_none(self):
        self.assertIsNone(secrets.unprotect("not-a-token"))
        self.assertIsNone(secrets.protect(""))
