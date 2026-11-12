from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from iris_core.db import DB


class DBTest(unittest.TestCase):
    def test_enqueue_guarda_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.enqueue(
                {
                    "chatId": "123",
                    "senderId": "123",
                    "text": "prompt",
                    "kind": "guardar",
                }
            )
            row = db.claim_next()
            self.assertIsNotNone(row)
            self.assertEqual(row["kind"], "guardar")

    def test_reset_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.set_project("123", "demo")
            db.set_session("123", "demo", "sid")
            db.reset_project("demo")
            self.assertEqual(db.get_chat("123")["project"], "general")
            self.assertIsNone(db.get_session("123", "demo"))

    def test_sesion_conserva_su_created_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.set_session("123", "demo", "s1")
            created = db.session_created_at("123", "demo")
            self.assertIsNotNone(created)
            time.sleep(0.02)
            db.set_session("123", "demo", "s2")
            self.assertEqual(db.get_session("123", "demo"), "s2")
            self.assertAlmostEqual(
                db.session_created_at("123", "demo") or 0, created or 0, places=3
            )

    def test_sesion_nueva_reinicia_created_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.set_session("123", "demo", "s1")
            first = db.session_created_at("123", "demo") or 0
            db.clear_session("123", "demo")
            time.sleep(0.02)
            db.set_session("123", "demo", "s2")
            second = db.session_created_at("123", "demo") or 0
            self.assertGreater(second, first)


if __name__ == "__main__":
    unittest.main()
