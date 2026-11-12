from __future__ import annotations

import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from iris_core import notas


class MemoryContextTest(unittest.TestCase):
    def setUp(self):
        notas._CACHE.update({"at": 0.0, "url": "", "content": ""})
        notas._PROJECT_CACHE.clear()
        notas._ACTIVE.update({"url": "", "at": 0.0})

    def cfg(self, url="http://notas:3001", token="nsk_test", urls=None):
        return SimpleNamespace(
            notas_url=url,
            notas_urls=tuple(urls) if urls is not None else (
                (url,) if url else ()
            ),
            notas_token=token,
        )

    @staticmethod
    def _response(payload: bytes):
        response = mock.MagicMock()
        response.read.return_value = payload
        response.__enter__.return_value = response
        return response

    def test_sin_configuracion_devuelve_vacio(self):
        self.assertEqual(notas.memory_context(self.cfg(url="", token="")), "")

    def test_lee_configuracion(self):
        tree = {"notes": [{"id": "abc", "path": "configuracion.md"}]}
        note = {"id": "abc", "content": "# Configuración\n\nNombre: Juan"}
        with mock.patch.object(notas, "_get", side_effect=[tree, note]) as get:
            self.assertIn("Nombre: Juan", notas.memory_context(self.cfg()))
            self.assertEqual(get.call_count, 2)

    def test_cachea_por_ttl(self):
        tree = {"notes": [{"id": "abc", "path": "configuracion.md"}]}
        note = {"id": "abc", "content": "memoria"}
        with mock.patch.object(notas, "_get", side_effect=[tree, note]) as get:
            notas.memory_context(self.cfg())
            notas.memory_context(self.cfg())
            self.assertEqual(get.call_count, 2)

    def test_si_falla_usa_la_cache(self):
        notas._CACHE.update({"at": 1.0, "url": "http://notas:3001", "content": "vieja"})
        with mock.patch.object(notas, "_get", side_effect=RuntimeError("caído")):
            with mock.patch.object(notas, "_ttl", return_value=0):
                self.assertEqual(notas.memory_context(self.cfg()), "vieja")

    def test_lista_proyectos(self):
        tree = {"folders": ["proyectos/uno", "proyectos/dos", "proyectos/uno/sub"]}
        with mock.patch.object(notas, "_get", return_value=tree):
            self.assertEqual(notas.list_projects(self.cfg()), ["dos", "uno"])

    def test_ensure_project_acepta_409(self):
        error = urllib.error.HTTPError("url", 409, "conflict", {}, None)
        with mock.patch.object(notas, "_request", side_effect=error):
            self.assertTrue(notas.ensure_project(self.cfg(), "demo"))

    def test_guarda_conversacion(self):
        calls = []

        def fake_request(cfg, method, path, payload=None):
            calls.append((method, path, payload))
            return {}

        with mock.patch.object(notas, "_get", return_value={"notes": []}):
            with mock.patch.object(notas, "_request", side_effect=fake_request):
                self.assertTrue(
                    notas.save_conversation(self.cfg(), "demo", "hola", "respuesta")
                )
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "/api/notes")
        self.assertIn("hola", calls[0][2]["content"])

    def test_failover_usa_la_siguiente_url(self):
        cfg = self.cfg(url="http://uno", urls=["http://uno", "http://dos"])
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            if request.full_url.startswith("http://uno"):
                raise urllib.error.URLError("caído")
            return self._response(b'{"ok": true}')

        with mock.patch.object(
            notas.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            self.assertEqual(notas._request(cfg, "GET", "/api/tree"), {"ok": True})
        self.assertEqual(calls, ["http://uno/api/tree", "http://dos/api/tree"])
        self.assertEqual(notas._ACTIVE["url"], "http://dos")

    def test_conversation_tail_recorta_y_empieza_en_bloque(self):
        tree = {"notes": [{"id": "c", "path": "proyectos/demo/conversacion.md"}]}
        content = (
            "### viejo\n\n"
            + ("x" * 500)
            + "\n### nuevo\n\n**Usuario:** hola\n**Iris:** qué tal\n"
        )
        with mock.patch.object(
            notas, "_get", side_effect=[tree, {"id": "c", "content": content}]
        ):
            tail = notas.conversation_tail(self.cfg(), "demo", 120)
        self.assertIn("### nuevo", tail)
        self.assertNotIn("### viejo", tail)
        self.assertLessEqual(len(tail), 120)

    def test_conversation_tail_desactivado_no_lee(self):
        with mock.patch.object(notas, "_get") as get:
            self.assertEqual(notas.conversation_tail(self.cfg(), "demo", 0), "")
            get.assert_not_called()

    def test_guardar_prompt_nombra_contexto_y_conversacion(self):
        prompt = notas.guardar_prompt("demo")
        self.assertIn("proyectos/demo/contexto.md", prompt)
        self.assertIn("proyectos/demo/conversacion.md", prompt)

    def test_4xx_no_hace_failover(self):
        cfg = self.cfg(url="http://uno", urls=["http://uno", "http://dos"])
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 404, "no", {}, None)

        with mock.patch.object(
            notas.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            with self.assertRaises(urllib.error.HTTPError):
                notas._request(cfg, "GET", "/api/x")
        self.assertEqual(calls, ["http://uno/api/x"])


if __name__ == "__main__":
    unittest.main()
