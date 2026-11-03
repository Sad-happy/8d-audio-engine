#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8D Fireworks v2 — an audio-driven fireworks show (tkinter + WASAPI loopback).

Feature parity with the 8d-fireworks plugin renderer (native-dist/fireworks.js):

  - twelve shell types: peony / chrysanthemum / willow / ring (sometimes
    double) / heart / crossette (sparks split mid-flight) / palm / glitter /
    spiral / double / pistil / strobe, plus a multi-break "grand" finale with
    a delayed secondary burst;
  - tiered shell selection by kick strength;
  - hue-family palettes so a burst stays inside one colour family;
  - a simple crescent moon (the plugin's real-lunar-phase moon was reverted
    here: tkinter has no alpha channel, so the fake-alpha glow rendered as
    opaque discs occluding the star field, and the item count stuttered);
  - a star field with a milky-way band, twinkle, cross flares and occasional
    meteors;
  - comet-tail sparks with a flickering head.

Bass onsets launch rockets; treble spikes crackle gold. With the 8D engine
live this reacts to the final post-8D sound (capture the headphone loopback).

Rendering model: the canvas is fully redrawn every frame (delete + draw), so
there is no item churn / z-order burial. Tkinter has no alpha channel, so all
"translucent" colours are pre-blended toward the background colour.

Usage:
  python fireworks.py                          # default output loopback
  python fireworks.py --capture-substr CABLE   # pre-engine signal
  python fireworks.py --demo                   # synthetic bass pulses
  python fireworks.py --top                    # keep window on top
  python fireworks.py --density 130            # spark count, 50..150

Deps: numpy, PyAudioWPatch; tkinter ships with Python on Windows.
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import sys, math, time, threading, argparse, random, colorsys
from collections import deque
import numpy as np
import pyaudiowpatch as pw

N = 2048          # FFT window (samples)
FPS = 30          # animation rate
TAU = math.pi * 2

ap = argparse.ArgumentParser()
ap.add_argument("--capture-substr", default="",
                help="empty = loopback of the system default output device")
ap.add_argument("--width", type=int, default=586)
ap.add_argument("--height", type=int, default=340)
ap.add_argument("--top", action="store_true", help="keep window on top")
ap.add_argument("--density", type=int, default=100,
                help="spark count percent, 50..150")
ap.add_argument("--demo", action="store_true",
                help="ignore audio, fire synthetic pulses (testing)")
a = ap.parse_args()
density = min(150, max(50, a.density))

# ---------- audio capture (callback thread only appends bytes) ----------
frames = deque(maxlen=80)
lock = threading.Lock()

def on_data(data, fc, info, flags):
    with lock:
        frames.append(bytes(data))
    return (None, pw.paContinue)

stream = None
if not a.demo:
    p = pw.PyAudio()
    if a.capture_substr:
        dev = None
        for info in p.get_loopback_device_info_generator():
            if a.capture_substr.lower() in info["name"].lower():
                dev = info
                break
        if dev is None:
            print("no loopback device matches:", a.capture_substr)
            sys.exit(2)
    else:
        dev = p.get_default_wasapi_loopback()
    SR = int(dev["defaultSampleRate"])
    CH = int(dev["maxInputChannels"])
    print(f"capturing [{dev['index']}] {dev['name']}  {SR}Hz {CH}ch", flush=True)
    stream = p.open(format=pw.paInt16, channels=CH, rate=SR, input=True,
                    input_device_index=dev["index"], frames_per_buffer=1024,
                    stream_callback=on_data)
else:
    SR, CH = 48000, 2
    print("demo mode: synthetic bass pulses", flush=True)

freqs = np.fft.rfftfreq(N, 1.0 / SR)
win = np.hanning(N).astype(np.float32)
BASS = (freqs >= 30) & (freqs <= 160)
TREB = (freqs >= 3000) & (freqs <= 12000)

def latest_block():
    with lock:
        if not frames:
            return None
        data = b"".join(frames)
        frames.clear()
    return data

