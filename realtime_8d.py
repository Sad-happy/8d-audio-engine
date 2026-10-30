#!/usr/bin/env python3
"""Realtime 8D (binaural rotation) audio engine for Windows.

Routes your system audio through a virtual cable, applies a binaural
rotation DSP chain (Woodworth ITD + 3-band ILD + head-shadow + presence
EQ + AGC + a click-free feed-forward lookahead limiter) and plays the
result to your real output device (headphones).

Wiring:  players -> default output = VB-Cable "CABLE Input"
         engine reads "CABLE Output" (loopback) -> DSP -> headphones
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # tiny blocks; BLAS threads blow up memory in detached procs
os.environ.setdefault("OMP_NUM_THREADS", "1")
import sys, time, math, argparse, threading
import numpy as np
import pyaudiowpatch as pw
from scipy import signal as sps
from scipy.ndimage import maximum_filter1d

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# DSP constants are filled in by configure() from CLI args (globals keep the
# processing functions simple; all values are the tuned "v3" preset).
SR = BLOCK = F_ROT = ELEV_F = ELEV_A = None
R = C = BASE = None
K = DULL = WARM = PRESENCE = TARGET_RMS = MAXD = None
LA = CEIL = ATT_E = REL_E = ATT_M = REL_M = None
SOS_PRE = SOS_LO = SOS_HI = None


def configure(a):
    """Resolve all DSP constants from parsed args (globals for the DSP code)."""
    global SR, BLOCK, F_ROT, ELEV_F, ELEV_A, R, C, BASE, K, DULL, WARM
    global PRESENCE, TARGET_RMS, MAXD, LA, CEIL, ATT_E, REL_E, ATT_M, REL_M
    global SOS_PRE, SOS_LO, SOS_HI
    SR, BLOCK = a.rate, a.block
    F_ROT = a.frot                 # Hz -> a.frot*2 seconds per revolution
    ELEV_F = 2 * F_ROT             # helix: two vertical cycles per revolution
    ELEV_A = math.radians(a.elev_deg)
    R, C = 0.0875, 343.0           # head radius [m], speed of sound [m/s]
    BASE = 0.0025                  # 2.5 ms base delay (head start)
    K = {"low": 0.08, "mid": 0.30, "high": 0.55}   # per-band far-ear shadow
    DULL, WARM = 0.35, 0.10        # behind-head high dulling / low-mid warmth
    PRESENCE = a.presence          # vocal-presence EQ mix (2.5-6.5 kHz)
    TARGET_RMS = a.target_rms
    MAXD = int(np.ceil((BASE + R * np.pi / C) * SR)) + 4
    LA = 240                                   # 5 ms lookahead @ 48 kHz
    CEIL = 0.95
    ATT_E = 1.0 - math.exp(-1.0 / (0.00001 * SR))   # ~instant detector attack
    REL_E = 1.0 - math.exp(-1.0 / (0.120 * SR))     # 120 ms detector release
    ATT_M = 1.0 - math.exp(-1.0 / (0.001 * SR))     # 1 ms attack on gain curve
    REL_M = 1.0 - math.exp(-1.0 / (0.002 * SR))     # 2 ms release on gain curve
    SOS_PRE = sps.butter(2, [2500 / (SR / 2), 6500 / (SR / 2)], "bandpass", output="sos")
    SOS_LO = sps.butter(4, 700 / (SR / 2), "lowpass", output="sos")
    SOS_HI = sps.butter(4, 2000 / (SR / 2), "highpass", output="sos")


# ---- filter/delay states (lazy-init on first block) ----
zi_pre = zi_lo = zi_hi = None
hist_L = hist_R = None
gain_agc = 1.0
n_global = 0


def pick_devices(p, in_sub, out_sub):
    """Input: loopback device matching in_sub (WASAPI preferred).
    Output: device matching out_sub; empty out_sub = system default output."""
    cin = cout = None
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        n = d["name"]
        wasapi = (d["hostApi"] == 2)
        if d["maxInputChannels"] and in_sub in n:
            if cin is None or (wasapi and p.get_device_info_by_index(cin)["hostApi"] != 2):
                cin = i
        if d["maxOutputChannels"] and out_sub and out_sub in n:
            if cout is None or (wasapi and p.get_device_info_by_index(cout)["hostApi"] != 2):
                cout = i
    if cout is None and not out_sub:
        cout = p.get_default_output_device_info()["index"]
    return cin, cout


# ---- lookahead limiter (feed-forward max-hold, click-free) ----
# A trailing follower can never anticipate short transients (a 4-sample pulse
# ends before any attack completes). Instead compute the TRUE requirement:
#   E  = near-instant follower of |x|/CEIL (fast attack, 120 ms release)
#   M  = sliding max of E over the lookahead window  -> exact future need,
#        final for raw [kB-LA, kB+B-LA) which is exactly the emitted audio
#   M2 = follower on M (1 ms attack / 2 ms release, floor 1.0) -> gain curve
#        is continuous; M always jumps >= LA before the peak reaches the
#        delayed output, so the 1 ms attack has settled in time. y <= CEIL.
env_lvl = 1.0
m_lvl = 1.0
E_tail = None                                    # E values of previous raw samples
dline = None                                     # raw-audio delay line


def limit(out):
    # out: (BLOCK,2) post-AGC raw audio -> peak-safe audio (delayed by LA)
    global env_lvl, m_lvl, E_tail, dline
    B = len(out)
    # 1) fast-attack / slow-release follower E over the new raw samples
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
    # 2) sliding max over the lookahead window (window must be fully inside
    #    roll, so M[i] is final exactly for the emitted audio positions)
    roll = np.concatenate([E_tail, E])
    M = maximum_filter1d(roll, size=LA + 1, origin=-(LA // 2))[:B]
    E_tail = E[-LA:].copy()
    # 3) smooth follower on the requirement (continuous gain curve, floor 1)
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
    # 4) apply to LA-delayed audio
    buf = np.concatenate([dline, out], axis=0)   # (LA+BLOCK, 2)
    y = buf[:B] * (1.0 / g.astype(np.float32))[:, None]
    dline = buf[B:].copy()
    return np.clip(y, -0.98, 0.98)               # belt, should never engage


def lagrange_delay(pre, d_samp, hist):
    # output[j] = pre delayed by d_samp[j] samples, with `hist` as context
    # ext[k] holds global sample (n_global - len(hist) + k)
    ext = np.concatenate([hist, pre])            # len = hist + BLOCK
    off = len(hist)
    pos = np.arange(len(pre)) + off - d_samp     # float read index
    i0 = np.floor(pos).astype(np.int64)
    fr = pos - i0                                # frac in [0,1)
    i0c = np.clip(i0, 1, len(ext) - 3)           # keep i0+2 valid
    y0, y1, y2, y3 = ext[i0c-1], ext[i0c], ext[i0c+1], ext[i0c+2]
    return (y0*(-fr*(fr-1)*(fr-2)/6) + y1*((fr+1)*(fr-1)*(fr-2)/2) +
            y2*(-(fr+1)*fr*(fr-2)/2) + y3*((fr+1)*fr*(fr-1)/6))


def process(x):   # x: (BLOCK,2) float32 in [-1,1]  ->  (BLOCK,2)
    global zi_pre, zi_lo, zi_hi, hist_L, hist_R, gain_agc, n_global, E_tail, dline
    mono = x.mean(axis=1).astype(np.float32)
    if zi_pre is None:
        zi_pre = sps.sosfilt_zi(SOS_PRE) * (mono[0] if mono[0] else 1.0)
        zi_lo = sps.sosfilt_zi(SOS_LO) * mono[0]
        zi_hi = sps.sosfilt_zi(SOS_HI) * mono[0]
    pres, zi_pre = sps.sosfilt(SOS_PRE, mono, zi=zi_pre)
    mono = mono + PRESENCE * pres.astype(np.float32)
    lo, zi_lo = sps.sosfilt(SOS_LO, mono, zi=zi_lo)
    hi, zi_hi = sps.sosfilt(SOS_HI, mono, zi=zi_hi)
    lo, hi = lo.astype(np.float32), hi.astype(np.float32)
    mid = mono - lo - hi

    n = n_global + np.arange(BLOCK)
    theta = 2 * np.pi * F_ROT * n / SR
    elev = ELEV_A * np.sin(2 * np.pi * ELEV_F * n / SR)
    sin_t = np.sin(theta).astype(np.float32)
    cos_t = (np.cos(theta) * np.cos(elev)).astype(np.float32)
    itd = (R / C) * (sin_t + theta)              # Woodworth ITD
    itd = np.clip(itd, -R * np.pi / C, R * np.pi / C)
    sL, sR = np.maximum(0, sin_t), np.maximum(0, -sin_t)
    b = np.maximum(0, -cos_t)                    # 0 front -> 1 back
    gL = (1-K["low"]*sR)*(1+WARM*b), (1-K["mid"]*sR), (1-K["high"]*sR)*(1-DULL*b)
    gR = (1-K["low"]*sL)*(1+WARM*b), (1-K["mid"]*sL), (1-K["high"]*sL)*(1-DULL*b)
    L_pre = lo*gL[0] + mid*gL[1] + hi*gL[2]
    R_pre = lo*gR[0] + mid*gR[1] + hi*gR[2]

    dL = (BASE + np.maximum(0,  itd)) * SR
    dR = (BASE + np.maximum(0, -itd)) * SR
    L = lagrange_delay(L_pre, dL, hist_L).astype(np.float32)
    Rc = lagrange_delay(R_pre, dR, hist_R).astype(np.float32)
    hist_L = np.concatenate([hist_L, L_pre])[-(MAXD+4):]
    hist_R = np.concatenate([hist_R, R_pre])[-(MAXD+4):]
    n_global += BLOCK

    out = np.stack([L, Rc], axis=1)
    rms = float(np.sqrt((out.astype(np.float64) ** 2).mean()))
    if rms > 1e-4:
        tgt = float(np.clip(TARGET_RMS / rms, 0.6, 1.6))
        gain_agc += (tgt - gain_agc) * 0.02      # smooth, ~0.4 s settle
    out *= gain_agc
    return limit(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-substr", default="CABLE Output",
                    help="capture (loopback) device name substring (default: %(default)s)")
    ap.add_argument("--out-substr", default="",
                    help="output device name substring; empty = system default output")
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--block", type=int, default=2048, help="frames per buffer")
    ap.add_argument("--frot", type=float, default=0.14,
                    help="rotation frequency Hz (0.14 = ~7 s/revolution)")
    ap.add_argument("--elev-deg", type=float, default=25.0,
                    help="vertical wobble amplitude in degrees")
    ap.add_argument("--presence", type=float, default=0.45,
                    help="2.5-6.5 kHz presence EQ mix")
    ap.add_argument("--target-rms", type=float, default=0.11,
                    help="AGC target RMS (lower = more transient headroom)")
    args = ap.parse_args()
    configure(args)

    global hist_L, hist_R, E_tail, dline
    hist_L = np.zeros(MAXD + 4, np.float32)
    hist_R = np.zeros(MAXD + 4, np.float32)
    E_tail = np.ones(LA)
    dline = np.zeros((LA, 2), np.float32)

    here = os.path.dirname(os.path.abspath(__file__))
    pidf = os.path.join(here, "engine.pid")
    with open(pidf, "w") as f:
        f.write(str(os.getpid()))

    stop = threading.Event()

    def watchdog():
        while not stop.is_set():
            if os.path.exists(os.path.join(here, "stop.flag")):
                stop.set()
                break
            time.sleep(0.5)
    threading.Thread(target=watchdog, daemon=True).start()

    with pw.PyAudio() as p:
        cin, cout = pick_devices(p, args.in_substr, args.out_substr)
        if cin is None:
            print(f"FATAL: no input device matching '{args.in_substr}'")
            print("hint: install VB-Cable and set it as the default output device")
            sys.exit(2)
        din = p.get_device_info_by_index(cin)["name"]
        if cout is None:
            print(f"FATAL: no output device matching '{args.out_substr}'")
            sys.exit(2)
        dout = p.get_device_info_by_index(cout)["name"]
        print(f"in={cin} ({din})  out={cout} ({dout})")

        underruns = [0]
        t_last = [time.time()]

        def cb(in_data, frames, info, flag):
            x = np.frombuffer(in_data, np.int16).reshape(-1, 2).astype(np.float32) / 32768
            if stop.is_set():
                return np.zeros_like(x).tobytes(), pw.paContinue
            try:
                y = process(x)
            except Exception:
                import traceback
                traceback.print_exc()        # bypass dry signal instead of dying
                y = x
            if flag:                         # input/output underflow/overflow bits
                underruns[0] += 1
            now = time.time()
            if now - t_last[0] > 30:
                print(f"stat: {now - t_last[0]:.0f}s underruns={underruns[0]} "
                      f"gain={gain_agc:.2f}", flush=True)
                t_last[0] = now
            return (y * 32767).astype(np.int16).tobytes(), pw.paContinue

        stream = p.open(format=pw.paInt16, channels=2, rate=SR,
                        input=True, output=True,
                        input_device_index=cin, output_device_index=cout,
                        frames_per_buffer=BLOCK, stream_callback=cb)
        print("engine running; create stop.flag next to this script to quit")
        while not stop.is_set():
            time.sleep(0.2)
        stream.stop_stream(); stream.close()
    # only remove the pid record if it is ours (avoid clobbering a freshly
    # restarted engine's pid when a stop/start race overlaps two instances)
    try:
        if open(pidf).read().strip() == str(os.getpid()):
            os.remove(pidf)
    except OSError:
        pass
    flag = os.path.join(here, "stop.flag")
    if os.path.exists(flag):
        os.remove(flag)
    print("engine stopped")


if __name__ == "__main__":
    main()
