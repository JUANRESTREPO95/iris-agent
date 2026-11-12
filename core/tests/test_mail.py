from __future__ import annotations

import base64
import email
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from iris_core import mail
from iris_core.db import DB


def config(**overrides):
    values = {
        "channels": "telegram",
        "telegram_user": "42",
        "phone": "+57 300 111 2233",
        "reminder_channel": "auto",
        "timezone": "America/Bogota",
        "max_chars": 4000,
        "mail": True,
        "mail_user": "juan@gmail.com",
        "mail_password": "abcd efgh ijkl mnop",
        "mail_interval": 60,
        "mail_channel": "auto",
        "mail_folder": "INBOX",
        "mail_quiet": "",
        "mail_max_per_tick": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def message(
    subject: str = "Hola",
    sender: str = "Ana <ana@example.com>",
    body: str = "Contenido del correo",
    message_id: str = "<m1@example.com>",
    content_type: str = "text/plain",
    transfer_encoding: str = "",
) -> dict:
    headers = (
        f"From: {sender}\r\n"
        f"Subject: {subject}\r\n"
        "Date: Tue, 12 Nov 2026 10:00:00 -0500\r\n"
        f"Message-ID: {message_id}\r\n"
        "MIME-Version: 1.0\r\n"
        f"Content-Type: {content_type}; charset=utf-8\r\n"
        + (
            f"Content-Transfer-Encoding: {transfer_encoding}\r\n"
            if transfer_encoding
            else ""
        )
        + "\r\n"
    ).encode("utf-8")
    return {"headers": headers, "body": body.encode("utf-8")}


def raw_message(raw: bytes) -> dict:
    """Divide un correo completo en cabeceras y cuerpo, como hace IMAP."""
    header, _, body = raw.partition(b"\r\n\r\n")
    return {"headers": header + b"\r\n\r\n", "body": body}


class FakeIMAP:
    """IMAP mínimo en memoria para probar el watcher sin red."""

    def __init__(self, uidvalidity: str = "1"):
        self.uidvalidity = uidvalidity
        self.messages: dict[int, dict] = {}
        self.stored: list[tuple[int, str, str]] = []
        self.selected: str | None = None
        self.login_args: tuple[str, str] | None = None

    def noop(self):
        return "OK", [b""]

    def login(self, user, password):
        self.login_args = (user, password)
        return "OK", [b"logged in"]

    def status(self, folder, what):
        uidnext = max(self.messages, default=0) + 1
        raw = f'{folder} (UIDVALIDITY {self.uidvalidity} UIDNEXT {uidnext})'
        return "OK", [raw.encode()]

    def select(self, folder):
        self.selected = folder
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        command = command.lower()
        if command == "search":
            spec = str(args[-1])
            start = int(spec.split()[1].split(":")[0])
            uids = [uid for uid in sorted(self.messages) if uid >= start]
            return "OK", [b" ".join(str(uid).encode() for uid in uids)]
        if command == "fetch":
            uid = int(args[0])
            spec = str(args[1])
            item = self.messages.get(uid)
            if item is None:
                return "NO", [b""]
            data = item["headers"] if "HEADER" in spec else item["body"]
            return "OK", [(f"1 (BODY[] {{{len(data)}}}".encode(), data)]
        if command == "store":
            self.stored.append((int(args[0]), str(args[1]), str(args[2])))
            return "OK", [b""]
        return "BAD", [b""]

    def logout(self):
        return "BYE", [b""]


class MailTester(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.tmp.name) / "iris.db")
        self.cfg = config()
        self.imap = FakeIMAP()
        self.watcher = mail.MailWatcher(self.cfg, self.db)

    def tearDown(self):
        self.watcher.stop()
        self.tmp.cleanup()

    def tick(self, imap: FakeIMAP | None = None):
        client = imap or self.imap
        with mock.patch.object(self.watcher, "_connect", return_value=client):
            self.watcher._tick()

    def state(self):
        return self.db.get_mail_state("juan@gmail.com", "INBOX")


