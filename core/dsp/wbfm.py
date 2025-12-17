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
    def __init__(self, iq_bytes_queue: "queue.Queue[bytes]", freq_mhz: float, on_audio=None):
        self.freq_hz = int(freq_mhz * 1e6)

        self.fs = 2_400_000
        self.fs1 = 240_000
        self.audio_fs = 48_000
        self.decim1 = self.fs // self.fs1       # 10
        self.decim2 = self.fs1 // self.audio_fs # 5

        self.iq_bytes_q = iq_bytes_queue

        # ---- Params en vivo (tuneables) ----
        self._param_lock = threading.Lock()
        self.chan_taps = 161
        self.aud_taps = 161
        self.chan_cutoff_hz = 110_000.0
        self.aud_cutoff_hz = 14_000.0
        self.tau = 90e-6
        self.drive = 1.2

        # filtros (reconstruibles en caliente)
        self._rebuild_filters()

        self.prev_iq = 0.0 + 0.0j
        self.de_state = 0.0

        self.audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=80)

        self._running = False
        self._thr: threading.Thread | None = None

        # bloque fijo (10 ms) => audio limpio y estable
        self.block_iq = 24_000  # 2.4e6 * 0.010s
        self._bytes_buf = b""
        self.on_audio = on_audio  # callback opcional para grabación
        self._stop_evt = threading.Event()





    def _rebuild_filters(self):
        # clamp básico para evitar cutoffs inválidos
        chan_cut = float(max(1.0, min(self.chan_cutoff_hz, (self.fs  * 0.49))))
        aud_cut  = float(max(50.0, min(self.aud_cutoff_hz,  (self.fs1 * 0.49))))

        self.chan_fir = fir_lowpass(int(self.chan_taps), chan_cut, self.fs)
        self.chan_zi_r = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)
        self.chan_zi_i = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)

        self.aud_fir = fir_lowpass(int(self.aud_taps), aud_cut, self.fs1)
        self.aud_zi = np.zeros(len(self.aud_fir) - 1, dtype=np.float32)

    def update_params(
        self,
        chan_cutoff_hz: float | None = None,
        aud_cutoff_hz: float | None = None,
        tau_us: float | None = None,
        drive: float | None = None,
        chan_taps: int | None = None,
        aud_taps: int | None = None,
    ):
        """Actualiza parámetros en caliente. tau_us en microsegundos."""
        with self._param_lock:
            if chan_cutoff_hz is not None:
                self.chan_cutoff_hz = float(chan_cutoff_hz)
            if aud_cutoff_hz is not None:
                self.aud_cutoff_hz = float(aud_cutoff_hz)
            if tau_us is not None:
                self.tau = float(tau_us) * 1e-6
            if drive is not None:
                self.drive = float(drive)
            if chan_taps is not None:
                self.chan_taps = int(chan_taps)
            if aud_taps is not None:
                self.aud_taps = int(aud_taps)

            self._rebuild_filters()
    def start(self):
        if self._running:
            return
        self._running = True

        self._stop_evt.clear()


        self._thr = threading.Thread(target=self._dsp_loop, daemon=True)
        self._thr.start()

        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        try:
            with sd.OutputStream(dtype="float32", blocksize=2048, callback=self._audio_cb):
                while self._running and not self._stop_evt.is_set():
                    time.sleep(0.05)

        finally:
            self.stop()

    def stop(self):
        self._running = False
        self._stop_evt.set()


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
                with self._param_lock:
                    drive = float(self.drive)
                audio = np.tanh(audio * drive).astype(np.float32)

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

                    # TAP para grabación (ScannerEngine)
        if self.on_audio is not None and filled > 0:
            try:
                self.on_audio(out[:filled].copy())
            except Exception:
                pass

        outdata[:, 0] = out
