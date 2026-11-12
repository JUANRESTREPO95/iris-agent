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
                    "project": "demo",
                }
            )
            row = db.claim_next()
            self.assertIsNotNone(row)
            self.assertEqual(row["kind"], "guardar")
            self.assertEqual(row["project"], "demo")

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

    def test_autosave_guarda_y_lee_la_fecha(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            self.assertIsNone(db.get_autosave("123", "demo"))
            db.set_autosave("123", "demo", 1000.0)
            self.assertEqual(db.get_autosave("123", "demo"), 1000.0)
            db.set_autosave("123", "demo", 2000.0)
            self.assertEqual(db.get_autosave("123", "demo"), 2000.0)

    def test_claim_next_deja_el_autoguardado_para_el_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.enqueue({"chatId": "1", "senderId": "1", "text": "auto",
                        "kind": "guardar_auto"})
            db.enqueue({"chatId": "1", "senderId": "1", "text": "hola"})
            self.assertEqual(db.claim_next()["text"], "hola")
            self.assertEqual(db.claim_next()["text"], "auto")

    def test_autosave_targets_ignora_guardados_y_proyectos_viejos(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.set_project("123", "demo")
            db.set_session("123", "demo", "s1")
            db.enqueue({"chatId": "123", "senderId": "123",
                        "text": "hola", "kind": None})
            self.assertEqual(
                db.autosave_targets(time.time() - 86400),
                [{"chat_id": "123", "project": "demo"}],
            )
            # Un guardado no cuenta como actividad.
            db2 = DB(Path(tmp) / "otra.db")
            db2.set_project("123", "demo")
            db2.set_session("123", "demo", "s1")
            db2.enqueue({"chatId": "123", "senderId": "123",
                         "text": "guardar", "kind": "guardar"})
            self.assertEqual(db2.autosave_targets(time.time() - 86400), [])

    def test_last_user_message_ignora_guardados(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.enqueue({"chatId": "1", "senderId": "1", "text": "hola"})
            first = db.last_user_message_at("1")
            self.assertIsNotNone(first)
            time.sleep(0.02)
            db.enqueue({"chatId": "1", "senderId": "1", "text": "auto",
                        "kind": "guardar_auto"})
            self.assertEqual(db.last_user_message_at("1"), first)


if __name__ == "__main__":
    unittest.main()
