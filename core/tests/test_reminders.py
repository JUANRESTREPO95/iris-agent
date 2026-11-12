from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from iris_core import reminders
from iris_core.db import DB


def config(**overrides):
    values = {
        "channels": "telegram",
        "telegram_user": "42",
        "phone": "+57 300 111 2233",
        "reminder_channel": "auto",
        "reminder_interval": 60,
        "reminders": True,
        "timezone": "America/Bogota",
        "notas_url": "http://notas:3001",
        "notas_token": "nsk_test",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ReplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.tmp.name) / "iris.db")
        self.cfg = config()

    def tearDown(self):
        self.tmp.cleanup()

    def test_si_marca_hecho(self):
        self.db.set_pending("123", "rid", "confirm")
        with mock.patch.object(reminders.notas, "set_reminder_done", return_value=True):
            reply = reminders.handle_reply(self.cfg, self.db, "123", "sí")
        self.assertIn("hecho", reply or "")
        self.assertIsNone(self.db.get_pending("123"))

    def test_no_pide_reprogramar(self):
        self.db.set_pending("123", "rid", "confirm")
        reply = reminders.handle_reply(self.cfg, self.db, "123", "no")
        self.assertIn("reprogramo", reply or "")
        pending = self.db.get_pending("123")
        self.assertEqual(pending["state"], "reschedule")

    def test_reprogramar_encola(self):
        self.db.set_pending("123", "rid", "reschedule")
        reply = reminders.handle_reply(self.cfg, self.db, "123", "mañana a las 9")
        self.assertIn("Reprogramando", reply or "")
        row = self.db.claim_next()
        self.assertEqual(row["kind"], "reschedule")
        self.assertIn("rid", row["text"])
        self.assertIsNone(self.db.get_pending("123"))

    def test_texto_raro_sigue_el_flujo_normal(self):
        self.db.set_pending("123", "rid", "confirm")
        reply = reminders.handle_reply(self.cfg, self.db, "123", "otra cosa")
        self.assertIsNone(reply)
        self.assertIsNone(self.db.get_pending("123"))


class ChatTest(unittest.TestCase):
    def test_canal_explicito(self):
        self.assertEqual(
            reminders.target_chat(config(reminder_channel="telegram")), "tg:42"
        )
        self.assertEqual(
            reminders.target_chat(config(reminder_channel="whatsapp")),
            "573001112233@s.whatsapp.net",
        )

    def test_auto_usa_el_canal_configurado(self):
        self.assertEqual(
            reminders.target_chat(config(channels="both")), "tg:42"
        )
        self.assertEqual(
            reminders.target_chat(
                config(channels="whatsapp", telegram_user="")
            ),
            "573001112233@s.whatsapp.net",
        )

    def test_prefiere_el_chat_real_de_whatsapp(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.set_project("29566689083572@lid", "general")
            self.assertEqual(
                reminders.target_chat(config(reminder_channel="whatsapp"), db),
                "29566689083572@lid",
            )

    def test_ignora_grupos_y_usa_el_telefono(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.set_project("120363000000000000@g.us", "general")
            self.assertEqual(
                reminders.target_chat(config(reminder_channel="whatsapp"), db),
                "573001112233@s.whatsapp.net",
            )


class WatcherTest(unittest.TestCase):
    def test_avisa_una_vez(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            cfg = config()
            due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            item = {"id": "rid", "title": "Comprar", "body": "", "due_at": due}
            watcher = reminders.ReminderWatcher(cfg, db)
            with mock.patch.object(reminders.notas, "configured", return_value=True):
                with mock.patch.object(
                    reminders.notas, "list_reminders", return_value=[item]
                ):
                    with mock.patch.object(
                        reminders.bridge, "send_text", return_value=True
                    ) as send:
                        watcher._tick()
                        watcher._tick()
            self.assertEqual(send.call_count, 1)
            self.assertTrue(db.was_notified("rid", due))
            self.assertEqual(db.get_pending("tg:42")["state"], "confirm")

    def test_whatsapp_guarda_pendiente_en_el_chat_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "iris.db")
            db.set_project("29566689083572@lid", "general")
            cfg = config(reminder_channel="whatsapp", channels="whatsapp")
            due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            item = {"id": "rid", "title": "Comprar", "body": "", "due_at": due}
            watcher = reminders.ReminderWatcher(cfg, db)
            with mock.patch.object(reminders.notas, "configured", return_value=True):
                with mock.patch.object(
                    reminders.notas, "list_reminders", return_value=[item]
                ):
                    with mock.patch.object(
                        reminders.bridge, "send_text", return_value=True
                    ) as send:
                        watcher._tick()
            self.assertEqual(send.call_args.args[1], "29566689083572@lid")
            self.assertEqual(
                db.get_pending("29566689083572@lid")["state"], "confirm"
            )


if __name__ == "__main__":
    unittest.main()
