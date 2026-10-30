# 8D Audio Engine

System-wide realtime "8D" (binaural rotation) audio for Windows — the same
effect music players call *8D audio*, applied to **everything** your PC plays:
Spotify, NetEase Cloud Music, YouTube, anything. Plus a small offline renderer
that applies the same effect to a WAV file.

Headphones required (binaural cues are destroyed by speaker crosstalk).

**English** | [中文文档](README.zh-CN.md)

## How it works

```
 players (Spotify / NetEase / YouTube / ...)
        │  default output
        ▼
 VB-Cable "CABLE Input"  ◄── switch_audio.ps1 -To cable
        │  (virtual loopback)
        ▼
 "CABLE Output" ──► realtime_8d.py ──► your headphones
                    (binaural DSP)        ▲
                                          └── switch_audio.ps1 -To restore
                                              puts your normal device back
```

The DSP chain, applied per 2048-sample block:

1. **Presence EQ** — +45 % of 2.5–6.5 kHz on the mono sum, compensating the
   high-frequency loss the spatialization adds below (keeps vocals clear).
2. **3-band split** — Butterworth lowpass 700 Hz / highpass 2 kHz, mid = rest.
3. **ILD (interaural level difference)** — the far ear is shadowed per band:
   low 8 %, mid 30 %, high 55 %.
4. **Head shadow** — when the source is behind the head, highs are dulled
   (−35 %) and low-mids warmed (+10 %): the "behind you" sensation.
5. **Woodworth ITD** — interaural time delay `(r/c)(sin θ + θ)` with
   r = 8.75 cm, rendered with a 4-point Lagrange fractional delay (≤ 0.8 ms).
6. **Helix trajectory** — azimuth rotates at `--frot` (default 0.14 Hz ≈ 7 s
   per revolution) while elevation wobbles ±25°, two cycles per revolution —
   the "over your head" sweep aggressive 8D modes have.
7. **AGC** — slow RMS targeting (0.11) so loud tracks don't clip.
8. **Feed-forward max-hold lookahead limiter** — see below; guarantees
   ≤ 0.95 peak with a *continuous* gain curve (no crackle on drum hits).

### The limiter, briefly

A classic trailing envelope follower cannot anticipate a 4-sample transient —
the attack never completes before the pulse ends, and the delayed gain has
already released when it finally reaches that sample. The fix is feed-forward:

- `E` = near-instant follower of `|x|/CEIL` (attack ≈ 0, release 120 ms)
- `M` = sliding max of `E` over a 240-sample lookahead window → the *exact*
  future requirement, final precisely for the audio being emitted
- `M2` = 1 ms / 2 ms follower on `M` → smooth, continuous gain curve

`M` always jumps ≥ 5 ms before the corresponding peak reaches the delayed
output, so the 1 ms attack is fully settled in time. Unit test:
[test_limiter.py](test_limiter.py) (`python test_limiter.py`, no devices
needed) asserts peak ≤ ceiling, no block-boundary clicks, and per-sample gain
steps below 0.012 even with pathological full-scale rectangular pulses.

## Install

1. **Python 3.9+** with `pip`.
2. **VB-Cable** (free virtual audio device): download from
   [vb-audio.com/Cable](https://vb-audio.com/Cable/), run the included
   `VBCABLE_Setup_x64.exe` **as administrator**, click *Install Driver*.
   No reboot needed; `CABLE Input` (output) and `CABLE Output` (capture)
   appear immediately.
3. `pip install -r requirements.txt` (numpy, scipy, PyAudioWPatch).

## Quickstart

```powershell
# 1) route system audio into the cable (remembers your previous device)
powershell -ExecutionPolicy Bypass -File switch_audio.ps1 -To cable

# 2) start the engine (reads the cable, plays to your default output = headphones)
python realtime_8d.py

# ... enjoy. to stop and restore your normal output:
powershell -ExecutionPolicy Bypass -File off.ps1
```

After a reboot, repeat steps 1–2 (the routing and the engine do not persist).

## Scripts

| file | what it does |
|---|---|
| `realtime_8d.py` | the realtime engine (see CLI below) |
| `make_8d.py` | offline WAV → WAV renderer (same model, no devices needed) |
| `switch_audio.ps1` | switch default output: `list` / `cable` / `restore` / `name` |
| `stop_8d.ps1` | stop the engine (creates `stop.flag`; watchdog exits ≤ 1 s) |
| `off.ps1` | stop the engine **and** restore your previous output device |
| `capture_loop.py` | record any output device's loopback to WAV (debug) |
| `verify_chain.py` | play a tone to the default output, capture another device's loopback, assert audio flows |
| `test_limiter.py` | offline unit test of the limiter |

### realtime_8d.py CLI

```
--in-substr STR     capture device substring          (default: "CABLE Output")
--out-substr STR    output device substring           (default: system default output)
--rate N            sample rate                       (default: 48000)
--block N           frames per buffer                 (default: 2048)
--frot F            rotation Hz                       (default: 0.14 ≈ 7 s/rev)
--elev-deg F        vertical wobble amplitude         (default: 25)
--presence F        2.5-6.5 kHz EQ mix                (default: 0.45)
--target-rms F      AGC target RMS                    (default: 0.11)
```

Example: fixed output device instead of the system default —
`python realtime_8d.py --out-substr Realtek` (run `switch_audio.ps1 -To list`
to see device names).

### Offline renderer

```powershell
python make_8d.py --in song.wav --out song-8d.wav --frot 0.1
```

## Verify the chain

```powershell
python verify_chain.py --capture-substr Realtek
# -> VERDICT: CHAIN OK - AUDIO FLOWING
```

Replace `Realtek` with a substring of your actual headphone device name
(`switch_audio.ps1 -To list` shows all names).

## Troubleshooting

- **`OpenBLAS error: Memory allocation still failed after 10 retries`** — the
  engine sets `OPENBLAS_NUM_THREADS=1` for you. If you import it into your own
  code, set that environment variable yourself (it must be set before numpy
  loads).
- **Silent output** — run `verify_chain.py`; if SILENT, check that the default
  output is the cable (`switch_audio.ps1 -To cable`) and that the engine is
  running. The engine logs `in=… out=…` at startup.
- **Engine exits with `no input device matching 'CABLE Output'`** — VB-Cable
  is not installed, or the cable is not the default output (the capture device
  exists regardless, but nothing will flow unless something plays into it).
- **Wrong device picked** — pass `--in-substr` / `--out-substr`, or list
  devices with `switch_audio.ps1 -To list`.
- **Latency / crackle on weak machines** — raise `--block` to 4096 (more
  latency, less CPU pressure).
- **PowerShell won't run the scripts** — use
  `powershell -ExecutionPolicy Bypass -File script.ps1`.

## Limitations

- Windows only (WASAPI loopback + IPolicyConfig COM for device switching).
- Stereo streams only; the engine re-opens at 48 kHz (VB-Cable's native rate).
- ~50 ms total latency (2048-sample block @ 48 kHz ≈ 43 ms + 5 ms limiter
  lookahead); lower `--block` for less latency on a quiet machine.
- Binaural cues need headphones; on speakers the effect largely collapses.

## License

[MIT](LICENSE) — © 2025 Sad-happy