class BaselineTest(MailTester):
    def test_primera_vez_no_avisa_lo_que_ya_existia(self):
        self.imap.messages[1] = message()
        with mock.patch.object(mail.bridge, "send_text") as send:
            self.tick()
        send.assert_not_called()
        self.assertEqual(self.state()["last_uid"], 1)
        self.assertEqual(self.state()["uidvalidity"], "1")

    def test_uidvalidity_nueva_reinicia_la_base(self):
        self.imap.messages[1] = message()
        self.tick()
        self.imap.uidvalidity = "2"
        self.imap.messages[2] = message(subject="Otro", message_id="<m2@example.com>")
        with mock.patch.object(mail.bridge, "send_text") as send:
            self.tick()
        send.assert_not_called()
        self.assertEqual(self.state()["uidvalidity"], "2")
        self.assertEqual(self.state()["last_uid"], 2)


class NewMailTest(MailTester):
    def setUp(self):
        super().setUp()
        self.imap.messages[1] = message()
        self.tick()  # deja la base en UID 1

    def test_avisa_y_marca_como_leido(self):
        self.imap.messages[2] = message(
            subject="Factura de noviembre", message_id="<m2@example.com>"
        )
        with mock.patch.object(
            mail.bridge, "send_text", return_value=True
        ) as send:
            self.tick()
        send.assert_called_once()
        args = send.call_args.args
        self.assertEqual(args[1], "tg:42")
        self.assertIn("Factura de noviembre", args[2])
        self.assertIn("ana@example.com", args[2])
        self.assertIn("Contenido del correo", args[2])
        self.assertEqual(self.imap.stored, [(2, "+FLAGS", "(\\Seen)")])
        self.assertEqual(self.state()["last_uid"], 2)
        self.assertTrue(self.db.was_mail_notified("<m2@example.com>"))

    def test_si_falla_el_aviso_no_avanza_ni_marca(self):
        self.imap.messages[2] = message(message_id="<m2@example.com>")
        with mock.patch.object(mail.bridge, "send_text", return_value=False):
            self.tick()
        self.assertEqual(self.state()["last_uid"], 1)
        self.assertEqual(self.imap.stored, [])
        self.assertFalse(self.db.was_mail_notified("<m2@example.com>"))

    def test_no_avisa_dos_veces_el_mismo_message_id(self):
        self.imap.messages[2] = message(message_id="<m2@example.com>")
        with mock.patch.object(mail.bridge, "send_text", return_value=True) as send:
            self.tick()
        self.imap.messages[3] = message(
            subject="Copia", message_id="<m2@example.com>"
        )
        with mock.patch.object(mail.bridge, "send_text", return_value=True) as send2:
            self.tick()
        self.assertEqual(send.call_count, 1)
        send2.assert_not_called()
        self.assertEqual(self.state()["last_uid"], 3)
        self.assertIn((3, "+FLAGS", "(\\Seen)"), self.imap.stored)

    def test_limita_los_avisos_por_revision(self):
        self.cfg = config(mail_max_per_tick=2)
        self.watcher.cfg = self.cfg
        for uid in range(2, 8):
            self.imap.messages[uid] = message(
                subject=f"Correo {uid}", message_id=f"<m{uid}@example.com>"
            )
        with mock.patch.object(mail.bridge, "send_text", return_value=True) as send:
            self.tick()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.state()["last_uid"], 3)


