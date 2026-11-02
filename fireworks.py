#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8D Fireworks — an audio-driven fireworks show (tkinter + WASAPI loopback).

Bass onsets launch rockets that burst into sparks; a really strong kick
bursts into a heart. Treble spikes crackle white. Stars and a crescent
moon fill the sky. With the 8D engine live this reacts to the final
post-8D sound (capture the headphone loopback).

Rendering model: the canvas is fully redrawn every frame (delete + draw),
so there is no item churn / z-order burial; spark trails are explicit
fading ghost dots taken from each spark's position history.

Usage:
  python fireworks.py                          # default output loopback
  python fireworks.py --capture-substr CABLE   # pre-engine signal
  python fireworks.py --demo                   # synthetic bass pulses,
                                               # no audio needed
  python fireworks.py --top                    # keep window on top

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

ap = argparse.ArgumentParser()
ap.add_argument("--capture-substr", default="",
                help="empty = loopback of the system default output device")
ap.add_argument("--width", type=int, default=586)
ap.add_argument("--height", type=int, default=340)
ap.add_argument("--top", action="store_true", help="keep window on top")
ap.add_argument("--demo", action="store_true",
                help="ignore audio, fire synthetic pulses (testing)")
a = ap.parse_args()

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
BG = "#070a14"

rng = random.Random(20261102)

# star field: (x, y, base brightness 0..1, twinkle speed, phase)
STARS = [(rng.uniform(6, W - 6), rng.uniform(4, H * 0.62),
          rng.uniform(0.10, 0.38), rng.uniform(0.6, 1.8), rng.uniform(0, 6.3))
         for _ in range(46)]

# hue palette: mostly gold, some rose / cyan / violet
PALETTE = (0.12, 0.12, 0.12, 0.12, 0.955, 0.955, 0.52, 0.52, 0.76, 0.15)

def hexcol(hue, sat, light):
    r, g, b = colorsys.hls_to_rgb(hue % 1.0, min(1.0, max(0.0, light)), sat)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))

# particle: dict(x, y, vx, vy, hue, born, life, hist=deque[(x,y), ...])
parts = []
rockets = []            # dict(x, y, vx, vy, ty, hue, s, hist)
bass_base = -60.0
treb_base = -60.0
last_launch = 0.0
last_crackle = 0.0

MAX_PARTS = 240
TRAIL = 3               # ghost dots per spark

def spawn(x, y, vx, vy, hue, life):
    if len(parts) >= MAX_PARTS:
        parts.pop(0)
    parts.append({"x": x, "y": y, "vx": vx, "vy": vy, "hue": hue,
                  "born": 0, "life": life, "hist": deque(maxlen=TRAIL)})

def explode(x, y, strength, hue):
    """Radial burst; a strong kick turns into a heart of sparks."""
    heart = strength > 0.82
    n = int(40 + 66 * strength)
    speed = (2.0 + 2.7 * strength) * (W / 586.0)
    for _ in range(n):
        life = 42 + int(26 * strength) + rng.randint(0, 10)
        if heart:
            t = rng.uniform(0, 2 * math.pi)
            hx = 16 * math.sin(t) ** 3
            hy = 13 * math.cos(t) - 5 * math.cos(2 * t) \
                 - 2 * math.cos(3 * t) - math.cos(4 * t)
            s = speed * 0.62
            spawn(x, y, hx / 16 * s + rng.uniform(-.3, .3),
                  -hy / 16 * s + rng.uniform(-.3, .3),
                  0.955 + rng.uniform(-0.02, 0.02), life)
        else:
            ang = rng.uniform(0, 2 * math.pi)
            sp = speed * (1.0 if rng.random() < 0.7 else 0.55)
            spawn(x, y, math.cos(ang) * sp, math.sin(ang) * sp,
                  hue + rng.uniform(-0.015, 0.015), life)
    r = 14 + 22 * strength                       # one-frame flash
    cv.create_oval(x - r, y - r, x + r, y + r, fill="",
                   outline=hexcol(hue, 0.35, 0.85), width=2)

