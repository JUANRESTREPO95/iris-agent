from __future__ import annotations

import tempfile
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


if __name__ == "__main__":
    unittest.main()
