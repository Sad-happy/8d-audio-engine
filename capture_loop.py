#!/usr/bin/env python3
"""Record the WASAPI loopback of an output device to a WAV file.

By default captures the loopback of the system default output device, which
is the easiest way to prove that audio is actually flowing somewhere.
"""
import argparse, sys, time, wave
import numpy as np
import pyaudiowpatch as pw


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="loopback.wav", help="output WAV path")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--substr", default="",
                    help="capture a loopback device whose name contains this "
                         "substring; empty = loopback of the default output")
    args = ap.parse_args()

    with pw.PyAudio() as p:
        if args.substr:
            dev = None
            for info in p.get_loopback_device_info_generator():
                if args.substr.lower() in info["name"].lower():
                    dev = info
                    break
            if dev is None:
                print(f"FATAL: no loopback device matching '{args.substr}'")
                sys.exit(2)
        else:
            dev = p.get_default_wasapi_loopback()
        rate = int(dev["defaultSampleRate"])
        ch = int(dev["maxInputChannels"])
        print(f"capturing [{dev['index']}] {dev['name']} ({rate} Hz, {ch} ch)")

        chunks = []
        total = [0]

        def cb(data, frame_count, info, flag):
            chunks.append(data)
            total[0] += frame_count
            return (None, pw.paContinue)

        s = p.open(format=pw.paInt16, channels=ch, rate=rate,
                   input=True, input_device_index=dev["index"],
                   stream_callback=cb)
        t0 = time.time()
        while time.time() - t0 < args.seconds:
            time.sleep(0.1)
        s.stop_stream(); s.close()

    raw = b"".join(chunks)
    with wave.open(args.out, "wb") as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(raw)
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    print(f"saved {args.out}: {total[0]/rate:.1f}s rms={rms:.4f} "
          f"peak={np.abs(x).max():.3f}")
    if rms < 1e-4:
        print("NOTE: capture is silent - is anything playing?")


if __name__ == "__main__":
    main()
