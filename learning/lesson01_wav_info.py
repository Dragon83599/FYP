from pathlib import Path
import wave

wav_path = Path("data/processed/synthetic-pcg/training_data/90001_AV.wav")

with wave.open(str(wav_path), "rb") as wav:
    channels = wav.getnchannels()
    sample_rate = wav.getframerate()
    sample_width_bytes = wav.getsampwidth()
    frames = wav.getnframes()

duration_sec = frames / sample_rate
duration_min = duration_sec / 60

print("channels =", channels)
print("sample_rate =", sample_rate)
print("sample_width_bytes =", sample_width_bytes)
print("frames =", frames)
print("duration_sec =", round(duration_sec, 3))
print("duration_min =", round(duration_min, 3))