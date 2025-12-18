# drivers/hackrf_driver.py
# HackRF (Windows) usando hackrf.dll por ctypes
# RX continuo -> IQ RingBuffer -> FFT/Waterfall + Audio comparten la misma fuente

from __future__ import annotations

import os
import ctypes as C
import threading
import time
from pathlib import Path

import numpy as np

from .base import RadioDriver
import queue



# --------------------------------------------------------------------
# DLL load (Windows / Python 3.8+)
# --------------------------------------------------------------------
def _load_hackrf_dll() -> C.CDLL:
    if os.name != "nt":
        raise RuntimeError("Este driver está orientado a Windows (hackrf.dll).")

    dll_dir = Path(r"C:\Program Files\PothosSDR\bin")
    if dll_dir.exists():
        os.add_dll_directory(str(dll_dir))

    # intenta cargar hackrf.dll
    try:
        return C.CDLL("hackrf.dll")
    except OSError as e:
        raise RuntimeError(
            "No pude cargar hackrf.dll. Verifica que exista en "
            r"C:\Program Files\PothosSDR\bin y que sea de 64-bit."
        ) from e


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


# ---- function signatures ----
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

lib.hackrf_set_baseband_filter_bandwidth.restype = C.c_int
lib.hackrf_set_baseband_filter_bandwidth.argtypes = [hackrf_device_p, C.c_uint32]

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


# --------------------------------------------------------------------
# IQ Ring Buffer
# --------------------------------------------------------------------
class IQRingBuffer:
    """Ring buffer de IQ complex64."""
    def __init__(self, capacity: int = 2_000_000):
        self.capacity = int(capacity)
        self.buf = np.zeros(self.capacity, dtype=np.complex64)
        self.wpos = 0
        self.lock = threading.Lock()

    def push_iq_int8(self, data_i8: np.ndarray):
        if data_i8.size < 2:
            return
        if data_i8.size % 2:
            data_i8 = data_i8[:-1]

        i = data_i8[0::2].astype(np.float32, copy=False)
        q = data_i8[1::2].astype(np.float32, copy=False)
        iq = (i + 1j * q) / 128.0

        # DC removal (muy importante para FM/NFM)
        iq = iq - np.mean(iq)

        self.push(iq.astype(np.complex64, copy=False))


    def push(self, iq: np.ndarray):
        iq = np.asarray(iq, dtype=np.complex64)
        n = int(iq.size)
        if n <= 0:
            return
        if n >= self.capacity:
            iq = iq[-self.capacity:]
            n = int(iq.size)

        with self.lock:
            end = self.wpos + n
            if end <= self.capacity:
                self.buf[self.wpos:end] = iq
            else:
                k = self.capacity - self.wpos
                self.buf[self.wpos:] = iq[:k]
                self.buf[:end - self.capacity] = iq[k:]
            self.wpos = end % self.capacity

    def latest(self, n: int) -> np.ndarray:
        n = min(int(n), self.capacity)
        if n <= 0:
            return np.zeros(0, dtype=np.complex64)

        with self.lock:
            end = self.wpos
            start = (end - n) % self.capacity
            if start < end:
                out = self.buf[start:end].copy()
            else:
                out = np.concatenate((self.buf[start:], self.buf[:end])).copy()
        return out
    



