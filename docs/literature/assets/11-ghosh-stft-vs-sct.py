"""STFT vs SCT-like 时频对比演示（Ghosh 2020 §2.2 的直观示意）。

⚠️ 这不是论文 SCT 的复现：论文用 spline-kernel chirplet transform 自己估计
瞬时频率（IF）；这里直接用合成信号里已知的 chirp 频率做解调，只为把
"先把频率掰直、再变换"这个核心思想画出来。

输出：11-ghosh-stft-vs-sct.png
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

rng = np.random.default_rng(0)
fs, T = 4000, 3.0
t = np.arange(0, T, 1 / fs)

# --- 合成信号：上扫 chirp（当作杂音）+ 两个 S1/S2 爆发 + 噪声 ---
f_chirp = 120.0 + 300.0 * (t / T) ** 1.5
phase = 2 * np.pi * np.cumsum(f_chirp) / fs
env = 0.55 * np.exp(-((t - 1.5) / 0.55) ** 2) + 0.15

def burst(tc, freq, dur=0.08, amp=0.8):
    out = np.zeros_like(t)
    m = (t >= tc) & (t < tc + dur)
    seg = t[m] - tc
    out[m] = amp * np.sin(2 * np.pi * freq * seg) * np.exp(-seg / 0.02)
    return out

x = env * np.sin(phase) + burst(0.30, 90) + burst(0.62, 130) + burst(2.35, 85) + burst(2.68, 125)
x += 0.02 * rng.standard_normal(x.size)
x /= np.max(np.abs(x))

def stft(sig, win=256, hop=16, sigma=64):
    n = np.arange(win) - (win - 1) / 2
    w = np.exp(-0.5 * (n / sigma) ** 2)
    frames = range(0, sig.size - win, hop)
    S = np.stack([np.fft.fftshift(np.fft.fft(sig[i:i + win] * w)) for i in frames], axis=1)
    freqs = np.fft.fftshift(np.fft.fftfreq(win, 1 / fs))
    times = np.array([(i + win / 2) / fs for i in frames])
    return np.abs(S) ** 2, freqs, times

P, freqs, times = stft(x)

# --- 用已知 chirp 频率解调：把斜率"掰直" ---
corr = 2 * np.pi * np.cumsum(f_chirp) / fs
y = x * np.exp(-1j * corr)
Py, _, _ = stft(y)

def db(A):
    return 10 * np.log10(A / A.max() + 1e-12)

fig = plt.figure(figsize=(13.5, 4.0))
gs = fig.add_gridspec(1, 4, width_ratios=[1.0, 1.25, 1.25, 0.06], wspace=0.25)
ax0 = fig.add_subplot(gs[0, 0]); ax1 = fig.add_subplot(gs[0, 1]); ax2 = fig.add_subplot(gs[0, 2])
cax = fig.add_subplot(gs[0, 3])

ax0.plot(t, x, lw=0.4, color="k")
ax0.set_title("Synthetic PCG (chirp + S1/S2)", fontsize=12)
ax0.set_xlabel("Time (s)"); ax0.set_ylabel("Amplitude"); ax0.set_xlim(0, T)

im = ax1.imshow(db(P), origin="lower", aspect="auto", cmap="magma",
                extent=[times[0], times[-1], freqs[0], freqs[-1]], vmin=-45, vmax=0)
ax1.set_ylim(0, 600); ax1.set_xlim(0, T)
ax1.set_title("STFT: slanted ridge", fontsize=12)
ax1.set_xlabel("Time (s)"); ax1.set_ylabel("Frequency (Hz)")

ax2.imshow(db(Py), origin="lower", aspect="auto", cmap="magma",
           extent=[times[0], times[-1], freqs[0], freqs[-1]], vmin=-45, vmax=0)
ax2.set_ylim(-250, 250); ax2.set_xlim(0, T)
ax2.set_title("SCT-like: flat ridge at 0 Hz", fontsize=12)
ax2.set_xlabel("Time (s)"); ax2.set_ylabel("Frequency relative to local IF (Hz)")

fig.colorbar(im, cax=cax, label="dB")
fig.savefig("11-ghosh-stft-vs-sct.png", dpi=150, bbox_inches="tight")
print("saved")
