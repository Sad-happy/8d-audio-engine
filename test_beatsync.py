# -*- coding: utf-8 -*-
"""Offline unit tests for beat_sync.BeatSync (no audio devices needed).

Run:  python test_beatsync.py
"""
import math
import numpy as np

from beat_sync import BeatSync

DT = 2048 / 48000.0            # engine block duration
SR = 48000


def kick_train(bpm, seconds, swing=0.0):
    """Bass-band RMS of a kick drum train: sharp attack each beat."""
    period = 60.0 / bpm
    n = int(seconds / DT)
    t = np.arange(n) * DT
    ph = (t + swing) % period
    env = np.exp(-ph / 0.09) * 0.55
    return 0.06 + env          # floor so the band is never fully dead


def run(bs, rms):
    """Feed a whole rms track; return theta path, dips and kick flags."""
    thetas, dips = np.empty(len(rms)), np.empty(len(rms))
    t = 0.0
    for i, r in enumerate(rms):
        dips[i] = bs.feed(float(r), t)
        bs.block_theta(DT, SR, 1)
        thetas[i] = bs.theta
        t += DT
    return thetas, dips


def wrap(x):
    return (x + math.pi) % (2 * math.pi) - math.pi


def test_lock_120():
    bs = BeatSync(dt=DT, frot=0.14)
    rms = kick_train(120, 30)
    thetas, dips = run(bs, rms)
    assert bs.locked, "did not lock at 120 BPM"
    assert abs(bs.bpm - 120) < 2.0, f"bpm {bs.bpm:.1f} != 120"
    # revolution should be ~6 s (auto beats-per-rev)
    t_rev = 60.0 / 120 * bs.nrev
    assert 4.5 <= t_rev <= 8.0, f"rev {t_rev:.2f}s outside 4.5-8"
    # dip fires on kicks, clearly lower between (contrast is the property)
    period = 60.0 / 120
    n = len(rms)
    idx_on = [i for i in range(int(20 / DT), n) if (i * DT) % period < DT * 1.5]
    idx_off = [i for i in range(int(20 / DT), n) if 0.3 * period < (i * DT) % period < 0.8 * period]
    d_on = max(dips[i] for i in idx_on)
    d_off = float(np.mean([dips[i] for i in idx_off]))
    assert d_on > 0.30, f"no dip on kicks ({d_on:.2f})"
    assert d_off < 0.5 * d_on, f"dip stuck between kicks (on {d_on:.2f} vs off {d_off:.2f})"
    print(f"OK lock 120bpm: bpm={bs.bpm:.1f} nrev={bs.nrev} "
          f"dip_on={d_on:.2f} dip_off={d_off:.2f}")


def test_lock_90():
    bs = BeatSync(dt=DT, frot=0.14)
    rms = kick_train(90, 30)
    thetas, _ = run(bs, rms)
    assert bs.locked, "did not lock at 90 BPM"
    assert abs(bs.bpm - 90) < 2.5, f"bpm {bs.bpm:.1f} != 90"
    print(f"OK lock 90bpm: bpm={bs.bpm:.1f} nrev={bs.nrev}")


def test_phase_and_continuity():
    bs = BeatSync(dt=DT, frot=0.14)
    rms = kick_train(120, 50)
    thetas, _ = run(bs, rms)
    # no snaps: max per-block phase step must stay sane. The internal theta
    # wraps at 2*pi, so measure steps modulo 2*pi (a 2*pi jump is continuous).
    d = np.diff(thetas)
    steps = np.abs((d + math.pi) % (2 * math.pi) - math.pi)
    assert steps.max() < 0.5, f"phase snap: {steps.max():.3f} rad"
    period = 60.0 / 120
    t_rev = period * bs.nrev
    # after locking, the rotation rate must equal one revolution per t_rev.
    # NOTE: unwrap first -- an exactly locked theta returns to its starting
    # value mod 2*pi and the raw difference would read ~0.
    n_rev_blocks = int(t_rev / DT)
    i0 = len(thetas) - 3 * n_rev_blocks
    assert i0 > int(30 / DT), "not enough post-lock audio for rate check"
    uw = np.unwrap(thetas)
    rate = (uw[-1] - uw[i0]) / (3 * t_rev)
    want = 2 * math.pi / t_rev
    assert abs(rate - want) < 0.02 * want, f"rate {rate:.4f} vs {want:.4f} rad/s"
    # theta advances by 2*pi/nrev from kick to kick (absolute phase origin is
    # deliberately wherever the PLL picked it up)
    kick_idx = [i for i in range(int(25 / DT), len(thetas))
                if (i * DT) % period < DT * 1.5]
    advs = [(thetas[b] - thetas[a] + math.pi) % (2 * math.pi) - math.pi
            for a, b in zip(kick_idx[:-1], kick_idx[1:])]
    want_adv = 2 * math.pi / bs.nrev
    med = float(np.median(advs))
    assert abs(med - want_adv) < 0.10, f"kick advance {med:.3f} vs {want_adv:.3f}"
    print(f"OK phase: max_step={steps.max():.3f} rad, rate={rate:.3f} "
          f"(want {want:.3f}), kick advance={med:.3f} (want {want_adv:.3f})")


def test_silence_free_runs():
    bs = BeatSync(dt=DT, frot=0.14)
    rms = np.zeros(int(20 / DT))
    thetas, _ = run(bs, rms)
    assert not bs.locked, "locked on silence"
    uw = np.unwrap(thetas)
    rate = (uw[-1] - uw[0]) / ((len(thetas) - 1) * DT)
    assert abs(rate - 2 * math.pi * 0.14) < 1e-6, "free-run rate drifted"
    print("OK silence: stays in free-run at base rate")


def test_offbeat_swings():
    # kicks consistently 1/8 late: the PLL must still lock. Swing only shifts
    # the phase origin, so check the rate and the per-kick advance, not
    # absolute theta at kick times.
    bs = BeatSync(dt=DT, frot=0.14)
    rms = kick_train(110, 50, swing=60.0 / 110 * 0.125)
    thetas, _ = run(bs, rms)
    assert bs.locked, "did not lock with swung kicks"
    period = 60.0 / 110
    t_rev = period * bs.nrev
    i0 = len(thetas) - 4 * int(t_rev / DT)
    uw = np.unwrap(thetas)
    rate = (uw[-1] - uw[i0]) / (4 * t_rev)
    want = 2 * math.pi / t_rev
    assert abs(rate - want) < 0.03 * want, f"rate {rate:.4f} vs {want:.4f} rad/s"
    kick_idx = [i for i in range(int(25 / DT), len(thetas))
                if (i * DT + period * 0.125) % period < DT * 1.5]
    advs = [(thetas[b] - thetas[a] + math.pi) % (2 * math.pi) - math.pi
            for a, b in zip(kick_idx[:-1], kick_idx[1:])]
    med = float(np.median(advs))
    want_adv = 2 * math.pi / bs.nrev
    assert abs(med - want_adv) < 0.12, f"kick advance {med:.3f} vs {want_adv:.3f}"
    print(f"OK swung 110bpm: bpm={bs.bpm:.1f} rate={rate:.3f} "
          f"advance={med:.3f}")


if __name__ == "__main__":
    test_lock_120()
    test_lock_90()
    test_phase_and_continuity()
    test_silence_free_runs()
    test_offbeat_swings()
    print("ALL BEAT-SYNC TESTS PASSED")
