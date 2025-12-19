# core/dsp/ssb.py
from __future__ import annotations

import threading
import queue
import numpy as np
import scipy.signal as sig

from core.dsp.wbfm import hpf_1pole


class SSBStream:
    """
    SSB voz limpia (USB / LSB) estilo SDR Console

    - Resample IQ -> 48 kHz complejo
    - Selección de sideband por FFT
    - HPF + LPF voz
    - Volumen real
    """

    def __init__(
        self,
        iq_bytes_queue,
        freq_mhz: float,
        sideband: str = "USB",
        on_audio=None,
        input_fs: int = 2_400_000
    ):
        self.q = iq_bytes_queue
        self.freq_mhz = float(freq_mhz)
        self.on_audio = on_audio

        self.sideband = (sideband or "USB").upper().strip()

        self.input_fs = int(input_fs)
        self.audio_fs = 48_000

        self.running = False
        self.th = None

        # ==========================
        # Parámetros DSP (voz)
        # ==========================
        self.hpf_hz = 150.0
        self.lpf_hz = 3000.0
        self.drive = 1.2

        # 🔊 volumen real (AudioEngine)
        self.volume = 1.0

        # estado filtros
        self._hpf_x1 = 0.0
        self._hpf_y1 = 0.0
        self._af_lpf_taps = None
        self._build_af()

    # -------------------------------------------------
    # DSP helpers
    # -------------------------------------------------
    def _build_af(self):
        """Reconstruye LPF de audio"""
        self._af_lpf_taps = sig.firwin(
            numtaps=161,
            cutoff=self.lpf_hz,
            fs=self.audio_fs
        )

    # -------------------------------------------------
    # API requerida por AudioEngine
    # -------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def stop(self):
        self.running = False

    def set_volume(self, v: float):
        self.volume = max(0.0, min(1.0, float(v)))

    def update_params(self, hpf_hz=None, lpf_hz=None, drive=None, sideband=None):
        rebuild = False

        if hpf_hz is not None and float(hpf_hz) != self.hpf_hz:
            self.hpf_hz = float(hpf_hz)

        if lpf_hz is not None and float(lpf_hz) != self.lpf_hz:
            self.lpf_hz = float(lpf_hz)
            rebuild = True

        if drive is not None:
            self.drive = float(drive)

        if sideband is not None:
            self.sideband = str(sideband).upper().strip()

        if rebuild:
            self._build_af()

    # -------------------------------------------------
    # Internos
    # -------------------------------------------------
    def _bytes_to_iq(self, raw: bytes) -> np.ndarray:
        data = np.frombuffer(raw, dtype=np.int8)
        if data.size < 2:
            return np.zeros(0, np.complex64)
        if data.size % 2:
            data = data[:-1]

        i = data[0::2].astype(np.float32, copy=False)
        q = data[1::2].astype(np.float32, copy=False)
        x = (i + 1j * q) / 128.0
        x = x - np.mean(x)
        return x.astype(np.complex64, copy=False)

    def _select_sideband(self, x: np.ndarray) -> np.ndarray:
        """
        Selección USB / LSB por FFT (audio-rate)
        """
        N = int(x.size)
        if N < 512:
            return x

        X = np.fft.fftshift(np.fft.fft(x))
        mid = N // 2

        if self.sideband == "USB":
            X[:mid] = 0        # mata negativas
        else:  # LSB
            X[mid + 1:] = 0   # mata positivas

        y = np.fft.ifft(np.fft.ifftshift(X))
        return y.astype(np.complex64, copy=False)

    # -------------------------------------------------
    # Loop principal
    # -------------------------------------------------
    def _loop(self):
        up = 1
        down = int(self.input_fs / self.audio_fs)  # ej. 2.4M -> 48k = 50

        while self.running:
            try:
                raw = self.q.get(timeout=0.2)
            except queue.Empty:
                continue

            iq = self._bytes_to_iq(raw)
            if iq.size < 256:
                continue

            # Resample a audio-rate complejo
            x = sig.resample_poly(iq, up, down).astype(np.complex64, copy=False)

            # Selección de banda lateral
            x = self._select_sideband(x)

            # Detector (producto)
            audio = np.real(x).astype(np.float32, copy=False)
            audio -= np.mean(audio)

            # HPF + LPF (voz)
            audio, self._hpf_x1, self._hpf_y1 = hpf_1pole(
                audio,
                self.audio_fs,
                self.hpf_hz,
                self._hpf_x1,
                self._hpf_y1
            )
            audio = sig.lfilter(self._af_lpf_taps, [1.0], audio).astype(np.float32, copy=False)

            # Drive + soft clip
            audio = np.tanh(audio * float(self.drive)).astype(np.float32, copy=False)

            # 🔊 volumen real
            audio *= self.volume

            if self.on_audio:
                self.on_audio(audio, self.audio_fs)
