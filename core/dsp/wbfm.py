# core/dsp/wbfm.py
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


def iq_from_int8_bytes(data: bytes) -> np.ndarray:
    a = np.frombuffer(data, dtype=np.int8)
    if a.size < 4:
        return np.empty(0, dtype=np.complex64)
    if a.size % 2:
        a = a[:-1]
    i = a[0::2].astype(np.float32)
    q = a[1::2].astype(np.float32)
    iq = (i + 1j * q) / 128.0
    # DC remove
    iq = iq - np.mean(iq)
    return iq.astype(np.complex64, copy=False)


class WBFMStream:
    """
    WBFM desde cola de IQ bytes (I,Q int8 intercalado).
    Pipeline:
      2.4M -> LP 120k -> decim 10 -> 240k -> demod -> LP 15k -> decim 5 -> 48k
    """
    def __init__(self, iq_bytes_queue: "queue.Queue[bytes]", freq_mhz: float):
        self.freq_hz = int(freq_mhz * 1e6)

        self.fs = 2_400_000
        self.fs1 = 240_000
        self.audio_fs = 48_000
        self.decim1 = self.fs // self.fs1       # 10
        self.decim2 = self.fs1 // self.audio_fs # 5

        self.iq_bytes_q = iq_bytes_queue

        # filtros
        self.chan_fir = fir_lowpass(161, 110_000.0, self.fs) #MEJORAR AUDIO
        self.chan_zi_r = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)
        self.chan_zi_i = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)

        self.aud_fir = fir_lowpass(161, 14_000.0, self.fs1) #MEJORAR AUDIO
        self.aud_zi = np.zeros(len(self.aud_fir) - 1, dtype=np.float32)

        self.prev_iq = 0.0 + 0.0j
        self.de_state = 0.0
        self.tau = 90e-6 #MEJORAR AUDIO

        self.audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=80)

        self._running = False
        self._thr: threading.Thread | None = None

        # bloque fijo (10 ms) => audio limpio y estable
        self.block_iq = 24_000  # 2.4e6 * 0.010s
        self._bytes_buf = b""

    def start(self):
        if self._running:
            return
        self._running = True

        self._thr = threading.Thread(target=self._dsp_loop, daemon=True)
        self._thr.start()

        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        try:
            with sd.OutputStream(dtype="float32", blocksize=2048, callback=self._audio_cb):
                while self._running:
                    time.sleep(0.2)
        finally:
            self.stop()

    def stop(self):
        self._running = False

    def _dsp_loop(self):
        need_bytes = int(self.block_iq) * 2  # I,Q int8
        while self._running:
            try:
                chunk = self.iq_bytes_q.get(timeout=0.25)
            except queue.Empty:
                continue

            self._bytes_buf += chunk

            while self._running and len(self._bytes_buf) >= need_bytes:
                raw = self._bytes_buf[:need_bytes]
                self._bytes_buf = self._bytes_buf[need_bytes:]

                iq = iq_from_int8_bytes(raw)
                if iq.size < 8:
                    continue

                # canal
                iq_f, self.chan_zi_r, self.chan_zi_i = fir_stream_cplx(
                    iq, self.chan_fir, self.chan_zi_r, self.chan_zi_i
                )
                iq_240k = iq_f[::self.decim1]

                # demod FM
                x = np.empty_like(iq_240k)
                x[0] = self.prev_iq
                x[1:] = iq_240k[:-1]
                self.prev_iq = iq_240k[-1]
                fm = np.angle(iq_240k * np.conj(x)).astype(np.float32)

                # audio
                fm_f, self.aud_zi = fir_stream(fm, self.aud_fir, self.aud_zi)
                audio = fm_f[::self.decim2]

                audio, self.de_state = deemph(audio, self.audio_fs, self.tau, self.de_state)
                audio = np.tanh(audio * 1.2).astype(np.float32)

                try:
                    self.audio_q.put_nowait(audio)
                except queue.Full:
                    pass

    def _audio_cb(self, outdata, frames, _time, _status):
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        while filled < frames:
            try:
                chunk = self.audio_q.get_nowait()
            except queue.Empty:
                break
            n = min(frames - filled, chunk.size)
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
