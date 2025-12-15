from __future__ import annotations

import queue
import threading
import time
import numpy as np
import sounddevice as sd


def fir_lowpass(num_taps: int, cutoff_hz: float, fs: float) -> np.ndarray:
    fc = float(cutoff_hz) / float(fs)
    n = np.arange(num_taps) - (num_taps - 1) / 2.0
    h = 2 * fc * np.sinc(2 * fc * n)
    h *= np.hamming(num_taps)
    h /= np.sum(h)
    return h.astype(np.float32)


def fir_stream(x: np.ndarray, h: np.ndarray, zi: np.ndarray):
    x = x.astype(np.float32, copy=False)
    x2 = np.concatenate([zi, x])
    y = np.convolve(x2, h, mode="valid").astype(np.float32)
    new_zi = x2[-(len(h) - 1):].copy()
    return y, new_zi


def fir_stream_cplx(iq: np.ndarray, h: np.ndarray, zi_r: np.ndarray, zi_i: np.ndarray):
    yr, zi_r = fir_stream(iq.real.astype(np.float32, copy=False), h, zi_r)
    yi, zi_i = fir_stream(iq.imag.astype(np.float32, copy=False), h, zi_i)
    return (yr + 1j * yi).astype(np.complex64), zi_r, zi_i


def deemph(x: np.ndarray, fs: int, tau: float, state: float):
    dt = 1.0 / float(fs)
    a = dt / (tau + dt)
    y = np.empty_like(x, dtype=np.float32)
    s = float(state)
    for i, xx in enumerate(x):
        s = s + a * (float(xx) - s)
        y[i] = s
    return y, s


class WBFMStream:
    """
    FM comercial (WBFM) desde IQ del driver (NO abre hardware).
    Cadena diseñada para fs=2.4MHz:
      2.4M -> (LP 120k) -> decim 10 -> 240k -> demod -> (LP 15k) -> decim 5 -> 48k
    """

    def __init__(self, driver):
        self.driver = driver

        self.fs = int(getattr(driver, "sample_rate", 2_400_000))
        if self.fs != 2_400_000:
            raise RuntimeError(
                f"WBFMStream requiere sample_rate=2_400_000 (actual={self.fs}). "
                "Ajusta config/radios.json para HackRF."
            )

        self.fs1 = 240_000
        self.audio_fs = 48_000
        self.decim1 = self.fs // self.fs1       # 10
        self.decim2 = self.fs1 // self.audio_fs # 5

        # filtros
        self.chan_fir = fir_lowpass(129, 120_000.0, self.fs)
        self.chan_zi_r = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)
        self.chan_zi_i = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)

        self.aud_fir = fir_lowpass(129, 15_000.0, self.fs1)
        self.aud_zi = np.zeros(len(self.aud_fir) - 1, dtype=np.float32)

        # demod state
        self.prev_iq = 0.0 + 0.0j
        self.de_state = 0.0
        self.tau = 75e-6  # WBFM

        # audio queue
        self.audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=120)

        self._running = False
        self._th: threading.Thread | None = None

        # tamaño de bloque IQ a procesar
        self.block_iq = 262_144  # ~109 ms @ 2.4Msps (ok)

    def start(self):
        if self._running:
            return
        self._running = True

        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        self._th = threading.Thread(target=self._worker, daemon=True)
        self._th.start()

        # stream de salida (callback)
        self._out = sd.OutputStream(dtype="float32", blocksize=2048, callback=self._audio_cb)
        self._out.start()

    def stop(self):
        if not self._running:
            return
        self._running = False

        try:
            if hasattr(self, "_out"):
                self._out.stop()
                self._out.close()
        except Exception:
            pass

        if self._th:
            self._th.join(timeout=1.0)
            self._th = None

        # limpia cola
        try:
            while True:
                self.audio_q.get_nowait()
        except Exception:
            pass

    def _worker(self):
        # loop de DSP (lee IQ del ring buffer)
        while self._running:
            iq = None
            try:
                iq = self.driver.get_latest_iq(self.block_iq)
            except Exception:
                iq = None

            if iq is None or getattr(iq, "size", 0) < (self.fft_size_safe()):
                time.sleep(0.02)
                continue

            # IQ complejo ya viene complex64
            iq = iq.astype(np.complex64, copy=False)
            iq = iq - np.mean(iq)

            # Canal (LP) y decimación
            iq_f, self.chan_zi_r, self.chan_zi_i = fir_stream_cplx(
                iq, self.chan_fir, self.chan_zi_r, self.chan_zi_i
            )
            iq_240k = iq_f[::self.decim1]
            if iq_240k.size < 8:
                continue

            # FM demod (discriminador por fase)
            x = np.empty_like(iq_240k)
            x[0] = self.prev_iq
            x[1:] = iq_240k[:-1]
            self.prev_iq = iq_240k[-1]
            fm = np.angle(iq_240k * np.conj(x)).astype(np.float32)

            # Audio LP + decimación
            fm_f, self.aud_zi = fir_stream(fm, self.aud_fir, self.aud_zi)
            audio = fm_f[::self.decim2]

            # De-emphasis + limit suave
            audio, self.de_state = deemph(audio, self.audio_fs, self.tau, self.de_state)
            audio = np.tanh(audio * 2.2).astype(np.float32)

            # push audio
            try:
                self.audio_q.put_nowait(audio)
            except queue.Full:
                pass

    def fft_size_safe(self):
        # mínimo para evitar problemas de tamaño
        return 4096

    def _audio_cb(self, outdata, frames, _time, _status):
        out = np.zeros(frames, dtype=np.float32)
        filled = 0

        while filled < frames:
            try:
                chunk = self.audio_q.get_nowait()
            except queue.Empty:
                break

            n = min(frames - filled, int(chunk.size))
            out[filled:filled + n] = chunk[:n]
            filled += n

            rest = chunk[n:]
            if rest.size:
                try:
                    self.audio_q.put_nowait(rest)
                except queue.Full:
                    pass
                break

        outdata[:, 0] = out
