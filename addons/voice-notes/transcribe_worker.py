#!/usr/bin/env python3
"""Runs inside the faster-whisper venv: transcribe_worker.py <wav> <model>. Prints one JSON line.

Gnotch recordings are 16 kHz mono PCM, so the WAV is decoded here with the stdlib
instead of faster-whisper's PyAV decoder (which breaks with some PyAV releases)."""
import json
import sys
import wave

import numpy as np
from faster_whisper import WhisperModel

wav, model = sys.argv[1], sys.argv[2]
with wave.open(wav, "rb") as w:
    if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
        audio = wav  # let faster-whisper decode other formats
    else:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
m = WhisperModel(model, device="cpu", compute_type="int8")
segs, info = m.transcribe(audio, vad_filter=True)
print(json.dumps({"text": " ".join(s.text.strip() for s in segs).strip(), "language": info.language}))
