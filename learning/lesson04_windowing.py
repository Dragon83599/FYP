from pathlib import Path
import wave
import numpy as np

wav_path = Path("data/raw/circor/subset/training_data/9979_MV.wav")

with wave.open(str(wav_path), "rb") as wav:
    sample_rate = wav.getframerate()
    samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    x = samples.astype(np.float32) / 32768.0

window_sec = 2.0
hop_sec = 1.0

window_samples = int(window_sec * sample_rate)
hop_samples = int(hop_sec * sample_rate)

starts = range(0, len(x) - window_samples + 1, hop_samples)

print("window_samples =", window_samples)
print("hop_samples =", hop_samples)
print("number of windows =", len(starts))

for index, start in enumerate(starts):
    window = x[start : start + window_samples]
    rms = np.sqrt(np.mean(window ** 2))
    print(index, start, window.shape, round(float(rms), 6))