def band_db(mag, mask):
    v = float(mag[mask].max()) if mask.any() and mag[mask].size else 0.0
    return 20.0 * math.log10(v + 1e-7)

# ---------- tkinter UI ----------
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
import tkinter as tk

root = tk.Tk()
root.title("8D Fireworks")
root.geometry(f"{a.width}x{a.height}-60-110")
root.configure(bg="#070a14")
root.resizable(False, False)
if a.top:
    root.attributes("-topmost", True)

cv = tk.Canvas(root, width=a.width, height=a.height, bg="#070a14",
               highlightthickness=0)
cv.pack()
W, H = a.width, a.height
S = min(2.4, max(1.0, H / 340.0))      # physics scale with window height
BG = "#070a14"                         # sky colour (moon overlay + canvas bg)
BG_RGB = (7, 10, 20)

rng = random.Random(20261102)

def rnd(a1=None, b1=None):
    if a1 is None:
        return rng.random()
    return a1 + (b1 - a1) * rng.random()

def pick(arr):
    return arr[int(rnd() * len(arr)) % len(arr)]

# ---------- palette: hue-family groups (from the plugin renderer) ----------
PALETTES = (
    (0.015, 0.045, 0.085),      # ember red / orange
    (0.095, 0.12, 0.145),       # gold / amber
    (0.50, 0.53, 0.56),         # ice blue
    (0.58, 0.62, 0.665),        # blue / indigo
    (0.74, 0.79, 0.845),        # violet / magenta
    (0.90, 0.945, 0.975),       # rose / pink
    (0.30, 0.335, 0.375),       # jade / teal
    (0.13, 0.955, 0.58),        # warm + rose duet
)
def hue_set():
    return pick(PALETTES)

def _mix_rgb(fg, alpha):
    return tuple(int(BG_RGB[i] * (1 - alpha) + fg[i] * alpha) for i in range(3))

def cc(hue, sat, light, alpha=1.0):
    """Colour as hex, pre-blended toward the background (fake alpha)."""
    hue = (hue % 1.0 + 1.0) % 1.0
    sat = min(1.0, max(0.0, sat))
    light = min(1.0, max(0.0, light))
    alpha = min(1.0, max(0.0, alpha))
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    r, g, b = _mix_rgb((r * 255, g * 255, b * 255), alpha)
    return "#%02x%02x%02x" % (r, g, b)

# ---------- star field: milky-way band, flares (90 stars for tkinter perf) ----------
STARS = []
for _ in range(90):
    in_band = rnd() < 0.42
    if in_band:
        t = rnd()
        x = 0.02 + 0.96 * t + rnd(-0.07, 0.07)
        y = 0.02 + 0.52 * t + rnd(-0.06, 0.06)
    else:
        x, y = rnd(0.01, 0.99), rnd(0.01, 0.62)
    b = (rnd() ** 1.6) * 0.9 + 0.10
    warm = rnd() < 0.22
    STARS.append({
        "x": min(0.995, max(0.005, x)) * W,
        "y": min(0.66, max(0.005, y)) * H,
        "b": b, "size": 0.7 + b * 2.3,
        "sp": rnd(0.4, 2.1), "ph": rnd(0, TAU),
        "hue": rnd(0.05, 0.14) if warm else rnd(0.56, 0.64),
        "sat": 0.55 if warm else 0.38,
        "flare": b > 0.62 and rnd() < 0.62,
    })

# ---------- moon: simple v1 crescent (real-phase version reverted) ----------

# ---------- show state ----------
parts = []          # sparks
rockets = []
flashes = []        # burst core flares
delayed = []        # secondary bursts (multi-break shells)
shooters = []       # meteors
MAX_PARTS = 320
tick_n = 0
next_shoot = time.time() + 5 + rnd(0, 6)
V_SPREAD = 0.62     # bloom-tightening velocity scale (plugin-calibrated)
events_seen = 0

bass_base = -60.0
treb_base = -60.0
last_launch = 0.0
last_crackle = 0.0

