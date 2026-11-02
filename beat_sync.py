# -*- coding: utf-8 -*-
"""Beat-synced rotation for realtime_8d.py.

Feeds on the bass-band RMS of each block and provides:

* **BPM estimation** — autocorrelation of the bass-onset envelope
  (positive flux over a ~120 ms EMA), refreshed every few seconds.
* **Phase-locked rotation** — the azimuth advances at the locked rate
  (one revolution per N beats, auto-picked so a revolution lasts
  `rev_sec`), with a small PLL correction that slews the phase toward
  the kick grid over a few seconds. No phase jumps, ever: the theta
  trajectory stays continuous, so the sound never snaps to a new
  position.
* **Bass dip** — a 0..1 envelope that rises on kick prominence; the
  engine presses the elevation down by `bass_dip_deg * dip` so the
  sound dives "below your chin" on every kick.

All time is sample-clock time fed in by the engine (block starts), so
the tracker is immune to wall-clock jitter.
"""
import math
from collections import deque
import numpy as np


class BeatSync:
    def __init__(self, dt, frot=0.14, beats_per_rev=0, rev_sec=6.0,
                 est_every=64, win=384, min_hist=192,
                 lag_min=0.33, lag_max=1.05, min_conf=0.18, pll_alpha=0.01):
        self.dt = dt                    # block duration [s]
        self.frot = frot                # free-run rotation Hz (no lock yet)
        self.nrev = int(beats_per_rev)  # beats per revolution; 0 = auto
        self.rev_sec = rev_sec          # target revolution length (auto mode)
        self.est_every = est_every      # blocks between BPM analyses
        self.win = win                  # onset history window (blocks)
        self.min_hist = min_hist
        self.lag_min, self.lag_max = lag_min, lag_max
        self.min_conf = min_conf
        self.alpha = pll_alpha

        self.theta = 0.0                # current azimuth [rad], continuous
        self._t = 0.0                   # sample-clock time of next block [s]
        self.bpm = 0.0
        self.period = None              # beat period [s], None = unlocked
        self.anchor = None              # kick time defining the phase grid
        self.locked = False
        self._misses = 0
        self._since_grid = 0.0
        self._dip = 0.0
        self._n = 0
        self.last_lock_msg = ""

        self.onsets = deque(maxlen=win)
        self._slow = 0.0                # ~120 ms EMA of bass rms (onset base)
        self._rms_1s = 0.0              # ~1 s EMA (prominence reference)
        self._on_mean = 0.0             # ~2 s EMA of onset (adaptive threshold)
        self._c_slow = 1.0 - math.exp(-dt / 0.12)
        self._c_1s = 1.0 - math.exp(-dt / 1.0)
        self._c_thr = 1.0 - math.exp(-dt / 2.0)
        self._c_dip_a = 1.0 - math.exp(-dt / 0.06)
        self._c_dip_r = 1.0 - math.exp(-dt / 0.35)

    # ---- API ----
    @property
    def dip(self):
        return self._dip

    def feed(self, rms, t):
        """One block of bass-band RMS starting at sample-time t.

        Returns the bass-dip envelope (0..1) for this block."""
        onset = max(0.0, rms - self._slow)
        self._slow += (rms - self._slow) * self._c_slow
        self._rms_1s += (rms - self._rms_1s) * self._c_1s
        self._on_mean += (onset - self._on_mean) * self._c_thr
        self.onsets.append(onset)
        self._n += 1
        self._t = t

        # dip is driven by the kick ONSET (impulse relative to loudness):
        # sustained bass must not hold the dip down, only transients do
        prom = onset / (self._rms_1s + 1e-6)
        tgt = min(1.0, max(0.0, (prom - 0.8) / 1.2))
        c = self._c_dip_a if tgt > self._dip else self._c_dip_r
        self._dip += (tgt - self._dip) * c

        if self.locked and self.period and onset > max(0.02, 2.5 * self._on_mean + 0.005):
            self._anchor_try(t - 0.5 * self.dt)

        if self._n % self.est_every == 0:
            self._estimate()
        return self._dip

    def block_theta(self, dur, sr, nsamp):
        """Per-sample azimuth for one block, advancing the internal state.

        Unlocked: constant free-run rate (identical to the legacy engine).
        Locked:   base rate + a small PLL pull toward the kick grid.
        """
        if not self.locked or self.anchor is None or not self.period:
            w = 2.0 * math.pi * self.frot
        else:
            t_rev = self.period * self.nrev
            w0 = 2.0 * math.pi / t_rev
            target = 2.0 * math.pi * (((self._t - self.anchor) / t_rev) % 1.0)
            d = math.atan2(math.sin(target - self.theta),
                           math.cos(target - self.theta))
            corr = self.alpha * d / max(dur, 1e-9)
            lim = 0.5 * w0
            corr = max(-lim, min(lim, corr))
            w = w0 + corr
        arr = self.theta + np.arange(nsamp) * (w / sr)
        self.theta = (self.theta + w * dur) % (2.0 * math.pi)
        return arr

    # ---- internals ----
    def _anchor_try(self, t_on):
        if self.anchor is None or not self.period:
            self.anchor = t_on          # first kick after locking
            self._since_grid = 0.0
            return
        k = (t_on - self.anchor) / self.period
        kf = round(k)
        if kf < 1:
            return
        pb = abs(k - kf)                # beat-grid error, in beats
        if pb < 0.2:                    # this kick is ON the beat grid
            self._since_grid = 0.0
            if kf % self.nrev == 0:     # ...and on the revolution grid:
                self.anchor = t_on      # refresh exactly, phase preserved
        else:                           # off-grid kick (syncopation / drift)
            self._since_grid += self.dt
            if self._since_grid > 6.0:  # grid went stale -> re-anchor
                self.anchor = t_on
                self._since_grid = 0.0

    def _estimate(self):
        if len(self.onsets) < self.min_hist:
            return
        e = np.asarray(self.onsets, dtype=np.float64)
        if e.std() < 1e-4:
            self._miss()
            return
        e = e - e.mean()
        ac = np.correlate(e, e, "full")[len(e) - 1:]
        if ac[0] <= 1e-12:
            self._miss()
            return
        l_min = max(2, int(math.ceil(self.lag_min / self.dt)))
        l_max = min(len(ac) - 2, int(math.floor(self.lag_max / self.dt)))
        if l_max <= l_min:
            self._miss()
            return
        l_best = int(np.argmax(ac[l_min:l_max + 1])) + l_min
        # prefer the fundamental: smallest lag whose correlation is comparable
        for l_c in range(l_min, l_best):
            if ac[l_c] >= 0.85 * ac[l_best]:
                l_best = l_c
                break
        denom = 2.0 * (ac[l_best - 1] - 2.0 * ac[l_best] + ac[l_best + 1])
        delta = 0.0
        if abs(denom) > 1e-12:
            delta = min(0.6, max(-0.6, (ac[l_best - 1] - ac[l_best + 1]) / denom))
        conf = ac[l_best] / ac[0]
        t_beat = (l_best + delta) * self.dt
        if conf < self.min_conf or not (self.lag_min <= t_beat <= self.lag_max):
            self._miss()
            return
        self.period = t_beat if self.period is None else 0.5 * self.period + 0.5 * t_beat
        if self.nrev <= 0:              # auto: aim for rev_sec-long revolutions
            self.nrev = int(min(16, max(2, round(self.rev_sec / self.period))))
        self.bpm = 60.0 / self.period
        self.locked = True
        self._misses = 0
        msg = f"BPM lock: {self.bpm:.1f} (beat {self.period * 1000:.0f} ms, " \
              f"{self.nrev} beats/rev = {self.period * self.nrev:.2f} s, conf {conf:.2f})"
        if msg != self.last_lock_msg:
            self.last_lock_msg = msg
            print(msg, flush=True)

    def _miss(self):
        if self.locked:
            self._misses += 1
            if self._misses >= 4:       # ~4*est_every*dt without confidence
                self.locked = False
                self.bpm = 0.0
                print("BPM unlock (free-run)", flush=True)
