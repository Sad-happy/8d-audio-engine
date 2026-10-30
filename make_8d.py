#!/usr/bin/env python3
"""Offline 8D (binaural rotation) renderer: WAV -> WAV.

Applies the same model as the realtime engine (Woodworth ITD + 3-band ILD +
behind-dulling pinna shadow + presence EQ) without any audio devices, so it
runs anywhere Python runs. Best listened to on headphones.
"""
import argparse, wave
import numpy as np
from scipy import signal as sps
from spatial import woodworth_itd


def read_wav(path):
    w = wave.open(path, "rb")
    n, ch, sr, sw = w.getnframes(), w.getnchannels(), w.getframerate(), w.getsampwidth()
    raw = w.readframes(n); w.close()
    if sw != 2:
        raise ValueError(f"unsupported sample width: {sw} (want 16-bit PCM)")
    return np.frombuffer(raw, np.int16).astype(np.float32).reshape(-1, ch) / 32768, sr


def var_delay(sig, d_samp, maxd):
    # 4-point Lagrange fractional delay (far less HF loss than linear interp)
    ext = np.concatenate([np.zeros(maxd + 2, np.float32), sig])
    idx = np.arange(len(sig)) + maxd + 1
    i0 = (np.floor(idx - d_samp)).astype(np.int64)
    fr = idx - d_samp - i0                     # fractional in [0,1)
    y0, y1, y2, y3 = ext[i0-1], ext[i0], ext[i0+1], ext[i0+2]
    return (y0 * (-fr*(fr-1)*(fr-2)/6) +
            y1 * ((fr+1)*(fr-1)*(fr-2)/2) +
            y2 * (-(fr+1)*fr*(fr-2)/2) +
            y3 * ((fr+1)*fr*(fr-1)/6))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="src", required=True, help="input WAV (16-bit PCM)")
    ap.add_argument("--out", dest="dst", required=True, help="output WAV (16-bit PCM)")
    ap.add_argument("--frot", type=float, default=0.14,
                    help="rotation frequency Hz (0.14 = ~7 s/revolution)")
    ap.add_argument("--elev-deg", type=float, default=25.0,
                    help="vertical wobble amplitude in degrees")
    args = ap.parse_args()

    x, sr = read_wav(args.src)
    mono = x.mean(axis=1).astype(np.float32)
    # presence compensation: restore vocal clarity lost to spatial dulling
    mono = mono + 0.45 * sps.sosfilt(
        sps.butter(2, [2500 / (sr / 2), 6500 / (sr / 2)], "bandpass", output="sos"),
        mono).astype(np.float32)
    N = len(mono)
    print(f"input {N/sr:.1f}s @{sr}, "
          f"rms={np.sqrt((mono.astype(np.float64)**2).mean()):.4f}")

    # ---- rotation trajectory (helix: azimuth + vertical wobble) ----
    theta = 2 * np.pi * args.frot * np.arange(N) / sr      # azimuth(t), 0=front, CCW+
    elev = np.radians(args.elev_deg) * np.sin(2 * np.pi * 2 * args.frot * np.arange(N) / sr)
    cos_t = np.cos(theta) * np.cos(elev)                   # front-back component
    sin_t = np.sin(theta)                                  # left-right component

    # ---- Woodworth ITD: itd(theta) = (r/c)(sin(theta) + theta) ----
    R, C = 0.0875, 343.0
    itd = woodworth_itd(theta, R, C)                      # s, >0 = source left
    BASE = 0.0025                                          # 2.5 ms head-start

    # ---- 3-band split (butterworth sosfilt) ----
    lo = sps.sosfilt(sps.butter(4, 700 / (sr / 2), "lowpass", output="sos"), mono).astype(np.float32)
    hi = sps.sosfilt(sps.butter(4, 2000 / (sr / 2), "highpass", output="sos"), mono).astype(np.float32)
    mid = mono - lo - hi

    # ---- ILD: far-ear shadow, stronger at high freq ----
    K = {"low": 0.08, "mid": 0.30, "high": 0.55}
    sL, sR = np.maximum(0, sin_t), np.maximum(0, -sin_t)   # source-side fraction
    gL = {"low": 1-K["low"]*sR, "mid": 1-K["mid"]*sR, "high": 1-K["high"]*sR}
    gR = {"low": 1-K["low"]*sL, "mid": 1-K["mid"]*sL, "high": 1-K["high"]*sL}

    # ---- behind-dulling: back hemisphere dulls highs, warms low-mids ----
    b = np.maximum(0, -cos_t)                              # 0 front -> 1 back
    gl_hi, gl_lo = 1 - 0.35 * b, 1 + 0.10 * b
    for d in (gL, gR):                                     # head shadow is shared
        d["high"] = d["high"] * gl_hi
        d["low"] = d["low"] * gl_lo

    B = {"lo": lo, "mid": mid, "hi": hi}
    L_pre = B["lo"]*gL["low"] + B["mid"]*gL["mid"] + B["hi"]*gL["high"]
    R_pre = B["lo"]*gR["low"] + B["mid"]*gR["mid"] + B["hi"]*gR["high"]

    dL_tot = (BASE * sr + np.maximum(0, -itd) * sr)        # continuous delay
    dR_tot = (BASE * sr + np.maximum(0,  itd) * sr)
    maxd = int(np.ceil(max(dL_tot.max(), dR_tot.max()))) + 2
    L = var_delay(L_pre, dL_tot, maxd)
    R = var_delay(R_pre, dR_tot, maxd)

    # ---- normalize: match input loudness, guard peak ----
    out = np.stack([L, R], axis=1)
    in_rms = np.sqrt((mono.astype(np.float64) ** 2).mean())
    out_rms = np.sqrt((out.astype(np.float64) ** 2).mean())
    out *= (in_rms / out_rms)
    peak = np.abs(out).max()
    if peak > 0.97:
        out *= 0.97 / peak
    print(f"out rms={np.sqrt((out.astype(np.float64)**2).mean()):.4f} "
          f"peak={np.abs(out).max():.3f}")

    w = wave.open(args.dst, "wb")
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((np.clip(out, -1, 1) * 32767).astype(np.int16).tobytes())
    w.close()
    print("saved", args.dst)


if __name__ == "__main__":
    main()