def add_part(x, y, vx, vy, hue, life, grav=0.05, drag=0.986, size=1.6,
             trail=3, spark=False, light=0.62, sat=1.0, split=False,
             split_hue=None):
    if len(parts) >= MAX_PARTS:
        parts.pop(0)
    parts.append({"x": x, "y": y, "vx": vx * V_SPREAD, "vy": vy * V_SPREAD,
                  "hue": hue, "age": 0, "life": life, "grav": grav,
                  "drag": drag, "size": size, "trail": max(2, trail),
                  "spark": spark, "light": light, "sat": sat,
                  "split": split, "split_done": False,
                  "split_hue": split_hue, "hist": deque(maxlen=max(2, trail))})

def choose_type(s):
    if s >= 0.84:
        return pick(("grand", "heart", "chrysanthemum", "crossette",
                     "double", "willow", "peony", "pistil"))
    if s >= 0.55:
        return pick(("peony", "chrysanthemum", "ring", "crossette", "spiral",
                     "palm", "glitter", "willow", "pistil", "strobe"))
    return pick(("peony", "ring", "glitter", "strobe"))

def burst(x, y, s, hue=None, force_type=None):
    """One shell burst — twelve types, ported from the plugin renderer."""
    typ = force_type or choose_type(s)
    fam = hue_set()
    h1 = hue if hue is not None else pick(fam)
    h2 = pick(fam)
    dens = density / 100.0          # (the plugin renderer uses x2.0 headroom;
                                    #  tkinter shares its budget with 12 shells)
    n = sp = 0

    if typ == "heart":
        n = int((54 + 40 * s) * dens)
        sp = (1.7 + 1.7 * s) * S
        hh = 0.955 if rnd() < 0.6 else 0.02
        for _ in range(n):
            ht = rnd(0, TAU)
            hx = 16 * math.sin(ht) ** 3
            hy = (13 * math.cos(ht) - 5 * math.cos(2 * ht)
                  - 2 * math.cos(3 * ht) - math.cos(4 * ht))
            add_part(x, y, hx / 16 * sp + rnd(-0.25, 0.25),
                     -hy / 16 * sp + rnd(-0.25, 0.25),
                     hh + rnd(-0.012, 0.012), int(46 + 22 * s + rnd(0, 10)),
                     grav=0.045, drag=0.986, trail=4,
                     size=1.5 + rnd(0, 0.6), light=0.63)
    elif typ == "chrysanthemum":
        n = int((44 + 50 * s) * dens)
        sp = (2.0 + 2.8 * s) * S
        ch = rnd(0.10, 0.14) if rnd() < 0.55 else h1
        for _ in range(n):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.78, 1.05)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     ch + rnd(-0.012, 0.012), int(78 + 38 * s + rnd(0, 16)),
                     grav=0.055, drag=0.988, trail=7,
                     size=1.4 + rnd(0, 0.5), light=0.64)
    elif typ == "willow":
        n = int((30 + 26 * s) * dens)
        sp = (1.5 + 1.5 * s) * S
        wh = rnd(0.09, 0.14)
        for _ in range(n):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.5, 1.0)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     wh + rnd(-0.008, 0.008), int(110 + 50 * s + rnd(0, 30)),
                     grav=0.10, drag=0.992, trail=9,
                     size=1.4 + rnd(0, 0.5), light=0.60)
    elif typ == "ring":
        two = rnd() < 0.42
        n = max(36, int((52 + 18 * s) * dens))
        sp = (3.9 + 3.0 * s) * S
        tilt, rot = rnd(0.32, 0.72), rnd(0, TAU)
        for k in range(n):
            ang = rot + (k / n) * TAU
            spd = sp * rnd(0.96, 1.04)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd * tilt,
                     h1 + rnd(-0.01, 0.01), int(48 + 20 * s + rnd(0, 6)),
                     grav=0.045, drag=0.987, trail=5, size=1.2,
                     light=0.62, spark=True)
            if two and k % 2 == 0:
                spd = sp * 0.62 * rnd(0.96, 1.04)
                add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd * tilt,
                         h2 + rnd(-0.01, 0.01), int(40 + 18 * s),
                         grav=0.045, drag=0.987, trail=4, size=1.1,
                         light=0.6, spark=True)
    elif typ == "crossette":
        n = int((26 + 12 * s) * dens)
        sp = (2.1 + 2.2 * s) * S
        for _ in range(n):
            ang = rnd(0, TAU)
            add_part(x, y, math.cos(ang) * sp * rnd(0.9, 1.05),
                     math.sin(ang) * sp * rnd(0.9, 1.05),
                     h1 + rnd(-0.012, 0.012), int(54 + 20 * s),
                     grav=0.05, drag=0.986, trail=4, size=1.5,
                     split=rnd() < 0.45, split_hue=h2)
    elif typ == "palm":
        n = int((12 + 8 * s) * dens) + 6
        sp = (2.6 + 2.6 * s) * S
        ph2 = rnd(0.09, 0.13) if rnd() < 0.5 else h1
        for _ in range(n):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.85, 1.05)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     ph2 + rnd(-0.008, 0.008), int(66 + 24 * s),
                     grav=0.09, drag=0.994, trail=10,
                     size=2.1 + rnd(0, 0.6), light=0.63)
    elif typ == "glitter":
        n = int((70 + 40 * s) * dens)
        sp = (1.1 + 2.2 * s) * S
        for _ in range(n):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.4, 1.0)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     (rnd(0.10, 0.16) if rnd() < 0.6 else h1) + rnd(-0.01, 0.01),
                     int(42 + 18 * s + rnd(0, 10)),
                     grav=0.025, drag=0.972, trail=1,
                     size=1.0 + rnd(0, 0.5), spark=True, light=0.70)
    elif typ == "spiral":
        arms = 3
        n = int((60 + 30 * s) * dens)
        for k in range(n):
            st = k / n
            ang = st * TAU * 2.2 + (k % arms) * TAU / arms
            spd = (0.6 + 2.2 * st) * S * (1 + 0.55 * s)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     h1 + rnd(-0.01, 0.01), int(50 + 24 * s),
                     grav=0.04, drag=0.986, trail=5, size=1.4, light=0.62)
    elif typ == "double":
        n2 = int((40 + 30 * s) * dens)
        sp = (2.2 + 2.6 * s) * S
        for _ in range(n2):
            ang = rnd(0, TAU)
            add_part(x, y, math.cos(ang) * sp * rnd(0.95, 1.05),
                     math.sin(ang) * sp * rnd(0.95, 1.05),
                     h1 + rnd(-0.01, 0.01), int(54 + 20 * s),
                     grav=0.05, drag=0.987, trail=4, size=1.5, light=0.62)
        for _ in range(int(n2 * 0.6)):
            ang = rnd(0, TAU)
            add_part(x, y, math.cos(ang) * sp * 0.55, math.sin(ang) * sp * 0.55,
                     h2 + rnd(-0.01, 0.01), int(46 + 18 * s),
                     grav=0.045, drag=0.985, trail=3, size=1.4, light=0.6)
    elif typ == "grand":
        n = int((72 + 48 * s) * dens)
        sp = (2.6 + 3.0 * s) * S
        hues = (h1, h2, pick(hue_set()))
        for k in range(n):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.8, 1.12)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     hues[k % 3] + rnd(-0.012, 0.012), int(58 + 28 * s),
                     grav=0.05, drag=0.988, trail=6,
                     size=1.6 + rnd(0, 0.6), light=0.64)
        for _ in range(int(44 * dens)):
            ang = rnd(0, TAU)
            add_part(x, y, math.cos(ang) * sp * 0.45, math.sin(ang) * sp * 0.45,
                     rnd(0.10, 0.16), int(64 + rnd(0, 20)),
                     grav=0.02, drag=0.972, trail=1, size=1.1,
                     spark=True, light=0.72)
        delayed.append({"at": tick_n + 16, "x": x + rnd(-26, 26),
                        "y": y + rnd(-18, 18), "s": s * 0.45, "hue": h2,
                        "type": "peony"})
    elif typ == "pistil":
        n = int((44 + 48 * s) * dens)
        sp = (2.4 + 2.7 * s) * S
        for _ in range(n):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.86, 1.06)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     h1 + rnd(-0.012, 0.012), int(46 + 26 * s + rnd(0, 12)),
                     grav=0.05, drag=0.986, trail=4,
                     size=1.7 + rnd(0, 0.6), light=0.60)
        np_ = int((22 + 18 * s) * dens)
        for _ in range(np_):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.18, 0.38)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     h2 + rnd(-0.01, 0.01), int(58 + 22 * s + rnd(0, 14)),
                     grav=0.03, drag=0.99, trail=2,
                     size=2.0 + rnd(0, 0.6), spark=True, light=0.72)
    elif typ == "strobe":
        n = int((46 + 32 * s) * dens)
        sp = (1.5 + 2.0 * s) * S
        for _ in range(n):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.55, 1.05)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     rnd(0.56, 0.63), int(40 + 16 * s + rnd(0, 10)),
                     grav=0.02, drag=0.972, trail=2,
                     size=1.2 + rnd(0, 0.4), spark=True, light=0.94, sat=0.16)
    else:  # peony
        n = int((46 + 62 * s) * dens)
        sp = (2.3 + 3.0 * s) * S
        bi = rnd() < 0.35
        for k in range(n):
            ang = rnd(0, TAU)
            spd = sp * rnd(0.86, 1.1)
            add_part(x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                     (h2 if (bi and (k & 1)) else h1) + rnd(-0.012, 0.012),
                     int(40 + 30 * s + rnd(0, 12)),
                     grav=0.048, drag=0.985, trail=3,
                     size=1.5 + rnd(0, 0.6), light=0.62)
    flashes.append({"x": x, "y": y, "r": (12 + 24 * s) * S, "hue": h1, "age": 0})

