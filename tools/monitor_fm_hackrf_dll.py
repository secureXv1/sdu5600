from __future__ import annotations
import ctypes as C
import threading
import queue
import time
import numpy as np
import sounddevice as sd
from pathlib import Path
import os

# Ajusta si tu DLL está en otra parte
# Recomendado: copiar hackrf.dll o libhackrf.dll junto a este script
DLL_CANDIDATES = ["hackrf.dll", "libhackrf.dll"]


def load_hackrf():
    """
    Carga hackrf.dll priorizando la instalación de PothosSDR (la que YA funciona),
    y deja tools/ solo como fallback.
    """
    search_dirs = [
        Path(r"C:\Program Files\PothosSDR\bin"),      # ✅ PRIMERO: DLL correcta
        Path(__file__).resolve().parent,              # fallback: tools/
    ]

    # Registrar directorios para resolver dependencias (libusb, etc.)
    for d in search_dirs:
        if d.exists():
            os.add_dll_directory(str(d))

    last_err = None
    for d in search_dirs:
        for name in ("hackrf.dll", "libhackrf.dll"):
            p = d / name
            if p.exists():
                try:
                    print(f"[DLL] Cargando {p}")
                    return C.CDLL(str(p))
                except OSError as e:
                    last_err = e

    raise FileNotFoundError(
        f"No pude cargar hackrf.dll desde PothosSDR ni tools/. Error real: {last_err}"
    )


# Cargar DLL HackRF AHORA
lib = load_hackrf()

# ---- Tipos/constantes mínimos ----
hackrf_device_p = C.c_void_p
hackrf_transfer_p = C.c_void_p

# hackrf_transfer struct (solo los campos que usamos)
class hackrf_transfer(C.Structure):
    _fields_ = [
        ("device", hackrf_device_p),
        ("buffer", C.POINTER(C.c_uint8)),
        ("buffer_length", C.c_int),
        ("valid_length", C.c_int),
        ("rx_ctx", C.c_void_p),
        ("tx_ctx", C.c_void_p),
    ]

# Prototipos
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


def deemph(x: np.ndarray, fs: int, tau: float = 75e-6, state: float = 0.0):
    dt = 1.0 / fs
    a = dt / (tau + dt)
    y = np.empty_like(x, dtype=np.float32)
    yy = float(state)
    for i, xx in enumerate(x):
        yy = yy + a * (float(xx) - yy)
        y[i] = yy
    return y, yy

class WBFMStream:
    def __init__(self, freq_mhz: float):
        self.freq_hz = int(freq_mhz * 1e6)

        # Para FM broadcast:
        self.fs = 2_400_000
        self.audio_fs = 48_000
        self.decim = self.fs // self.audio_fs  # 50 exacto

        self.lna = 32
        self.vga = 24
        self.amp = 0

        self.prev_iq = 0.0 + 0.0j
        self.de_state = 0.0

        self.audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=60)
        self.stop_flag = threading.Event()

        self.dev = hackrf_device_p()

        # Mantener referencia al callback para que no lo GC-ee
        self._cb = RX_CALLBACK(self._rx_cb)

    def start(self):
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

        print(f"[FM DLL] {self.freq_hz/1e6:.3f} MHz | fs={self.fs} | audio_fs={self.audio_fs} | decim={self.decim}")
        print("[FM DLL] Ctrl+C para detener")

        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        # Arranca RX
        rc = lib.hackrf_start_rx(self.dev, self._cb, None)
        if rc != 0:
            raise RuntimeError(f"hackrf_start_rx failed rc={rc}")

        # Audio loop (consume cola)
        try:
            with sd.OutputStream(dtype="float32", blocksize=2048, callback=self._audio_cb):
                while True:
                    time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
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

    # Callback de HackRF: buffer contiene IQ int8 intercalado I,Q,I,Q...
    def _rx_cb(self, transfer_ptr):
        tr = transfer_ptr.contents
        n = int(tr.valid_length)
        if n <= 0:
            return 0

        buf = C.cast(tr.buffer, C.POINTER(C.c_int8))
        data = np.frombuffer(C.string_at(buf, n), dtype=np.int8)
        if data.size < 4:
            return 0
        if data.size % 2:
            data = data[:-1]

        i = data[0::2].astype(np.float32)
        q = data[1::2].astype(np.float32)
        iq = (i + 1j * q) / 128.0

        # Demod FM: angle(x[n]*conj(x[n-1]))
        x = np.empty_like(iq)
        x[0] = self.prev_iq
        x[1:] = iq[:-1]
        self.prev_iq = iq[-1]
        fm = np.angle(iq * np.conj(x)).astype(np.float32)

        # Decimar a 48k
        audio = fm[:: self.decim]

        # De-emphasis + ganancia + limiter
        audio, self.de_state = deemph(audio, self.audio_fs, 75e-6, self.de_state)
        audio = np.tanh(audio * 8.0).astype(np.float32)

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
            out[filled:filled+n] = chunk[:n]
            filled += n
            rest = chunk[n:]
            if rest.size:
                # reinsertar lo que sobró
                try:
                    self.audio_q.put_nowait(rest)
                except queue.Full:
                    pass
                break
        outdata[:, 0] = out

if __name__ == "__main__":
    # Cambia a tu emisora fuerte
    FM_MHZ = 91.4
    WBFMStream(FM_MHZ).start()
