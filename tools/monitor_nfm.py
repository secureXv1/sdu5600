# tools/monitor_nfm.py
from __future__ import annotations

import numpy as np
import sounddevice as sd

# pip install pyhackrf2
from pyhackrf2 import HackRF  # type: ignore


def _fir_lowpass(num_taps: int, cutoff_hz: float, fs: float) -> np.ndarray:
    """FIR lowpass (sinc + Hamming)."""
    fc = float(cutoff_hz) / float(fs)  # 0..0.5
    n = np.arange(num_taps) - (num_taps - 1) / 2.0
    h = 2 * fc * np.sinc(2 * fc * n)
    w = np.hamming(num_taps)
    h = h * w
    h = h / np.sum(h)
    return h.astype(np.float32)


class NFMMonitor:
    def __init__(
        self,
        freq_hz: int,
        fs: int = 2_000_000,
        audio_fs: int = 48_000,
        cutoff_hz: float = 12_000.0,
        lna_gain: int = 16,
        vga_gain: int = 16,
        amp: int = 0,
    ):
        self.freq_hz = int(freq_hz)
        self.fs = int(fs)
        self.audio_fs = int(audio_fs)
        self.decim = int(round(self.fs / self.audio_fs))
        if abs(self.fs / self.decim - self.audio_fs) > 200:
            raise ValueError(f"fs ({fs}) no decima bien a {audio_fs}. Usa fs=1920000 o 2400000 por ejemplo.")

        self.audio_fs = int(round(self.fs / self.decim))

        self.lna_gain = int(lna_gain)
        self.vga_gain = int(vga_gain)
        self.amp = int(amp)

        # filtros
        self.lp = _fir_lowpass(num_taps=129, cutoff_hz=cutoff_hz, fs=self.fs)
        self._lp_zi = np.zeros(len(self.lp) - 1, dtype=np.float32)

        # deemphasis NFM (~75 us). Si quieres 50 us (EU), cambia tau.
        self.tau = 75e-6
        self._de_y = 0.0

        # estado para demod fase
        self._prev_iq = 0.0 + 0.0j

        # buffer audio
        self._audio_queue = []

        self.dev = HackRF()

    def start(self):
        # Config del HackRF
        self.dev.sample_rate = self.fs
        self.dev.center_freq = self.freq_hz

        # Ganancias (pyhackrf2 expone propiedades típicas)
        try:
            self.dev.lna_gain = self.lna_gain
        except Exception:
            pass
        try:
            self.dev.vga_gain = self.vga_gain
        except Exception:
            pass
        try:
            self.dev.amp_enable = bool(self.amp)
        except Exception:
            pass

        # Salida de audio
        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        print(f"[NFM] freq={self.freq_hz/1e6:.6f} MHz | fs={self.fs} | audio_fs={self.audio_fs} | decim={self.decim}")
        print("[NFM] Ctrl+C para detener")

        # Stream de audio
        with sd.OutputStream(dtype="float32", callback=self._audio_cb, blocksize=1024):
            # RX callback del HackRF
            self.dev.start_rx(self._rx_cb)

            try:
                while True:
                    sd.sleep(1000)
            finally:
                try:
                    self.dev.stop_rx()
                except Exception:
                    pass
                try:
                    self.dev.close()
                except Exception:
                    pass

    def _rx_cb(self, samples: np.ndarray):
        """
        pyhackrf2 entrega IQ como complejo (complex64) o como int8 intercalado según versión.
        Normalizamos a complex64 [-1..1].
        """
        iq = samples

        # Normalización si vienen bytes
        if iq.dtype == np.int8:
            d = iq.astype(np.float32)
            i = d[0::2]
            q = d[1::2]
            iq = (i + 1j * q) / 128.0
        else:
            iq = iq.astype(np.complex64, copy=False)

        if iq.size < 4:
            return

        # NFM demod por diferencia de fase: angle(x[n]*conj(x[n-1]))
        x = np.empty_like(iq)
        x[0] = self._prev_iq
        x[1:] = iq[:-1]
        self._prev_iq = iq[-1]

        fm = np.angle(iq * np.conj(x)).astype(np.float32)

        # Lowpass
        y, self._lp_zi = self._lfilter_fir(fm, self.lp, self._lp_zi)

        # Decimación
        y = y[:: self.decim]

        # De-emphasis (filtro IIR simple)
        y = self._deemph(y)

        # Limitador suave para no reventar audio
        y = np.tanh(y * 2.5).astype(np.float32)

        self._audio_queue.append(y)

    def _audio_cb(self, outdata, frames, _time, _status):
        # Consumir buffer
        if not self._audio_queue:
            outdata[:] = 0
            return

        chunk = self._audio_queue.pop(0)
        if chunk.size < frames:
            # rellenar
            out = np.zeros(frames, dtype=np.float32)
            out[: chunk.size] = chunk
        else:
            out = chunk[:frames]
            rest = chunk[frames:]
            if rest.size:
                self._audio_queue.insert(0, rest)

        outdata[:, 0] = out

    @staticmethod
    def _lfilter_fir(x: np.ndarray, h: np.ndarray, zi: np.ndarray):
        # FIR streaming: y = conv(x, h) con estado zi
        x = x.astype(np.float32, copy=False)
        h = h.astype(np.float32, copy=False)

        # concatenar estado + x
        x2 = np.concatenate([zi, x])
        y = np.convolve(x2, h, mode="valid")
        # nuevo estado: últimas (len(h)-1) muestras de x2
        new_zi = x2[-(len(h) - 1):].copy()
        return y.astype(np.float32), new_zi

    def _deemph(self, x: np.ndarray) -> np.ndarray:
        # y[n] = y[n-1] + alpha*(x[n]-y[n-1])
        # alpha = dt/(tau+dt)
        dt = 1.0 / float(self.audio_fs)
        alpha = dt / (self.tau + dt)

        y = np.empty_like(x, dtype=np.float32)
        yy = float(self._de_y)
        for i, xx in enumerate(x):
            yy = yy + alpha * (float(xx) - yy)
            y[i] = yy
        self._de_y = yy
        return y


if __name__ == "__main__":
    # Cambia a la frecuencia NFM que quieras monitorear
    # Ejemplo: 439.000 MHz
    mon = NFMMonitor(freq_hz=439_000_000, fs=2_000_000, lna_gain=16, vga_gain=16, amp=0)
    mon.start()
