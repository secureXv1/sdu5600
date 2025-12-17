from __future__ import annotations
import wave
from pathlib import Path
import numpy as np

class WavRecorder:
    def __init__(self, path: str, sample_rate: int = 48000):
        self.path = Path(path)
        self.sample_rate = int(sample_rate)
        self._wf = None

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wf = wave.open(str(self.path), "wb")
        self._wf.setnchannels(1)
        self._wf.setsampwidth(2)  # int16
        self._wf.setframerate(self.sample_rate)

    def write_float32(self, audio: np.ndarray):
        if self._wf is None:
            return
        a = np.asarray(audio, dtype=np.float32)
        a = np.clip(a, -1.0, 1.0)
        i16 = (a * 32767.0).astype(np.int16)
        self._wf.writeframes(i16.tobytes())

    def stop(self):
        if self._wf:
            try:
                self._wf.close()
            except Exception:
                pass
            self._wf = None
