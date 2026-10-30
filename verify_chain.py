#!/usr/bin/env python3
"""End-to-end routing test: play a test tone to the default output device and
capture the loopback of another output device, asserting that audio flows.

With the 8D engine live and the default output set to the virtual cable this
proves the whole chain: player -> CABLE -> engine DSP -> headphones.

    python verify_chain.py --capture-substr 耳机
"""
import argparse, sys, threading, time
import numpy as np
import pyaudiowpatch as pw


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture-substr", required=True,
                    help="name substring of the output device whose loopback "
                         "to capture (e.g. your headphone device)")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--freq", type=float, default=440.0)
    args = ap.parse_args()

    with pw.PyAudio() as p:
        cap = None
        for info in p.get_loopback_device_info_generator():
            if args.capture_substr.lower() in info["name"].lower():
                cap = info
                break
        if cap is None:
            print(f"FATAL: no loopback device matching '{args.capture_substr}'")
            sys.exit(2)
        out = p.get_default_output_device_info()
        cap_rate = int(cap["defaultSampleRate"])
        out_rate = int(out["defaultSampleRate"])
        cap_ch = int(cap["maxInputChannels"])
        print(f"play  -> [{out['index']}] {out['name']} ({out_rate} Hz)")
        print(f"cap   <- [{cap['index']}] {cap['name']} ({cap_rate} Hz)")

        # blocking loopback capture on the main thread (callback-driven
        # loopback streams proved unreliable), tone render on a side thread
        chunks = []
        cs = p.open(format=pw.paInt16, channels=cap_ch, rate=cap_rate,
                    input=True, input_device_index=cap["index"],
                    frames_per_buffer=1024)

        def render():
            os_ = p.open(format=pw.paInt16, channels=2, rate=out_rate,
                         output=True, output_device_index=out["index"],
                         frames_per_buffer=1024)
            n = 0
            blocks = int(args.seconds * out_rate / 1024)
            for _ in range(blocks):
                t = np.arange(n, n + 1024) / out_rate
                n += 1024
                tone = 0.25 * np.sin(2 * np.pi * args.freq * t)
                tone *= 0.6 + 0.4 * np.sin(2 * np.pi * 1.5 * t)   # slow AM marker
                x = np.stack([tone] * 2, axis=1)
                os_.write((x * 32767).astype(np.int16).tobytes())
            os_.stop_stream(); os_.close()

        th = threading.Thread(target=render)
        th.start()
        while th.is_alive():
            chunks.append(cs.read(1024))
        th.join()
        cs.stop_stream(); cs.close()

    raw = b"".join(chunks)
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    print(f"captured {len(x)/cap_rate:.1f}s rms={rms:.4f} "
          f"peak={np.abs(x).max():.3f}")
    print("VERDICT:", "CHAIN OK - AUDIO FLOWING" if rms > 0.005 else "SILENT - audio is not flowing")


if __name__ == "__main__":
    main()
