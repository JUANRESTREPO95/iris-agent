from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from iris_core import bridge


def config(root: Path, **overrides) -> SimpleNamespace:
    workspace = root / "workspace"
    data_dir = root / "data"
    workspace.mkdir(parents=True, exist_ok=True)
    values = {
        "workspace": workspace,
        "data_dir": data_dir,
        "outbox": data_dir / "outbox",
        "max_file_mb": 1,
        "bridge_url": "http://wa:3003",
        "telegram_bridge_url": "http://tg:3005",
        "token": "tok",
        "max_chars": 4000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ExtractAttachmentsTest(unittest.TestCase):
    def test_quita_varios_marcadores(self):
        clean, paths = bridge.extract_attachments(
            "Listo.\n[[ADJUNTO:/tmp/x.pdf]]\n\n[[ADJUNTO: /tmp/y.zip ]]"
        )
        self.assertEqual(paths, ["/tmp/x.pdf", "/tmp/y.zip"])
        self.assertNotIn("ADJUNTO", clean)
        self.assertEqual(clean, "Listo.")

    def test_texto_sin_marcador_no_cambia(self):
        clean, paths = bridge.extract_attachments("Hola\n\nmundo")
        self.assertEqual(clean, "Hola\n\nmundo")
        self.assertEqual(paths, [])


class PrepareAttachmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = config(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_copia_al_outbox_y_sanea_el_nombre(self):
        source = self.cfg.workspace / "mi informe (1).pdf"
        source.write_bytes(b"%PDF-1.4")
        destination, copied = bridge.prepare_attachment(self.cfg, str(source))

        self.assertTrue(copied)
        self.assertEqual(destination.parent, self.cfg.outbox)
        self.assertTrue(destination.name.endswith("-mi informe _1_.pdf"))
        self.assertEqual(destination.read_bytes(), b"%PDF-1.4")

    def test_ruta_fuera_del_workspace(self):
        outside = self.cfg.data_dir / "secreto.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "fuera del workspace"):
            bridge.prepare_attachment(self.cfg, str(outside))
        with self.assertRaisesRegex(ValueError, "fuera del workspace"):
            bridge.prepare_attachment(self.cfg, "/etc/passwd")

    def test_rechaza_archivos_sensibles(self):
        sensitive = self.cfg.workspace / ".env"
        sensitive.write_text("TOKEN=x")
        with self.assertRaisesRegex(ValueError, "sensible"):
            bridge.prepare_attachment(self.cfg, str(sensitive))

    def test_rechaza_symlinks(self):
        real = self.cfg.workspace / "real.pdf"
        real.write_bytes(b"%PDF")
        link = self.cfg.workspace / "link.pdf"
        link.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            bridge.prepare_attachment(self.cfg, str(link))

    def test_rechaza_extensiones_no_permitidas(self):
        executable = self.cfg.workspace / "malware.exe"
        executable.write_bytes(b"MZ")
        with self.assertRaisesRegex(ValueError, "extensión"):
            bridge.prepare_attachment(self.cfg, str(executable))

    def test_rechaza_archivos_grandes(self):
        big = self.cfg.workspace / "grande.pdf"
        big.write_bytes(b"x" * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "demasiado grande"):
            bridge.prepare_attachment(self.cfg, str(big))

    def test_archivo_inexistente(self):
        with self.assertRaisesRegex(ValueError, "no existe"):
            bridge.prepare_attachment(
                self.cfg, str(self.cfg.workspace / "no-existe.pdf")
            )

    def test_archivo_ya_en_outbox_no_se_copia(self):
        self.cfg.outbox.mkdir(parents=True)
        existing = self.cfg.outbox / "listo.pdf"
        existing.write_bytes(b"%PDF")
        destination, copied = bridge.prepare_attachment(self.cfg, str(existing))
        self.assertFalse(copied)
        self.assertEqual(destination, existing.resolve())


class SendDocumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = config(self.root)
        self.source = self.cfg.workspace / "informe.pdf"
        self.source.write_bytes(b"%PDF-1.4")

    def tearDown(self):
        self.tmp.cleanup()

    def _patch(self, result=None):
        calls = []

        def fake_post(cfg, base, path, payload, timeout=15.0):
            calls.append(
                {"base": base, "path": path, "payload": payload, "timeout": timeout}
            )
            return {"ok": True} if result is None else result

        patcher = mock.patch.object(bridge, "_post_url", side_effect=fake_post)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_whatsapp_copia_envia_y_limpia(self):
        calls = self._patch()
        self.assertTrue(bridge.send_document(self.cfg, "573001112233", str(self.source)))
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["base"], self.cfg.bridge_url)
        self.assertEqual(call["path"], "/send_document")
        self.assertEqual(call["timeout"], 120.0)
        self.assertEqual(call["payload"]["chatId"], "573001112233")
        sent_path = Path(call["payload"]["path"])
        self.assertEqual(sent_path.parent, self.cfg.outbox)
        self.assertFalse(sent_path.exists())

    def test_telegram_usa_su_puente_y_quita_prefijo(self):
        calls = self._patch()
        self.assertTrue(bridge.send_document(self.cfg, "tg:99", str(self.source)))
        self.assertEqual(calls[0]["base"], self.cfg.telegram_bridge_url)
        self.assertEqual(calls[0]["payload"]["chatId"], "99")

    def test_si_el_puente_falla_devuelve_false_y_limpia(self):
        with mock.patch.object(bridge, "_post_url", return_value=None):
            self.assertFalse(
                bridge.send_document(self.cfg, "123", str(self.source))
            )
        # No se pudo enviar: no debe quedar el temporal en el outbox.
        leftovers = list(self.cfg.outbox.glob("*-informe.pdf"))
        self.assertEqual(leftovers, [])

    def test_ruta_invalida_no_hace_http(self):
        calls = self._patch()
        with self.assertRaises(ValueError):
            bridge.send_document(self.cfg, "123", "/etc/passwd")
        self.assertEqual(calls, [])

    def test_archivo_en_outbox_no_se_borra(self):
        calls = self._patch()
        self.cfg.outbox.mkdir(parents=True)
        existing = self.cfg.outbox / "directo.pdf"
        existing.write_bytes(b"%PDF")
        self.assertTrue(bridge.send_document(self.cfg, "123", str(existing)))
        self.assertTrue(existing.exists())
        self.assertEqual(Path(calls[0]["payload"]["path"]), existing.resolve())


if __name__ == "__main__":
    unittest.main()
