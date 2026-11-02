"""Exercise the offline CLI with real temporary PCM WAV files."""
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
import numpy as np


class OfflineTests(unittest.TestCase):
    def render(self, data, rate=48000):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/'in.wav', Path(tmp)/'out.wav'
            with wave.open(str(src), 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(np.asarray(data, dtype='<i2').tobytes())
            proc = subprocess.run([sys.executable, '-W', 'error',
                str(Path(__file__).with_name('make_8d.py')), '--in', str(src), '--out', str(dst)],
                capture_output=True, text=True)
            result = None
            if dst.exists():
                with wave.open(str(dst), 'rb') as w:
                    result = (w.getnchannels(), w.getframerate(), w.getnframes(),
                              np.frombuffer(w.readframes(w.getnframes()), dtype='<i2'))
            return proc, result

    def test_silence_is_valid_stereo_silence(self):
        proc, result = self.render(np.zeros(4800))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result[:3], (2, 48000, 4800))
        self.assertTrue(np.all(result[3] == 0))

    def test_short_signal_delayed_beyond_file_stays_finite(self):
        proc, result = self.render([1000]*10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result[2], 10)

    def test_empty_input_has_clear_error(self):
        proc, result = self.render([])
        self.assertEqual(proc.returncode, 2)
        self.assertIn('no audio frames', proc.stderr)
        self.assertIsNone(result)

    def test_low_sample_rate_has_clear_error(self):
        proc, result = self.render([0]*80, rate=8000)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('sample rate must exceed', proc.stderr)
        self.assertIsNone(result)

    def test_normal_tone_preserves_duration_and_peak_limit(self):
        signal = 12000*np.sin(2*np.pi*440*np.arange(4800)/48000)
        proc, result = self.render(signal)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result[:3], (2, 48000, 4800))
        self.assertGreater(np.abs(result[3]).max(), 0)
        self.assertLessEqual(np.abs(result[3]).max(), int(0.97*32767)+1)


if __name__ == '__main__':
    unittest.main()