class MimeTest(MailTester):
    def setUp(self):
        super().setUp()
        self.imap.messages[1] = message()
        self.tick()  # deja la base en UID 1

    def _aviso(self, uid: int, raw: bytes) -> str:
        self.imap.messages[uid] = raw_message(raw)
        with mock.patch.object(
            mail.bridge, "send_text", return_value=True
        ) as send:
            self.tick()
        send.assert_called_once()
        return send.call_args.args[2]

    def test_quoted_printable_no_se_muestra_en_crudo(self):
        raw = (
            b"From: Hostinger <team@emails.hostinger.com>\r\n"
            b"Subject: Tu carrito sigue guardado\r\n"
            b"Message-ID: <qp@example.com>\r\n"
            b"MIME-Version: 1.0\r\n"
            b'Content-Type: multipart/alternative; boundary="b"\r\n'
            b"\r\n"
            b"--b\r\n"
            b'Content-Type: text/plain; charset="UTF-8"\r\n'
            b"Content-Transfer-Encoding: quoted-printable\r\n"
            b"\r\n"
            b"Tu carrito sigue guardado=0A=0A=0AAqu=C3=AD tienes un 10% de "
            b"descuento\r\n"
            b"--b--\r\n"
        )
        text = self._aviso(2, raw)
        self.assertIn("Tu carrito sigue guardado", text)
        self.assertIn("Aquí tienes un 10% de descuento", text)
        self.assertNotIn("=0A", text)
        self.assertNotIn("quoted-printable", text.lower())
        self.assertNotIn("--b", text)

    def test_base64_se_decodifica(self):
        cuerpo = base64.b64encode(
            "Nuevo acceso en el dispositivo Linux".encode("utf-8")
        ).decode("ascii")
        raw = (
            b"From: Google <no-reply@accounts.google.com>\r\n"
            b"Subject: Alerta de seguridad\r\n"
            b"Message-ID: <b64@example.com>\r\n"
            b"MIME-Version: 1.0\r\n"
            b'Content-Type: text/plain; charset="UTF-8"\r\n'
            b"Content-Transfer-Encoding: base64\r\n"
            b"\r\n"
            + cuerpo.encode("ascii")
            + b"\r\n"
        )
        text = self._aviso(2, raw)
        self.assertIn("Nuevo acceso en el dispositivo Linux", text)
        self.assertNotIn("base64", text.lower())
        self.assertNotIn(cuerpo[:20], text)

    def test_restos_de_mime_se_limpian(self):
        raw = email.message_from_string(
            "From: ana@example.com\n"
            "Subject: Hola\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            "Content-Transfer-Encoding: quoted-printable\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            "Hola=0Amundo"
        )
        self.assertEqual(mail._summary(raw), "Hola mundo")

    def test_ignora_caracteres_invisibles_y_etiquetas_de_imagen(self):
        raw = email.message_from_bytes(
            (
                "From: ana@example.com\n"
                "Subject: Hola\n"
                "Content-Type: text/plain; charset=utf-8\n\n"
            ).encode("utf-8")
            + "\u200b".encode("utf-8") * 2
            + b"[image: Google] Nuevo acceso"
        )
        self.assertEqual(mail._summary(raw), "Nuevo acceso")


class QuietTest(MailTester):
    def test_horario_silencioso_no_conecta(self):
        self.imap.messages[1] = message()
        with mock.patch.object(mail, "_quiet", return_value=True):
            with mock.patch.object(self.watcher, "_connect") as connect:
                self.watcher._tick()
        connect.assert_not_called()

    def test_rango_nocturno_que_cruza_medianoche(self):
        now = datetime.now(ZoneInfo("America/Bogota"))
        start = (now - timedelta(minutes=1)).strftime("%H:%M")
        end = (now + timedelta(minutes=1)).strftime("%H:%M")
        cfg = config(mail_quiet=f"{start}-{end}")
        self.assertTrue(mail._quiet(cfg))

    def test_sin_horario_no_es_silencioso(self):
        self.assertFalse(mail._quiet(config()))


class ResumenTest(unittest.TestCase):
    def test_colapsa_espacios_y_recorta(self):
        raw = email.message_from_string(
            "From: ana@example.com\n"
            "Subject: Hola\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            "  Uno\n\n  dos   tres  "
        )
        self.assertEqual(mail._summary(raw), "Uno dos tres")

        raw = email.message_from_string(
            "From: ana@example.com\n"
            "Subject: Largo\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            + ("x" * 1000)
        )
        self.assertEqual(len(mail._summary(raw, limit=50)), 51)
        self.assertTrue(mail._summary(raw, limit=50).endswith("…"))

    def test_elige_texto_plano_antes_que_html(self):
        raw = email.message_from_string(
            "From: ana@example.com\n"
            "Subject: Hola\n"
            "MIME-Version: 1.0\n"
            'Content-Type: multipart/alternative; boundary="b"\n\n'
            "--b\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            "Texto plano\n"
            "--b\n"
            "Content-Type: text/html; charset=utf-8\n\n"
            "<p>HTML</p>\n"
            "--b--\n"
        )
        self.assertEqual(mail._summary(raw), "Texto plano")

    def test_limpia_html(self):
        raw = email.message_from_string(
            "From: ana@example.com\n"
            "Subject: Hola\n"
            "Content-Type: text/html; charset=utf-8\n\n"
            "<style>p{color:red}</style><p>Hola&nbsp;mundo</p><script>x()</script>"
        )
        self.assertEqual(mail._summary(raw), "Hola mundo")

    def test_formato_con_asunto_y_remitente(self):
        text = mail._format("Asunto", "Ana <ana@example.com>", "Resumen")
        self.assertIn("Asunto", text)
        self.assertIn("Ana <ana@example.com>", text)
        self.assertIn("Resumen", text)


if __name__ == "__main__":
    unittest.main()
