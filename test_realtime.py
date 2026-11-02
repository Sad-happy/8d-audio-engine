"""Regression tests for production DSP; no Windows audio hardware needed."""
import argparse
import sys
import types
import unittest
from unittest.mock import patch
import numpy as np

with patch.dict(sys.modules, {"pyaudiowpatch": types.ModuleType("pyaudiowpatch")}):
    import realtime_8d as engine


class RealtimeTests(unittest.TestCase):
    def setUp(self):
        engine.configure(argparse.Namespace(
            rate=48000, block=2048, frot=0.14, elev_deg=25,
            bass_dip_deg=12, presence=0.45, target_rms=0.11))
        engine.zi_pre = engine.zi_lo = engine.zi_hi = None
        engine.hist_L = np.zeros(engine.MAXD + 4, np.float32)
        engine.hist_R = engine.hist_L.copy()
        engine.gain_agc = engine.env_lvl = engine.m_lvl = 1.0
        engine.n_global = 0
        engine.E_tail = np.ones(engine.LA)
        engine.dline = np.zeros((engine.LA, 2), np.float32)
        engine.BYPASS[0] = False
        engine.BS = None

    def test_silent_start_stays_silent(self):
        for _ in range(4):
            np.testing.assert_array_equal(engine.process(np.zeros((2048, 2), np.float32)), 0)

    def test_actual_frame_count(self):
        total = 0
        for size in (1024, 128, 2048, 64):
            t = np.arange(total, total + size)
            x = np.repeat((0.1 * np.sin(2*np.pi*220*t/48000))[:, None], 2, axis=1).astype(np.float32)
            y = engine.process(x)
            self.assertEqual(y.shape, x.shape)
            self.assertTrue(np.isfinite(y).all())
            total += size
            self.assertEqual(engine.n_global, total)

    def test_limiter_chunk_invariance(self):
        x = np.random.default_rng(42).normal(0, 0.1, (4096, 2)).astype(np.float32)
        x[239:243] = 1.4
        x[2047:2051] = -1.35
        reference = engine.limit(x)
        self.setUp()
        parts, offset = [], 0
        for size in (1, 31, 64, 128, 16, 1000, 2048, 808):
            parts.append(engine.limit(x[offset:offset+size]))
            offset += size
        self.assertEqual(offset, len(x))
        np.testing.assert_allclose(np.concatenate(parts), reference, atol=1e-7)

    def test_bypass_clock(self):
        engine.BYPASS[0] = True
        x = np.zeros((128, 2), np.float32)
        np.testing.assert_array_equal(engine.process(x), x)
        self.assertEqual(engine.n_global, 128)

    def test_empty_callback_preserves_state(self):
        y = engine.process(np.empty((0, 2), np.float32))
        self.assertEqual(y.shape, (0, 2))
        self.assertEqual(engine.n_global, 0)
        self.assertIsNone(engine.zi_pre)

    def test_beat_tracker_advances_by_actual_duration(self):
        from beat_sync import BeatSync
        for bypass in (False, True):
            with self.subTest(bypass=bypass):
                self.setUp()
                engine.BS = BeatSync(dt=2048 / 48000)
                engine.BYPASS[0] = bypass
                engine.process(np.zeros((128, 2), np.float32))
                self.assertAlmostEqual(engine.BS.theta, 2*np.pi*0.14*128/48000)


if __name__ == '__main__':
    unittest.main()
