from __future__ import annotations
import subprocess, threading, queue, tempfile, time, shutil
from pathlib import Path
import numpy as np
import sounddevice as sd

HACKRF_TRANSFER = shutil.which("hackrf_transfer") or r"C:\Program Files\PothosSDR\bin\hackrf_transfer.exe"

def deemph(x: np.ndarray, fs: int, tau: float = 75e-6) -> np.ndarray:
    dt = 1.0 / fs
    a = dt / (tau + dt)
    y = np.empty_like(x, dtype=np.float32)
    yy = 0.0
    for i, xx in enumerate(x):
        yy = yy + a * (float(xx) - yy)
        y[i] = yy
    return y

def fm_demod(iq: np.ndarray, prev: complex) -> tuple[np.ndarray, complex]:
    x = np.empty_like(iq)
    x[0] = prev
    x[1:] = iq[:-1]
    prev2 = iq[-1]
    fm = np.angle(iq * np.conj(x)).astype(np.float32)
    return fm, prev2

def simple_lowpass(y: np.ndarray, k: int = 8) -> np.ndarray:
    if k <= 1: return y
    ker = np.ones(k, dtype=np.float32) / k
    return np.convolve(y, ker, mode="same").astype(np.float32)

class CaptureWorker(threading.Thread):
    def __init__(self, freq_mhz: float, fs: int, n_samples: int, lna: int, vga: int, amp: int, out_q: queue.Queue):
        super().__init__(daemon=True)
        self.freq_mhz = freq_mhz
        self.fs = fs
        self.n_samples = n_samples
        self.lna = lna
        self.vga = vga
        self.amp = amp
        self.out_q = out_q
        self.stop = False

    def run(self):
        while not self.stop:
            with tempfile.TemporaryDirectory() as td:
                out_path = Path(td) / "iq.raw"
                cmd = [
                    HACKRF_TRANSFER,
                    "-f", str(int(self.freq_mhz * 1e6)),
                    "-s", str(self.fs),
                    "-l", str(self.lna),
                    "-g", str(self.vga),
                    "-a", "1" if self.amp else "0",
                    "-n", str(self.n_samples),
                    "-r", str(out_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode != 0 or not out_path.exists():
                    # si falla, espera un poco y reintenta
                    time.sleep(0.05)
                    continue
                raw = out_path.read_bytes()

            # entregar raw al consumidor
            try:
                self.out_q.put(raw, timeout=0.5)
            except queue.Full:
                pass

def main():
    FREQ_MHZ = 91.4
    FS = 2_400_000
    AUDIO_FS = 48_000
    DECIM = FS // AUDIO_FS  # 50

    LNA = 32
    VGA = 24
    AMP = 0

    chunk_seconds = 0.25  # sube a 0.30 si quieres menos overhead
    n_samples = int(FS * chunk_seconds)

    print(f"[FM] {FREQ_MHZ:.3f} MHz | fs={FS} | audio_fs={AUDIO_FS} | chunk={chunk_seconds}s")
    print("[FM] Ctrl+C para detener")

    qraw = queue.Queue(maxsize=6)
    worker = CaptureWorker(FREQ_MHZ, FS, n_samples, LNA, VGA, AMP, qraw)
    worker.start()

    sd.default.samplerate = AUDIO_FS
    sd.default.channels = 1

    prev = 0.0 + 0.0j

    with sd.OutputStream(dtype="float32", blocksize=2048) as stream:
        try:
            while True:
                raw = qraw.get()  # bloquea hasta tener bloque capturado

                d = np.frombuffer(raw, dtype=np.int8)
                if d.size % 2: d = d[:-1]
                i = d[0::2].astype(np.float32)
                q = d[1::2].astype(np.float32)
                iq = (i + 1j * q) / 128.0

                fm, prev = fm_demod(iq.astype(np.complex64), prev)

                fm = simple_lowpass(fm, k=8)
                audio = fm[::DECIM]
                audio = deemph(audio, AUDIO_FS, tau=75e-6)
                audio = np.tanh(audio * 8.0).astype(np.float32)

                stream.write(audio.reshape(-1, 1))

        except KeyboardInterrupt:
            pass
        finally:
            worker.stop = True

if __name__ == "__main__":
    main()
