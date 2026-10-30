"""Geometry regression tests; no Windows audio devices required."""
import unittest
import numpy as np
from spatial import woodworth_itd


class SpatialTests(unittest.TestCase):
    def test_front_and_back_centers_have_zero_itd(self):
        np.testing.assert_allclose(woodworth_itd(np.arange(-4, 5) * np.pi), 0, atol=1e-17)

    def test_revolutions_repeat_and_reverse_direction(self):
        angles = np.linspace(-np.pi, np.pi, 1001)
        np.testing.assert_allclose(woodworth_itd(angles + 20*np.pi), woodworth_itd(angles), atol=1e-16)
        np.testing.assert_allclose(woodworth_itd(-angles), -woodworth_itd(angles), atol=1e-17)

    def test_lateral_extrema(self):
        expected = 0.0875 / 343.0 * (1 + np.pi/2)
        np.testing.assert_allclose(woodworth_itd([np.pi/2, 3*np.pi/2]), [expected, -expected])

    def test_renderers_delay_the_far_ear(self):
        # Evaluate the actual renderer delay assignments without audio imports.
        import ast
        from pathlib import Path
        for filename, names in [('make_8d.py', ('dL_tot', 'dR_tot')),
                                ('realtime_8d.py', ('dL', 'dR'))]:
            tree = ast.parse(Path(__file__).with_name(filename).read_text())
            values = dict(BASE=0.0025, sr=48000, SR=48000, np=np,
                          itd=woodworth_itd(np.array([np.pi/2, 3*np.pi/2])))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
                    values[node.targets[0].id] = eval(compile(ast.Expression(node.value), filename, 'eval'), values)
            left, right = (values[n] for n in names)
            self.assertLess(left[0], right[0])
            self.assertGreater(left[1], right[1])


if __name__ == '__main__':
    unittest.main()
