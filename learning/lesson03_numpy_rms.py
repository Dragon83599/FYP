from pathlib import Path
import wave
import numpy as np

wav_path = Path("data/raw/circor/subset/training_data/9979_MV.wav")

with wave.open(str(wav_path), "rb") as wav:
    sample_rate = wav.getframerate()
    frames = wav.readframes(wav.getnframes())

samples = np.frombuffer(frames, dtype="<i2")
x = samples.astype(np.float32) / 32768.0
rms = np.sqrt(np.mean(x ** 2))
peak = np.max(np.abs(x))
print("peak =", peak)
print("sample_rate =", sample_rate)
print("shape =", samples.shape)
print("dtype =", samples.dtype)
print("min =", samples.min())
print("max =", samples.max())
print("mean =", samples.mean())
print("rms =", rms)