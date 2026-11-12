from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from iris_core import sync as sync_mod


class ProjectSyncStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.workspace = root / "ws"
        self.workspace.mkdir()
        (self.workspace / "proyecto").mkdir()
        self.data = root / "data"
        self.cfg = SimpleNamespace(
            workspace=self.workspace,
            data_dir=self.data,
            channels="telegram",
            telegram_user="123",
            phone="",
        )
        self.sent: list[str] = []
        self.remote: list[str] = []
        self.send_ok = True
        for patcher in (
            mock.patch.object(sync_mod.notas, "configured", new=lambda cfg: True),
            mock.patch.object(
                sync_mod.notas,
                "list_projects",
                new=lambda cfg: list(self.remote),
            ),
            mock.patch.object(
                sync_mod.bridge, "send_text", side_effect=self._send
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _send(self, cfg, chat_id, text):
        self.sent.append(text)
        return self.send_ok

    def test_avisa_una_sola_vez_y_lo_recuerda_tras_reinicio(self):
        sync_mod.ProjectSync(self.cfg)._tick()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("proyecto", self.sent[0])
        state = json.loads((self.data / "sync-notified.json").read_text("utf-8"))
        self.assertEqual(state, ["proyecto"])

        # Nueva instancia = reinicio del núcleo: no debe repetir el aviso.
        sync_mod.ProjectSync(self.cfg)._tick()
        self.assertEqual(len(self.sent), 1)

    def test_vuelve_a_avisar_si_el_proyecto_reaparece_en_notas_y_luego_falta(self):
        sync_mod.ProjectSync(self.cfg)._tick()
        self.remote = ["proyecto"]
        sync_mod.ProjectSync(self.cfg)._tick()
        self.remote = []
        sync_mod.ProjectSync(self.cfg)._tick()
        self.assertEqual(len(self.sent), 2)

    def test_no_marca_cuando_el_envio_falla(self):
        self.send_ok = False
        sync_mod.ProjectSync(self.cfg)._tick()
        self.assertEqual(len(self.sent), 1)
        self.assertFalse((self.data / "sync-notified.json").exists())

        self.send_ok = True
        sync_mod.ProjectSync(self.cfg)._tick()
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
