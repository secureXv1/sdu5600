from __future__ import annotations
import time
import subprocess
import tempfile
from pathlib import Path
import numpy as np
import sounddevice as sd
import shutil

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
    # angle(x[n]*conj(x[n-1]))
    x = np.empty_like(iq)
    x[0] = prev
    x[1:] = iq[:-1]
    prev2 = iq[-1]
    fm = np.angle(iq * np.conj(x)).astype(np.float32)
    return fm, prev2

def simple_lowpass(y: np.ndarray, k: int = 6) -> np.ndarray:
    # suavizado simple (promedio móvil) para quitar aspereza
    if k <= 1:
        return y
    kernel = np.ones(k, dtype=np.float32) / k
    return np.convolve(y, kernel, mode="same").astype(np.float32)

def main():
    FREQ_MHZ = 91.4   # cambia aquí
    FS = 2_400_000    # HackRF
    AUDIO_FS = 48_000
    DECIM = FS // AUDIO_FS  # 50

    LNA = 32
    VGA = 24
    AMP = 0

    # capturamos trozos de ~0.2s
    chunk_seconds = 0.20
    n_samples = int(FS * chunk_seconds)  # hackrf_transfer -n cuenta muestras (IQ compleja)

    print(f"[FM] {FREQ_MHZ:.3f} MHz | fs={FS} | audio_fs={AUDIO_FS} | chunk={chunk_seconds}s | n={n_samples}")
    print("[FM] Ctrl+C para detener")

    sd.default.samplerate = AUDIO_FS
    sd.default.channels = 1

    prev = 0.0 + 0.0j

    with sd.OutputStream(dtype="float32", blocksize=2048) as stream:
        try:
            while True:
                with tempfile.TemporaryDirectory() as td:
                    out_path = Path(td) / "iq.raw"

                    cmd = [
                        HACKRF_TRANSFER,
                        "-f", str(int(FREQ_MHZ * 1e6)),
                        "-s", str(FS),
                        "-l", str(LNA),
                        "-g", str(VGA),
                        "-a", "1" if AMP else "0",
                        "-n", str(n_samples),
                        "-r", str(out_path),
                    ]

                    proc = subprocess.run(cmd, capture_output=True, text=True)

                    if proc.returncode != 0 or not out_path.exists():
                        print("[FM] hackrf_transfer falló:", (proc.stderr or proc.stdout).strip())
                        time.sleep(0.2)
                        continue

                    raw = out_path.read_bytes()

                d = np.frombuffer(raw, dtype=np.int8)
                if d.size < 4:
                    continue
                if d.size % 2:
                    d = d[:-1]

                i = d[0::2].astype(np.float32)
                q = d[1::2].astype(np.float32)
                iq = (i + 1j * q) / 128.0

                fm, prev = fm_demod(iq.astype(np.complex64), prev)

                # Filtrado + decimación a audio
                fm = simple_lowpass(fm, k=8)
                audio = fm[::DECIM]

                # De-emphasis + ganancia + limitador
                audio = deemph(audio, AUDIO_FS, tau=75e-6)
                audio = np.tanh(audio * 8.0).astype(np.float32)

                stream.write(audio.reshape(-1, 1))

        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
