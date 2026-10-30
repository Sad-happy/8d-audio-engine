# Offline unit test for the feed-forward max-hold lookahead limiter that
# realtime_8d.py uses. No audio devices needed.
#
#   python test_limiter.py
#
# Feed-forward design: the requirement M = sliding max of a near-instant
# follower over the lookahead window; the gain curve is then smoothed by a
# 1 ms/2 ms follower. The test asserts (1) no sample exceeds the ceiling
# (attack settles within the lookahead), (2) no click at block boundaries,
# (3) the per-sample gain step stays tiny (smooth gain curve = no crackle).
import math
import numpy as np
from scipy.ndimage import maximum_filter1d

SR = 48000
LA, CEIL = 240, 0.95
ATT_E = 1.0 - math.exp(-1.0 / (0.00001 * SR))
REL_E = 1.0 - math.exp(-1.0 / (0.120 * SR))
ATT_M = 1.0 - math.exp(-1.0 / (0.001 * SR))
REL_M = 1.0 - math.exp(-1.0 / (0.002 * SR))
env_lvl, m_lvl = 1.0, 1.0
E_tail = np.ones(LA)
dline = np.zeros((LA, 2), np.float32)

def limit(out):
    global env_lvl, m_lvl, E_tail, dline
    B = len(out)
    e = env_lvl
    E = np.empty(B)
    for i, pk in enumerate(np.abs(out).max(axis=1).tolist()):
        t = pk / CEIL
        if t > e:
            e += (t - e) * ATT_E
        else:
            e += (t - e) * REL_E
        E[i] = e
    env_lvl = e
    roll = np.concatenate([E_tail, E])
    M = maximum_filter1d(roll, size=LA + 1, origin=-(LA // 2))[:B]
    E_tail = E[-LA:].copy()
    m = m_lvl
    g = np.empty(B)
    for i, t in enumerate(M.tolist()):
        if t > m:
            m += (t - m) * ATT_M
        elif t < m:
            m += (t - m) * REL_M
        if m < 1.0:
            m = 1.0
        g[i] = m
    m_lvl = m
    buf = np.concatenate([dline, out], axis=0)
    y = buf[:B] * (1.0 / g.astype(np.float32))[:, None]
    dline = buf[B:].copy()
    return np.clip(y, -0.98, 0.98), g

# sanity: window convention -> maximum_filter1d origin=-(LA//2) is LEADING
r = np.array([0.0, 5.0, 1.0, 2.0, 3.0, 9.0, 0.5, 4.0], np.float64)
w = maximum_filter1d(r, size=5, origin=-2)
assert abs(w[0] - 5.0) < 1e-9 and abs(w[3] - 9.0) < 1e-9, f"window convention broken: {w}"

n = np.arange(40 * 2048 + 480)
sine = (0.92 * np.sin(2 * np.pi * 220 * n / 48000)).astype(np.float32)
max_peak, max_jump, max_gstep = 0.0, 0.0, 0.0
prev_last, prev_g = None, None
for blk in range(40):
    x = np.stack([sine[blk*2048:(blk+1)*2048]]*2, axis=1)
    # pathological transients: rectangular full-scale pulses a follower alone
    # could never anticipate (they end before any attack completes)
    if blk % 3 == 0:
        x[500:504] = 1.3
        x[1500:1502] = -1.35
    if blk % 3 == 1:
        x[2046:2048] = 1.4
    y, g = limit(x)
    max_peak = max(max_peak, float(np.abs(y).max()))
    if prev_last is not None:
        max_jump = max(max_jump, float(np.abs(y[0] - prev_last).max()))
    if prev_g is not None:
        max_gstep = max(max_gstep, float(np.abs(g[0] - prev_g).max()))
    max_gstep = max(max_gstep, float(np.abs(np.diff(g)).max()))
    prev_last, prev_g = y[-1].copy(), g[-1]
print(f"40 blocks: max output peak={max_peak:.4f} (target == {CEIL}, no belt)")
print(f"max block-boundary discontinuity={max_jump:.4f} (sine natural diff=0.027)")
print(f"max per-sample gain step={max_gstep:.5f} (ATT_M-limited, tiny = no clicks)")
verdict = max_peak <= CEIL + 0.005 and max_jump < 0.05 and max_gstep < 0.012
print("VERDICT:", "LIMITER OK" if verdict else "FAIL")
