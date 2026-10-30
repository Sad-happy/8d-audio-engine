# 8D Audio Engine（实时 8D 音频引擎）

Windows 全系统实时"8D"（双耳旋转）音效——就是音乐 App 里那个 8D 音效，但作用于
电脑播放的**所有声音**：网易云音乐、Spotify、YouTube、游戏，全都旋转。附带一个
离线渲染器，可以把同款效果直接烤进 WAV 文件。

需要**耳机**（双耳线索会被音箱串扰抵消）。

[English](README.md) | **中文文档**

## 工作原理

```
 各类播放器（网易云 / Spotify / YouTube / ...）
        │  默认输出
        ▼
 VB-Cable "CABLE Input"  ◄── switch_audio.ps1 -To cable
        │  （虚拟回环）
        ▼
 "CABLE Output" ──► realtime_8d.py ──► 耳机
                    （双耳 DSP）          ▲
                                          └── switch_audio.ps1 -To restore
                                              恢复你原来的输出设备
```

DSP 链路，每 2048 样本一块处理：

1. **临场感 EQ** —— 单声道和信号叠加 45 % 的 2.5–6.5 kHz，补偿下面空间化带来
   的高频损失（人声保持清透）。
2. **三频段分频** —— Butterworth 700 Hz 低通 / 2 kHz 高通，中间为 mid。
3. **ILD（双耳声级差）** —— 远耳按频段被"头影"遮蔽：低频 8 %、中频 30 %、
   高频 55 %。
4. **脑后变闷** —— 声源绕到脑后时高频闷化（−35 %）、低中频加暖（+10 %）：
   "从背后经过"的感觉。
5. **Woodworth ITD** —— 双耳时间差 `(r/c)(sin θ + θ)`，头半径 8.75 cm，用
   4 点拉格朗日分数延迟实现（≤ 0.8 ms）。
6. **螺旋轨迹** —— 方位角以 `--frot` 旋转（默认 0.14 Hz ≈ 7 秒一圈），同时
   俯仰角 ±25° 摆动、每圈两个周期——"从头顶掠过"的夸张档。
7. **AGC** —— 慢速 RMS 目标（0.11），响歌不削波。
8. **前馈式最大保持前视限幅器** —— 见下；保证峰值 ≤ 0.95 且增益曲线**连续**
   （鼓点无毛刺）。

### 限幅器原理（简版）

经典拖尾包络跟随器无法预知 4 样本瞬态——脉冲结束了攻击还没完成，等被延迟的
增益到达那个样本时包络又已经回落。解法是前馈：

- `E` = `|x|/CEIL` 的近瞬时跟随器（攻击 ≈ 0，释放 120 ms）
- `M` = `E` 在 240 样本前视窗上的滑动最大值 → 精确的"未来需求"，且恰好对
  正在发射的音频是最终值
- `M2` = `M` 的 1 ms / 2 ms 跟随器 → 平滑连续的增益曲线

`M` 总是在对应峰值到达延迟输出前 ≥ 5 ms 跳起，1 ms 攻击有充足时间就位。
单元测试 [test_limiter.py](test_limiter.py)（`python test_limiter.py`，无需
音频设备）断言：峰值不超天花板、块边界无爆点、即使病态满幅矩形脉冲下每样本
增益步进 < 0.012。

## 安装

