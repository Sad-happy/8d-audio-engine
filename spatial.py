"""Device-independent binaural geometry shared by both renderers."""
import numpy as np


def woodworth_itd(theta, radius=0.0875, speed=343.0):
    """Return periodic ITD in seconds; positive means delay the right ear.

    Fold the rear hemisphere onto [-pi/2, pi/2] before applying the
    spherical-head model. Front and back center both have zero ITD.
    """
    lateral = np.arcsin(np.clip(np.sin(theta), -1.0, 1.0))
    return (radius / speed) * (np.sin(lateral) + lateral)
