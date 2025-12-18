# core/dsp/ssb.py
from __future__ import annotations
import threading
import queue
import numpy as np
import scipy.signal as sig

from core.dsp.wbfm import hpf_1pole

class SSBStream:
    """
    SSB voz limpia (USB/LSB):
    - Resample IQ -> 48 kHz complejo
    - Selección de sideband (USB/LSB) por FFT en audio-rate
    - HPF 150 Hz + LPF 3 kHz
    """
    def __init__(self, iq_bytes_queue, freq_mhz: float, sideband: str = "USB", on_audio=None, input_fs: int = 2_400_000):
        self.q = iq_bytes_queue
        self.freq_mhz = float(freq_mhz)
        self.on_audio = on_audio
        self.sideband = (sideband or "USB").upper().strip()

        self.input_fs = int(input_fs)
        self.audio_fs = 48_000

        self.running = False
        self.th = None

        self.hpf_hz = 150.0
        self.lpf_hz = 3000.0
        self.drive = 1.2

        self._hpf_x1 = 0.0
        self._hpf_y1 = 0.0
        self._build_af()

    def _build_af(self):
        self._af_lpf_taps = sig.firwin(161, self.lpf_hz, fs=self.audio_fs)

    def start(self):
        if self.running:
            return
        self.running = True
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def stop(self):
        self.running = False

    def update_params(self, hpf_hz=None, lpf_hz=None, drive=None, sideband=None):
        if hpf_hz is not None:
            self.hpf_hz = float(hpf_hz)
        if lpf_hz is not None:
            self.lpf_hz = float(lpf_hz)
            self._build_af()
        if drive is not None:
            self.drive = float(drive)
        if sideband is not None:
            self.sideband = str(sideband).upper().strip()

    def _bytes_to_iq(self, raw: bytes) -> np.ndarray:
        data = np.frombuffer(raw, dtype=np.int8)
        if data.size < 2:
            return np.zeros(0, np.complex64)
        if data.size % 2:
            data = data[:-1]
        i = data[0::2].astype(np.float32, copy=False)
        q = data[1::2].astype(np.float32, copy=False)
        x = (i + 1j*q) / 128.0
        x = x - np.mean(x)
        return x.astype(np.complex64, copy=False)

    def _select_sideband(self, x: np.ndarray) -> np.ndarray:
        # x complejo a 48k. Elegimos USB (frecuencias +) o LSB (frecuencias -)
        N = int(x.size)
        if N < 512:
            return x

        X = np.fft.fftshift(np.fft.fft(x))
        mid = N // 2

        if self.sideband == "USB":
            X[:mid] = 0  # mata negativas, deja positivas
        else:  # LSB
            X[mid+1:] = 0  # mata positivas, deja negativas

        y = np.fft.ifft(np.fft.ifftshift(X))
        return y.astype(np.complex64, copy=False)

    def _loop(self):
        up, down = 1, int(self.input_fs / self.audio_fs)  # 2.4M -> 48k = /50

        while self.running:
            try:
                raw = self.q.get(timeout=0.2)
            except queue.Empty:
                continue

            iq = self._bytes_to_iq(raw)
            if iq.size < 256:
                continue

            x = sig.resample_poly(iq, up, down).astype(np.complex64, copy=False)

            # sideband select
            x = self._select_sideband(x)

            # audio real (producto detector simple)
            audio = np.real(x).astype(np.float32, copy=False)
            audio = audio - np.mean(audio)

            # HPF + LPF
            audio, self._hpf_x1, self._hpf_y1 = hpf_1pole(audio, self.audio_fs, self.hpf_hz, self._hpf_x1, self._hpf_y1)
            audio = sig.lfilter(self._af_lpf_taps, [1.0], audio).astype(np.float32, copy=False)

            audio = np.tanh(audio * float(self.drive)).astype(np.float32, copy=False)

            if self.on_audio:
                self.on_audio(audio, self.audio_fs)