def launch(strength):
    hue = rng.choice(PALETTE)
    rockets.append({"x": rng.uniform(W * 0.12, W * 0.88), "y": H + 4,
                    "vx": rng.uniform(-0.5, 0.5),
                    "vy": -(3.4 + 3.8 * strength),
                    "ty": H * (0.16 + 0.30 * (1.0 - strength)),
                    "hue": hue, "s": strength, "hist": deque(maxlen=TRAIL)})

def draw_dot(x, y, r, col):
    cv.create_oval(x - r, y - r, x + r, y + r, fill=col, width=0)

def tick():
    global bass_base, treb_base, last_launch, last_crackle
    now = time.time()
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
    bass_base += (bass_db - bass_base) * 0.020
    treb_base += (treb_db - treb_base) * 0.020

    # --- launch logic ---
    if bass_db > bass_base + 5.5 and bass_db > -50 \
            and now - last_launch > 0.55 and len(rockets) < 6:
        strength = min(1.0, max(0.15, (bass_db - bass_base - 3.0) / 14.0))
        launch(strength)
        last_launch = now
    elif now - last_launch > 4.5:          # keep the sky alive when quiet
        launch(0.30)
        last_launch = now

    if treb_db > treb_base + 7.0 and treb_db > -46 \
            and now - last_crackle > 0.45:
        last_crackle = now
        cx, cy = rng.uniform(W * 0.15, W * 0.85), rng.uniform(H * 0.08, H * 0.42)
        for _ in range(rng.randint(8, 14)):
            ang = rng.uniform(0, 2 * math.pi)
            sp = rng.uniform(0.5, 1.4)
            spawn(cx, cy, math.cos(ang) * sp, math.sin(ang) * sp,
                  0.15, rng.randint(10, 16))

    # --- physics ---
    for r in list(rockets):
        r["hist"].append((r["x"], r["y"]))
        r["x"] += r["vx"]; r["y"] += r["vy"]; r["vy"] += 0.020
        if r["y"] <= r["ty"] or r["vy"] > -0.8:
            rockets.remove(r)
            explode(r["x"], r["y"], r["s"], r["hue"])

    for q in list(parts):
        q["hist"].append((q["x"], q["y"]))
        q["vy"] += 0.055
        q["vx"] *= 0.986; q["vy"] *= 0.986
        q["x"] += q["vx"]; q["y"] += q["vy"]
        q["born"] += 1
        if q["born"] >= q["life"] or q["y"] > H + 20:
            parts.remove(q)

    # --- render: full redraw, bounded item count ---
    cv.delete("all")
    tw = now * 2.2
    for (sx, sy, sb, sp, pp) in STARS:
        b = sb * (0.72 + 0.28 * math.sin(tw * sp + pp))
        draw_dot(sx, sy, 0.8, hexcol(0.60, 0.25, 0.16 + b * 0.5))
    mx, my = W * 0.13, H * 0.18               # crescent moon
    cv.create_oval(mx - 16, my - 16, mx + 16, my + 16,
                   fill=hexcol(0.13, 0.18, 0.86), width=0)
    cv.create_oval(mx - 7, my - 21, mx + 25, my + 11, fill=BG, width=0)

    for r in rockets:                          # rocket + short trail
        col = hexcol(r["hue"], 0.3, 0.9)
        for i, (hx, hy) in enumerate(r["hist"]):
            draw_dot(hx, hy, 1.2 - 0.3 * i, hexcol(r["hue"], 0.3,
                                                   0.5 - 0.12 * i))
        draw_dot(r["x"], r["y"], 1.8, col)

    for q in parts:                            # sparks: head + ghost trail
        age = q["born"]
        fade = 1.0 - age / q["life"]
        flick = rng.uniform(0.72, 1.0)
        light = (0.80 * fade + 0.22) * flick
        sat2 = 0.25 + 0.65 * (1.0 - fade)
        for i, (hx, hy) in enumerate(q["hist"]):
            if i:
                draw_dot(hx, hy, 1.6 - 0.4 * i,
                         hexcol(q["hue"], sat2, light * (0.55 - 0.18 * i)))
        draw_dot(q["x"], q["y"], 1.6, hexcol(q["hue"], sat2, light))

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