# --------------------------------------------------------------------
# HackRF Driver (ctypes continuous RX)
# --------------------------------------------------------------------
class HackRFDriver(RadioDriver):
    """
    HackRF con RX continuo (hackrf_start_rx).
    FFT/Waterfall/Audio leen del mismo ring buffer.
    """

    def __init__(self, config=None):
        super().__init__(config or {})

        self.center_freq_hz = int(self.config.get("center_freq_hz", 435_000_000))
        self.sample_rate = int(self.config.get("sample_rate", 2_400_000))  # recomendado para tu DSP
        self.fft_size = int(self.config.get("fft_size", 4096))
        self.span_hz = int(self.config.get("span_hz", self.sample_rate))  # solo visual

        self.lna_gain = int(self.config.get("lna_gain", 24))
        self.vga_gain = int(self.config.get("vga_gain", 12))
        self.amp = int(self.config.get("amp", 0))

        # baseband filter (0 = auto). Si no lo quieres, déjalo en 0.
        self.baseband_filter = int(self.config.get("baseband_filter", 0))

        self.dev = hackrf_device_p()
        self._cb = RX_CALLBACK(self._rx_cb)

        self._rx_running = False
        self._rx_lock = threading.Lock()

        self.iqbuf = IQRingBuffer(capacity=int(self.config.get("iq_capacity", 2_000_000)))
        self.last_spectrum = ([], [])

        # para estabilizar al tunear (scanner)
        self._last_tune_ts = 0.0

        self._audio_bytes_q = queue.Queue(maxsize=200)  # ~1s aprox según tamaño de chunk

    # ---------- lifecycle ----------
    def connect(self):
        if self.connected:
            return True

        rc = lib.hackrf_init()
        if rc != 0:
            raise RuntimeError(f"hackrf_init failed rc={rc}")

        rc = lib.hackrf_open(C.byref(self.dev))
        if rc != 0:
            lib.hackrf_exit()
            raise RuntimeError(f"hackrf_open failed rc={rc}")

        # config base
        lib.hackrf_set_sample_rate(self.dev, float(self.sample_rate))
        lib.hackrf_set_freq(self.dev, C.c_uint64(int(self.center_freq_hz)))

        # gains
        lib.hackrf_set_lna_gain(self.dev, int(self.lna_gain))
        lib.hackrf_set_vga_gain(self.dev, int(self.vga_gain))
        lib.hackrf_set_amp_enable(self.dev, 1 if self.amp else 0)

        # baseband filter (si 0, lo ignoramos para dejar auto)
        try:
            if self.baseband_filter and self.baseband_filter > 0:
                lib.hackrf_set_baseband_filter_bandwidth(self.dev, int(self.baseband_filter))
        except Exception:
            pass

        self.connected = True
        self.start_stream()
        return True

    def disconnect(self):
        try:
            self.stop_stream()
        finally:
            if self.dev:
                try:
                    lib.hackrf_close(self.dev)
                except Exception:
                    pass
            try:
                lib.hackrf_exit()
            except Exception:
                pass

            self.connected = False
        return True

    # ---------- stream ----------
    def start_stream(self):
        if not self.connected:
            self.connect()

        with self._rx_lock:
            if self._rx_running:
                return
            self._rx_running = True

            rc = lib.hackrf_start_rx(self.dev, self._cb, None)
            if rc != 0:
                self._rx_running = False
                raise RuntimeError(f"hackrf_start_rx failed rc={rc}")

    def stop_stream(self):
        with self._rx_lock:
            if not self._rx_running:
                return
            self._rx_running = False
            try:
                lib.hackrf_stop_rx(self.dev)
            except Exception:
                pass

    # ---------- tuning / gains ----------
    def set_center_freq(self, freq_hz):
        self.center_freq_hz = int(freq_hz)
        if self.connected:
            try:
                lib.hackrf_set_freq(self.dev, C.c_uint64(int(self.center_freq_hz)))
                self._last_tune_ts = time.time()
            except Exception:
                pass

    def set_span(self, span_hz):
        self.span_hz = int(span_hz)

    def set_rf_gain(self, lna_db=None, vga_db=None, amp=None):
        if lna_db is not None:
            self.lna_gain = int(lna_db)
            if self.connected:
                try:
                    lib.hackrf_set_lna_gain(self.dev, int(self.lna_gain))
                except Exception:
                    pass

        if vga_db is not None:
            self.vga_gain = int(vga_db)
            if self.connected:
                try:
                    lib.hackrf_set_vga_gain(self.dev, int(self.vga_gain))
                except Exception:
                    pass

        if amp is not None:
            self.amp = 1 if amp else 0
            if self.connected:
                try:
                    lib.hackrf_set_amp_enable(self.dev, 1 if self.amp else 0)
                except Exception:
                    pass

    # ---------- callback ----------
    def _rx_cb(self, transfer_ptr):
        if not self._rx_running:
            return 0

        tr = transfer_ptr.contents
        n = int(tr.valid_length)
        if n <= 0:
            return 0

        # lee bytes crudos UNA vez
        raw = C.string_at(tr.buffer, n)

        # 1) para FFT (ring buffer)
        data = np.frombuffer(raw, dtype=np.int8)
        try:
            self.iqbuf.push_iq_int8(data)
        except Exception:
            pass

        # 2) para audio (cola)
        try:
            self._audio_bytes_q.put_nowait(raw)
        except Exception:
            pass

        return 0



    # ---------- spectrum ----------
    def get_spectrum(self):
        """
        FFT sobre IQ del ring buffer. No detiene RX.
        """
        if not self.connected:
            self.connect()

        N = int(self.fft_size)
        iq = self.iqbuf.latest(max(N * 4, N))
        if iq.size < N:
            return [], []

        x = iq[-N:].astype(np.complex64, copy=False)

        # ventana
        win = np.hanning(N).astype(np.float32)
        xw = x * win

        spec = np.fft.fftshift(np.fft.fft(xw))
        pwr = 20.0 * np.log10(np.abs(spec) + 1e-12)

        freqs = np.fft.fftshift(np.fft.fftfreq(N, d=1.0 / float(self.sample_rate)))
        freqs_hz = freqs + float(self.center_freq_hz)

        freqs_list = freqs_hz.tolist()
        pwr_list = pwr.tolist()

        self.last_spectrum = (freqs_list, pwr_list)
        return freqs_list, pwr_list

    def get_latest_iq(self, n: int) -> np.ndarray:
        if not self.connected:
            self.connect()
        return self.iqbuf.latest(n)

    def get_smeter(self):
        if not self.last_spectrum[1]:
            return -120.0
        return float(max(self.last_spectrum[1]))

    # Métodos abstractos (compat)
    def set_frequency(self, freq_hz: float):
        self.set_center_freq(freq_hz)

    def set_mode(self, mode: str):
        pass

    def get_audio_queue(self):
        return self._audio_bytes_q