def launch(strength):
    fam = hue_set()
    rockets.append({"x": rnd(W * 0.18, W * 0.82), "y": H + 4,
                    "vx": rnd(-0.35, 0.35),
                    "vy": -(3.4 + 3.6 * strength) * S,
                    "ty": H * (0.14 + 0.30 * (1.0 - strength)),
                    "hue": pick(fam), "s": strength, "hist": deque(maxlen=6)})

def crackle(strength):
    cx, cy = rnd(W * 0.12, W * 0.88), rnd(H * 0.06, H * 0.40)
    n = int((16 + 26 * strength) * density / 100) + 8
    for _ in range(n):
        ang, spd = rnd(0, TAU), rnd(0.4, 1.6) * S
        add_part(cx, cy, math.cos(ang) * spd, math.sin(ang) * spd,
                 rnd(0.09, 0.16), int(rnd(12, 26)),
                 grav=0.03, drag=0.97, trail=2, size=1.1,
                 spark=True, light=0.70)
    flashes.append({"x": cx, "y": cy, "r": 8 * S, "hue": 0.12, "age": 0})

def dot(x, y, r, col):
    cv.create_oval(x - r, y - r, x + r, y + r, fill=col, width=0)

def tick():
    global bass_base, treb_base, last_launch, last_crackle, tick_n, next_shoot
    global events_seen
    now = time.time()
    now_ms = now * 1000.0
    tick_n += 1
    bass_db = treb_db = -70.0
    if a.demo:
        ph = now % 1.3
        bass_db = -28 + 20 * math.exp(-(ph / 0.09) ** 2)
        treb_db = -40 + 8 * math.exp(-((ph - 0.65) / 0.05) ** 2) \
                  + rng.uniform(-2, 2)
    else:
        d = latest_block()
        if d is not None:
            try:
                x = np.frombuffer(d, np.int16).astype(np.float32)
                x = x.reshape(-1, CH).mean(axis=1) / 32768.0
                if len(x) >= N:
                    mag = np.abs(np.fft.rfft(x[-N:] * win)) / (N / 4.0)
                    bass_db = band_db(mag, BASS)
                    treb_db = band_db(mag, TREB)
            except Exception:
                pass
    if bass_db > -69:
        bass_base += (bass_db - bass_base) * 0.020
        treb_base += (treb_db - treb_base) * 0.020

    # --- onsets ---
    if bass_db > bass_base + 5.5 and bass_db > -50 \
            and now - last_launch > 0.55 and len(rockets) < 8:
        strength = min(1.0, max(0.15, (bass_db - bass_base - 3.0) / 14.0))
        launch(strength)
        events_seen += 1
        last_launch = now
    elif now - last_launch > 4.5:          # keep the sky alive when quiet
        launch(0.30)
        last_launch = now
    if treb_db > treb_base + 7.0 and treb_db > -46 \
            and now - last_crackle > 0.45:
        crackle(min(1.0, max(0.3, (treb_db - treb_base - 4.0) / 12.0)))
        events_seen += 1
        last_crackle = now

    # --- physics ---
    for db in [d for d in delayed if d["at"] <= tick_n]:
        delayed.remove(db)
        burst(db["x"], db["y"], db["s"], db["hue"], db["type"])

    for r in list(rockets):
        r["hist"].append((r["x"], r["y"]))
        r["x"] += r["vx"]; r["y"] += r["vy"]; r["vy"] += 0.020 * S
        if r["y"] <= r["ty"] or r["vy"] > -0.8 * S:
            rockets.remove(r)
            burst(r["x"], r["y"], r["s"], r["hue"])

    for q in list(parts):
        q["hist"].append((q["x"], q["y"]))
        q["vy"] += q["grav"] * S
        q["vx"] *= q["drag"]; q["vy"] *= q["drag"]
        q["x"] += q["vx"]; q["y"] += q["vy"]
        q["age"] += 1
        if q["split"] and not q["split_done"] and q["age"] > q["life"] * 0.55:
            q["split_done"] = True
            for _ in range(4):
                aa, ss = rnd(0, TAU), rnd(0.5, 1.1) * S
                add_part(q["x"], q["y"], math.cos(aa) * ss, math.sin(aa) * ss,
                         q["split_hue"] or q["hue"], int(rnd(16, 26)),
                         grav=0.05, drag=0.97, trail=2, size=1.2,
                         spark=True, light=0.72)
        if q["age"] >= q["life"] or q["y"] > H + 30:
            parts.remove(q)

    for f in list(flashes):
        f["age"] += 1
        if f["age"] > 7:
            flashes.remove(f)

    for sh in list(shooters):
        sh["x"] += sh["vx"]; sh["y"] += sh["vy"]; sh["age"] += 1
        if sh["age"] >= sh["life"]:
            shooters.remove(sh)
    if now > next_shoot:
        shooters.append({"x": rnd(W * 0.30, W * 0.95), "y": rnd(H * 0.02, H * 0.16),
                         "vx": -rnd(11, 17) * S * 0.5, "vy": rnd(3.5, 6.0) * S * 0.5,
                         "age": 0, "life": 40, "hue": rnd(0.55, 0.64)})
        next_shoot = now + 8 + rnd(0, 10)

    # --- render: full redraw every frame ---
    cv.delete("all")

    # stars (twinkle + cross flares)
    tw = now * 2.1
    for st in STARS:
        b = st["b"] * (0.68 + 0.32 * math.sin(tw * st["sp"] + st["ph"]))
        base = 0.28 + 0.36 * b
        alpha = 0.42 + 0.58 * b
        dot(st["x"], st["y"], st["size"] * 1.1,
            cc(st["hue"], st["sat"], min(0.82, base + 0.12), alpha))
        if st["flare"]:
            fl = st["size"] * (4.5 + 4 * b)
            cl = cc(st["hue"], st["sat"] * 0.5, min(0.86, base + 0.20),
                    alpha * 0.5)
            cv.create_line(st["x"] - fl, st["y"], st["x"] + fl, st["y"],
                           fill=cl, width=1)
            cv.create_line(st["x"], st["y"] - fl, st["x"], st["y"] + fl,
                           fill=cl, width=1)

    # simple crescent moon (v1 style: two ovals — the real-phase rendering
    # needs alpha compositing tkinter doesn't have, and its fake-alpha glow
    # discs occluded the stars)
    mx, my = W * 0.13, H * 0.18
    dot(mx, my, 16, cc(0.13, 0.18, 0.86, 0.9))
    cv.create_oval(mx - 7, my - 21, mx + 25, my + 11, fill=BG, width=0)

    # meteors
    for sh in shooters:
        t = 1 - sh["age"] / sh["life"]
        for k in range(1, 6):
            tx = sh["x"] - sh["vx"] * k * 0.55
            ty = sh["y"] - sh["vy"] * k * 0.55
            dot(tx, ty, 1.4 - 0.2 * k,
                cc(sh["hue"], 0.35, 0.80, 0.75 * t * (1 - k / 6)))
        dot(sh["x"], sh["y"], 1.7, cc(sh["hue"], 0.25, 0.95, 0.9 * t))

    # rockets + trail
    for r in rockets:
        for i, (hx, hy) in enumerate(r["hist"]):
            t = (i + 1) / (len(r["hist"]) + 1)
            dot(hx, hy, 0.7 + 1.4 * t,
                cc(r["hue"], 0.45, 0.34 + 0.34 * t, 0.16 + 0.38 * t))
        dot(r["x"], r["y"], 2.6, cc(r["hue"], 0.5, 0.92, 0.9))
        dot(r["x"], r["y"], 1.1, "#fff8e8")

    # sparks: comet tail + flickering head + soft bloom
    for q in parts:
        fade = 1 - q["age"] / q["life"]
        if fade <= 0:
            continue
        flick = (0.35 + 0.65 * abs(math.sin(now * 40 + q["x"] * 0.09
                                                + q["y"] * 0.06))) \
                if q["spark"] else 1.0
        alpha = min(1.0, fade * 1.35) * flick
        size = q["size"] * 1.25 * (0.85 + 0.3 * fade)
        hist = list(q["hist"])
        for i in range(1, len(hist)):            # tail: thin -> bright
            t = i / len(hist)
            cv.create_line(hist[i - 1][0], hist[i - 1][1], hist[i][0], hist[i][1],
                           fill=cc(q["hue"], q["sat"] * 0.95,
                                   q["light"] * 0.70, alpha * 0.22 * t * t),
                           width=max(1, int(size * (0.4 + 0.8 * t))),
                           capstyle="round")
        if len(hist) > 1:
            cv.create_line(hist[-1][0], hist[-1][1], q["x"], q["y"],
                           fill=cc(q["hue"], q["sat"] * 0.95,
                                   q["light"] * 0.70, alpha * 0.45),
                           width=max(1, int(size * 0.9)), capstyle="round")
        dot(q["x"], q["y"], size,                # saturated body
            cc(q["hue"], q["sat"], q["light"], min(1.0, alpha)))
        if q["size"] > 1.05 and not q["spark"]:
            dot(q["x"], q["y"], max(0.5, size * 0.32),
                cc(q["hue"], q["sat"] * 0.3, 0.95, alpha * 0.6))

    # burst flares: filled radial bloom (ring read as a soap bubble)
    for f in flashes:
        t = 1 - f["age"] / 7
        if t <= 0:
            continue
        rr = f["r"] * (0.85 + f["age"] * 0.10)
        dot(f["x"], f["y"], rr, cc(f["hue"], 0.85, 0.70, 0.20 * t))
        dot(f["x"], f["y"], rr * 0.45, cc(f["hue"], 0.9, 0.80, 0.45 * t))

    # status badge
    cv.create_text(10, H - 12, anchor="w", font=("Consolas", 9),
                   fill=cc(0.12, 0.7, 0.75, 0.8),
                   text=f"FX {events_seen}")

    root.after(int(1000 / FPS), tick)

def on_close():
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
root.after(int(1000 / FPS), tick)
try:
    root.mainloop()
finally:
    if stream is not None:
        try:
            stream.stop_stream(); stream.close()
        except Exception:
            pass
        p.terminate()
    print("bye")
