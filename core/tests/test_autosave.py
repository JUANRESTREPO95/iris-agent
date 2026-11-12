from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from iris_core import notas
from iris_core.autosave import AutoSaveWatcher
from iris_core.db import DB


class AutoSaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DB(Path(self.tmp.name) / "iris.db")
        self.cfg = SimpleNamespace(autosave_hours=6.0)
        self.watcher = AutoSaveWatcher(
            self.cfg, self.db, interval=30, idle_seconds=0
        )

    def _chat_con_sesion(self):
        self.db.set_project("123", "general")
        self.db.set_session("123", "general", "sid")
        self.db.enqueue({"chatId": "123", "senderId": "123", "text": "hola"})

    def test_encola_una_vez_y_no_duplica(self):
        self._chat_con_sesion()
        self.db.set_autosave("123", "general", time.time() - 7 * 3600)
        with mock.patch.object(notas, "guardar_prompt", return_value="prompt"):
            self.watcher._tick()
            self.assertEqual(self.db.queue_size(), 2)
            self.watcher._tick()
            self.assertEqual(self.db.queue_size(), 2)

        user = self.db.claim_next()
        self.assertEqual(user["text"], "hola")
        self.db.mark(user["id"], "respondido")
        auto = self.db.claim_next()
        self.assertEqual(auto["kind"], "guardar_auto")
        self.assertEqual(auto["text"], "prompt")

    def test_primera_vez_solo_toma_linea_base(self):
        self._chat_con_sesion()
        with mock.patch.object(notas, "guardar_prompt", return_value="prompt"):
            self.watcher._tick()
        self.assertEqual(self.db.queue_size(), 1)
        self.assertIsNotNone(self.db.get_autosave("123", "general"))

    def test_sin_sesion_no_encola(self):
        self.db.set_project("123", "general")
        with mock.patch.object(notas, "guardar_prompt", return_value="prompt"):
            self.watcher._tick()
        self.assertEqual(self.db.queue_size(), 0)

    def test_horas_cero_no_encola(self):
        self._chat_con_sesion()
        cfg = SimpleNamespace(autosave_hours=0.0)
        watcher = AutoSaveWatcher(cfg, self.db, interval=30)
        with mock.patch.object(notas, "guardar_prompt", return_value="prompt"):
            watcher._tick()
        self.assertEqual(self.db.queue_size(), 1)

    def test_no_encola_si_no_hay_mensajes_nuevos(self):
        self._chat_con_sesion()
        old_user = time.time() - 8 * 3600
        with self.db._conn() as conn:
            conn.execute("UPDATE messages SET created_at = ?", (old_user,))
        self.db.set_autosave("123", "general", time.time() - 7 * 3600)
        with mock.patch.object(notas, "guardar_prompt", return_value="prompt"):
            self.watcher._tick()
        self.assertEqual(self.db.queue_size(), 1)


if __name__ == "__main__":
    unittest.main()
