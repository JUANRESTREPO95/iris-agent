from __future__ import annotations

import unittest

from iris_core import worker


class SessionExpiredTest(unittest.TestCase):
    def test_sin_fecha_no_expira(self):
        self.assertFalse(worker.session_expired(None, 12))
        self.assertFalse(worker.session_expired(0, 12))

    def test_cero_desactiva_la_rotacion(self):
        self.assertFalse(worker.session_expired(1000, 0, now=10**9))

    def test_cumple_las_horas_configuradas(self):
        self.assertTrue(worker.session_expired(1000, 12, now=1000 + 12 * 3600))
        self.assertFalse(worker.session_expired(1000, 12, now=1000 + 12 * 3600 - 1))

    def test_acepta_horas_fraccionarias(self):
        self.assertTrue(worker.session_expired(1000, 0.5, now=1000 + 1800))


if __name__ == "__main__":
    unittest.main()
