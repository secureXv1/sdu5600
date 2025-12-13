from __future__ import annotations

import ctypes as C
import os
import queue
import time
from pathlib import Path

import numpy as np
import sounddevice as sd


def _load_hackrf_dll():
    search_dirs = [
        Path(r"C:\Program Files\PothosSDR\bin"),
        Path(__file__).resolve().parent,
    ]
    for d in search_dirs:
        if d.exists():
            os.add_dll_directory(str(d))

    last_err = None
    for d in search_dirs:
        for name in ("hackrf.dll", "libhackrf.dll"):
            p = d / name
            if p.exists():
                try:
                    return C.CDLL(str(p))
                except OSError as e:
                    last_err = e
    raise RuntimeError(f"No pude cargar HackRF DLL. Error real: {last_err!r}")


lib = _load_hackrf_dll()

hackrf_device_p = C.c_void_p


class hackrf_transfer(C.Structure):
    _fields_ = [
        ("device", hackrf_device_p),
        ("buffer", C.POINTER(C.c_uint8)),
        ("buffer_length", C.c_int),
        ("valid_length", C.c_int),
        ("rx_ctx", C.c_void_p),
        ("tx_ctx", C.c_void_p),
    ]


lib.hackrf_init.restype = C.c_int
lib.hackrf_exit.restype = C.c_int

lib.hackrf_open.restype = C.c_int
lib.hackrf_open.argtypes = [C.POINTER(hackrf_device_p)]

lib.hackrf_close.restype = C.c_int
lib.hackrf_close.argtypes = [hackrf_device_p]

lib.hackrf_set_freq.restype = C.c_int
lib.hackrf_set_freq.argtypes = [hackrf_device_p, C.c_uint64]

lib.hackrf_set_sample_rate.restype = C.c_int
lib.hackrf_set_sample_rate.argtypes = [hackrf_device_p, C.c_double]

lib.hackrf_set_lna_gain.restype = C.c_int
lib.hackrf_set_lna_gain.argtypes = [hackrf_device_p, C.c_uint32]

lib.hackrf_set_vga_gain.restype = C.c_int
lib.hackrf_set_vga_gain.argtypes = [hackrf_device_p, C.c_uint32]

lib.hackrf_set_amp_enable.restype = C.c_int
lib.hackrf_set_amp_enable.argtypes = [hackrf_device_p, C.c_uint8]

RX_CALLBACK = C.CFUNCTYPE(C.c_int, C.POINTER(hackrf_transfer))

lib.hackrf_start_rx.restype = C.c_int
lib.hackrf_start_rx.argtypes = [hackrf_device_p, RX_CALLBACK, C.c_void_p]

lib.hackrf_stop_rx.restype = C.c_int
lib.hackrf_stop_rx.argtypes = [hackrf_device_p]


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
    FM comercial (WBFM) con 2 etapas:
    2.4M -> (LP 120k) -> decim 10 -> 240k -> demod -> (LP 15k) -> decim 5 -> 48k
    """

    def __init__(self, freq_mhz: float):
        self.freq_hz = int(freq_mhz * 1e6)

        self.fs = 2_400_000
        self.fs1 = 240_000
        self.audio_fs = 48_000
        self.decim1 = self.fs // self.fs1      # 10
        self.decim2 = self.fs1 // self.audio_fs # 5

        # Gains base (ajusta luego desde UI)
        self.lna = 24
        self.vga = 12
        self.amp = 0

        self.chan_fir = fir_lowpass(129, 120_000.0, self.fs)
        self.chan_zi_r = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)
        self.chan_zi_i = np.zeros(len(self.chan_fir) - 1, dtype=np.float32)

        self.aud_fir = fir_lowpass(129, 15_000.0, self.fs1)
        self.aud_zi = np.zeros(len(self.aud_fir) - 1, dtype=np.float32)

        self.prev_iq = 0.0 + 0.0j
        self.de_state = 0.0
        self.tau = 75e-6

        self.audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=80)

        self.dev = hackrf_device_p()
        self._cb = RX_CALLBACK(self._rx_cb)
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True

        rc = lib.hackrf_init()
        if rc != 0:
            raise RuntimeError(f"hackrf_init failed rc={rc}")

        rc = lib.hackrf_open(C.byref(self.dev))
        if rc != 0:
            raise RuntimeError(f"hackrf_open failed rc={rc}")

        lib.hackrf_set_sample_rate(self.dev, float(self.fs))
        lib.hackrf_set_freq(self.dev, C.c_uint64(self.freq_hz))
        lib.hackrf_set_lna_gain(self.dev, self.lna)
        lib.hackrf_set_vga_gain(self.dev, self.vga)
        lib.hackrf_set_amp_enable(self.dev, 1 if self.amp else 0)

        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        rc = lib.hackrf_start_rx(self.dev, self._cb, None)
        if rc != 0:
            raise RuntimeError(f"hackrf_start_rx failed rc={rc}")

        try:
            with sd.OutputStream(dtype="float32", blocksize=2048, callback=self._audio_cb):
                while self._running:
                    time.sleep(0.2)
        finally:
            self.stop()

    def stop(self):
        if not self._running:
            return
        self._running = False
        try:
            lib.hackrf_stop_rx(self.dev)
        except Exception:
            pass
        try:
            lib.hackrf_close(self.dev)
        except Exception:
            pass
        try:
            lib.hackrf_exit()
        except Exception:
            pass

    def _rx_cb(self, transfer_ptr):
        if not self._running:
            return 0

        tr = transfer_ptr.contents
        n = int(tr.valid_length)
        if n <= 0:
            return 0

        buf_i8 = C.cast(tr.buffer, C.POINTER(C.c_int8))
        data = np.frombuffer(C.string_at(buf_i8, n), dtype=np.int8)
        if data.size < 4:
            return 0
        if data.size % 2:
            data = data[:-1]

        i = data[0::2].astype(np.float32)
        q = data[1::2].astype(np.float32)
        iq = (i + 1j * q) / 128.0
        iq = iq - np.mean(iq)

        iq_f, self.chan_zi_r, self.chan_zi_i = fir_stream_cplx(
            iq.astype(np.complex64), self.chan_fir, self.chan_zi_r, self.chan_zi_i
        )
        iq_240k = iq_f[::self.decim1]

        x = np.empty_like(iq_240k)
        x[0] = self.prev_iq
        x[1:] = iq_240k[:-1]
        self.prev_iq = iq_240k[-1]
        fm = np.angle(iq_240k * np.conj(x)).astype(np.float32)

        fm_f, self.aud_zi = fir_stream(fm, self.aud_fir, self.aud_zi)
        audio = fm_f[::self.decim2]

        audio, self.de_state = deemph(audio, self.audio_fs, self.tau, self.de_state)
        audio = np.tanh(audio * 2.2).astype(np.float32)

        try:
            self.audio_q.put_nowait(audio)
        except queue.Full:
            pass
        return 0

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
