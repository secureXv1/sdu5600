# drivers/hackrf_driver.py
# HackRF REAL en Windows usando hackrf_transfer.exe + FFT en Python (sin sweep)

from __future__ import annotations

import math
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .base import RadioDriver


class HackRFDriver(RadioDriver):
    """
    Driver HackRF usando hackrf_transfer.exe como backend.

    Ventajas:
    - NO depende de hackrf_sweep (tu firmware actual no soporta sweep).
    - Compatible con el flujo que usa SDR Console (streaming IQ).
    """

    def __init__(self, config=None):
        super().__init__(config or {})

        self.center_freq_hz = int(self.config.get("center_freq_hz", 435_000_000))
        self.span_hz = int(self.config.get("span_hz", 20_000_000))  # solo para eje de freq / visual
        self.sample_rate = int(self.config.get("sample_rate", 20_000_000))
        self.fft_size = int(self.config.get("fft_size", 4096))

        self.lna_gain = int(self.config.get("lna_gain", 16))
        self.vga_gain = int(self.config.get("vga_gain", 16))
        self.amp = int(self.config.get("amp", 0))

        # hackrf_transfer
        self.hackrf_transfer_path = shutil.which("hackrf_transfer") or r"C:\Program Files\PothosSDR\bin\hackrf_transfer.exe"

        self.last_spectrum = ([], [])
        self.simulator = False

        if not Path(self.hackrf_transfer_path).exists():
            print("[HackRFDriver] ADVERTENCIA: hackrf_transfer.exe no encontrado. Modo SIMULADOR.")
            self.simulator = True

    # -----------------------------------------------------------------------
    def connect(self):
        if self.connected:
            return True

        if self.simulator:
            self.connected = True
            return True

        # Probar que el device responda con una captura mini
        try:
            _ = self._capture_iq(num_samples=32768)
            self.connected = True
            return True
        except Exception as e:
            print(f"[HackRFDriver] ERROR conectando con hackrf_transfer: {e}. Modo SIMULADOR.")
            self.simulator = True
            self.connected = True
            return True

    def disconnect(self):
        self.connected = False
        return True

    # -----------------------------------------------------------------------
    def set_center_freq(self, freq_hz):
        self.center_freq_hz = int(freq_hz)

    def set_span(self, span_hz):
        self.span_hz = int(span_hz)

    def set_rf_gain(self, lna_db=None, vga_db=None, amp=None):
        if lna_db is not None:
            self.lna_gain = int(lna_db)
        if vga_db is not None:
            self.vga_gain = int(vga_db)
        if amp is not None:
            self.amp = 1 if amp else 0

    # -----------------------------------------------------------------------
    def get_spectrum(self):
        """
        Devuelve (freqs_hz, powers_db) calculado desde IQ capturado.
        """
        if self.simulator:
            return self._fake_spectrum()

        # Capturamos IQ
        iq = self._capture_iq(num_samples=max(self.fft_size * 2, 65536))

        # FFT
        N = min(self.fft_size, iq.size)
        x = iq[:N].astype(np.complex64, copy=False)

        # ventana para mejorar estética
        win = np.hanning(N).astype(np.float32)
        xw = x * win

        spec = np.fft.fftshift(np.fft.fft(xw))
        pwr = 20.0 * np.log10(np.abs(spec) + 1e-12)  # dB relativo

        # eje de frecuencia (Hz) alrededor de center
        freqs = np.fft.fftshift(np.fft.fftfreq(N, d=1.0 / float(self.sample_rate)))
        freqs_hz = freqs + float(self.center_freq_hz)

        freqs_list = freqs_hz.tolist()
        pwr_list = pwr.tolist()

        self.last_spectrum = (freqs_list, pwr_list)
        return freqs_list, pwr_list

    def get_smeter(self):
        if not self.last_spectrum[1]:
            return -120.0
        return float(max(self.last_spectrum[1]))

    # -----------------------------------------------------------------------
    def _capture_iq(self, num_samples: int) -> np.ndarray:
        """
        Captura IQ con hackrf_transfer a un archivo temporal y lo convierte a complejos.
        hackrf_transfer guarda IQ intercalado 8-bit signed: I,Q,I,Q...
        """
        # bytes necesarios: 2 bytes por muestra compleja (I + Q)
        num_bytes = int(num_samples) * 2

        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "iq.raw"

            cmd = [
                self.hackrf_transfer_path,
                "-r", str(out_path),
                "-f", str(int(self.center_freq_hz)),
                "-s", str(int(self.sample_rate)),
                "-n", str(int(num_bytes)),
                "-l", str(int(self.lna_gain)),
                "-g", str(int(self.vga_gain)),
                "-a", "1" if self.amp else "0",
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6)

            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(
                    f"hackrf_transfer falló (rc={proc.returncode}). stderr={proc.stderr.strip()}"
                )

            raw = out_path.read_bytes()
            if len(raw) < 4:
                raise RuntimeError("captura IQ vacía")

        # convertir a int8 y luego a complejos
        data = np.frombuffer(raw, dtype=np.int8)
        if data.size % 2 != 0:
            data = data[: data.size - 1]

        i = data[0::2].astype(np.float32)
        q = data[1::2].astype(np.float32)

        # normalizamos a [-1..1] aprox
        iq = (i + 1j * q) / 128.0
        return iq

    # -----------------------------------------------------------------------
    def _fake_spectrum(self):
        N = 512
        f0 = self.center_freq_hz - self.span_hz // 2
        step = self.span_hz / N

        freqs = [f0 + i * step for i in range(N)]
        powers = [-90 + random.random() * 8 for _ in range(N)]

        idx = N // 3
        for i in range(-4, 5):
            j = idx + i
            if 0 <= j < N:
                powers[j] += 20 * math.exp(-abs(i) / 3)

        self.last_spectrum = (freqs, powers)
        return freqs, powers

    # ============================================================
    # Métodos abstractos requeridos por RadioDriver
    # ============================================================
    def set_frequency(self, freq_hz: float):
        self.set_center_freq(freq_hz)

    def set_mode(self, mode: str):
        pass
