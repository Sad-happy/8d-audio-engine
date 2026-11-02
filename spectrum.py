# -*- coding: utf-8 -*-
"""
8D Spectrum — realtime audio spectrum visualizer (WASAPI loopback -> FFT -> tkinter)
 Watches what your output device is actually playing, in 56 log-spaced bars.

Usage:
  python spectrum.py                        # default output device (with the
                                            # 8D engine live this = headphones
                                            # = the final post-8D sound)
  python spectrum.py --device-substr CABLE  # pre-engine signal (CABLE Input),
                                            # handy for before/after comparison
  python spectrum.py --top                  # keep window always on top

Deps: numpy, PyAudioWPatch (pip install PyAudioWPatch); tkinter ships with
Python on Windows.
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import sys, threading, argparse
from collections import deque
import numpy as np
import pyaudiowpatch as pw

N = 2048          # FFT window size (samples)
FPS = 30          # redraw rate

ap = argparse.ArgumentParser()
ap.add_argument("--device-substr", default="",
                help="empty = loopback of the system default output device; "
                     "or a name substring, e.g. CABLE")
ap.add_argument("--bars", type=int, default=56)
ap.add_argument("--width", type=int, default=560)
ap.add_argument("--height", type=int, default=240)
ap.add_argument("--top", action="store_true", help="keep window on top")
a = ap.parse_args()

# ---------- capture (callback thread only appends byte chunks) ----------
frames = deque(maxlen=64)
lock = threading.Lock()

def on_data(data, fc, info, flags):
    with lock:
        frames.append(bytes(data))
    return (None, pw.paContinue)

p = pw.PyAudio()
if a.device_substr:
    dev = None
    for info in p.get_loopback_device_info_generator():
        if a.device_substr.lower() in info["name"].lower():
            dev = info
            break
    if dev is None:
        print("no loopback device matches:", a.device_substr)
        sys.exit(2)
else:
    dev = p.get_default_wasapi_loopback()

SR = int(dev["defaultSampleRate"])
CH = int(dev["maxInputChannels"])
print(f"capturing [{dev['index']}] {dev['name']}  {SR}Hz {CH}ch")
stream = p.open(format=pw.paInt16, channels=CH, rate=SR, input=True,
                input_device_index=dev["index"], frames_per_buffer=1024,
                stream_callback=on_data)

# ---------- spectrum setup (log-spaced bins + high-frequency lift) ----------
freqs = np.fft.rfftfreq(N, 1.0 / SR)
edges = np.geomspace(40.0, min(16000.0, SR * 0.475), a.bars + 1)
idx = np.searchsorted(freqs, edges)
idx = np.maximum.accumulate(idx)
win = np.hanning(N).astype(np.float32)
lvl = np.zeros(a.bars, dtype=np.float32)
peak = np.zeros(a.bars, dtype=np.float32)

def latest_block():
    with lock:
        if not frames:
            return None
        data = b"".join(frames)
        frames.clear()
    return data

# ---------- tkinter UI ----------
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
import tkinter as tk

root = tk.Tk()
root.title("8D Spectrum")
root.geometry(f"{a.width}x{a.height}-60-110")
root.configure(bg="#0b0e16")
root.resizable(False, False)
if a.top:
    root.attributes("-topmost", True)

c0, c1, c2 = (56, 214, 255), (150, 110, 255), (255, 70, 190)
def lerp(cA, cB, t):
    return tuple(int(cA[j] + (cB[j] - cA[j]) * t) for j in range(3))
cols = []
for i in range(a.bars):
    t = i / max(1, a.bars - 1)
    c = lerp(c0, c1, t / 0.5) if t < 0.5 else lerp(c1, c2, (t - 0.5) / 0.5)
    cols.append("#%02x%02x%02x" % c)

cv = tk.Canvas(root, width=a.width, height=a.height, bg="#0b0e16",
               highlightthickness=0)
cv.pack()

def draw():
    cv.delete("all")
    gap = a.width / a.bars
    bw = gap * 0.70
    base = a.height - 12
    amp = a.height - 34
    cv.create_text(8, 7, anchor="nw", text="8D SPECTRUM",
                   fill="#39415a", font=("Consolas", 9))
    for i in range(a.bars):
        x0 = i * gap + (gap - bw) / 2
        h = max(1.0, lvl[i] * amp)
        cv.create_rectangle(x0, base - h, x0 + bw, base,
                            fill=cols[i], width=0)
        y2 = base - peak[i] * amp
        cv.create_rectangle(x0, y2 - 2, x0 + bw, y2,
                            fill="#e8ecff", width=0)

def tick():
    d = latest_block()
    if d is not None:
        try:
            x = np.frombuffer(d, np.int16).astype(np.float32)
            x = x.reshape(-1, CH).mean(axis=1) / 32768.0
            if len(x) >= N:
                seg = x[-N:]
                mag = np.abs(np.fft.rfft(seg * win)) / (N / 4.0)
                for i in range(a.bars):
                    s, e = int(idx[i]), int(idx[i + 1])
                    if e <= s:
                        e = s + 1
                    v = float(mag[s:e].max()) if e <= len(mag) else 0.0
                    # 20*log10 amplitude -> dB, +9 dB tilt across bars so the
                    # naturally weaker high band stays visible
                    db = 20.0 * np.log10(v + 1e-7) + 9.0 * i / a.bars
                    new = min(1.0, max(0.0, (db + 62.0) / 56.0))
                    if new > lvl[i]:            # fast attack
                        lvl[i] = new * 0.55 + lvl[i] * 0.45
                    else:                       # slow decay
                        lvl[i] *= 0.78
        except Exception:
            pass
    np.maximum(peak - 0.012, lvl, out=peak)     # falling peak caps
    draw()
    root.after(int(1000 / FPS), tick)

def on_close():
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
root.after(int(1000 / FPS), tick)
try:
    root.mainloop()
finally:
    try:
        stream.stop_stream(); stream.close()
    except Exception:
        pass
    p.terminate()
    print("bye")