1. **Python 3.9+**，带 `pip`。
2. **VB-Cable**（免费虚拟音频设备）：从
   [vb-audio.com/Cable](https://vb-audio.com/Cable/) 下载，**以管理员身份**
   运行 `VBCABLE_Setup_x64.exe`，点 *Install Driver*。无需重启，装完立刻出现
   `CABLE Input`（输出侧）和 `CABLE Output`（采集侧）。
3. `pip install -r requirements.txt`（numpy、scipy、PyAudioWPatch）。

## 快速开始

```powershell
# 1) 把系统声音导进虚拟声卡（会记住你之前的输出设备）
powershell -ExecutionPolicy Bypass -File switch_audio.ps1 -To cable

# 2) 启动引擎（读声卡回环，播到你的默认输出 = 耳机）
python realtime_8d.py

# ... 享受。停止并恢复原输出设备：
powershell -ExecutionPolicy Bypass -File off.ps1
```

重启电脑后重跑第 1–2 步（路由和引擎都不会自启）。

## 脚本一览

| 文件 | 作用 |
|---|---|
| `realtime_8d.py` | 实时引擎（参数见下） |
| `make_8d.py` | 离线渲染 WAV → WAV（同款模型，无需音频设备） |
| `switch_audio.ps1` | 切默认输出：`list` / `cable` / `restore` / `name` |
| `stop_8d.ps1` | 停引擎（创建 `stop.flag`，看门狗 ≤ 1 秒退出） |
| `off.ps1` | 停引擎**并**恢复之前的输出设备 |
| `capture_loop.py` | 录任意输出设备的回环到 WAV（排障用） |
| `verify_chain.py` | 向默认输出播测试音、抓另一设备的回环，断言音频在流动 |
| `test_limiter.py` | 限幅器离线单元测试 |

### realtime_8d.py 参数

```
--in-substr STR     采集设备名子串                  （默认 "CABLE Output"）
--out-substr STR    输出设备名子串                  （默认系统默认输出）
--rate N            采样率                          （默认 48000）
--block N           每缓冲帧数                      （默认 2048）
--frot F            旋转频率 Hz                     （默认 0.14 ≈ 7 秒/圈）
--elev-deg F        纵向摆动幅度（度）              （默认 25）
--presence F        2.5-6.5 kHz 临场感 EQ 比例      （默认 0.45）
--target-rms F      AGC 目标 RMS                    （默认 0.11）
```

例：不跟随系统默认输出、固定到指定设备——
`python realtime_8d.py --out-substr Realtek`（设备名用
`switch_audio.ps1 -To list` 查看）。

### 离线渲染

```powershell
python make_8d.py --in song.wav --out song-8d.wav --frot 0.1
```

## 验证链路

```powershell
python verify_chain.py --capture-substr Realtek
# -> VERDICT: CHAIN OK - AUDIO FLOWING
```

`Realtek` 换成你耳机设备名里的子串（`switch_audio.ps1 -To list` 列出全部）。

## 常见问题

- **`OpenBLAS error: Memory allocation still failed after 10 retries`** ——
  引擎已替你设 `OPENBLAS_NUM_THREADS=1`。如果你把代码 import 进自己的工程，
  请自己设这个环境变量（必须在 numpy 加载前设）。
- **没声音** —— 先跑 `verify_chain.py`；SILENT 的话检查默认输出是不是声卡
  （`switch_audio.ps1 -To cable`）以及引擎是否在跑。引擎启动时会打印
  `in=… out=…`。
- **引擎退出报 `no input device matching 'CABLE Output'`** —— VB-Cable 没装；
  或声卡不是默认输出（采集设备永远存在，但没东西往里放就没声音）。
- **设备选错** —— 传 `--in-substr` / `--out-substr`，或用
  `switch_audio.ps1 -To list` 查设备名。
- **低配机卡顿** —— `--block` 提到 4096（延迟变大，CPU 压力变小）。
- **PowerShell 拒绝运行脚本** —— 加
  `powershell -ExecutionPolicy Bypass -File 脚本.ps1`。

## 局限

- 仅 Windows（WASAPI 回环 + IPolicyConfig COM 切设备）。
- 仅立体声流；引擎按 48 kHz 开流（VB-Cable 原生采样率）。
- 总延迟约 50 ms（2048 块 @ 48 kHz ≈ 43 ms + 限幅器 5 ms 前视）；安静环境下
  可调低 `--block`。
- 双耳线索需要耳机；音箱上效果基本抵消。

## 许可

[MIT](LICENSE) — © 2025 Sad-happy
