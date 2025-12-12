from __future__ import annotations
import subprocess
import numpy as np
import sounddevice as sd
import shutil
import sys
import math

HACKRF_TRANSFER = shutil.which("hackrf_transfer") or r"C:\Program Files\PothosSDR\bin\hackrf_transfer.exe"


def fir_lowpass(num_taps: int, cutoff_hz: float, fs: float) -> np.ndarray:
    fc = float(cutoff_hz) / float(fs)  # 0..0.5
    n = np.arange(num_taps) - (num_taps - 1) / 2.0
    h = 2 * fc * np.sinc(2 * fc * n)
    h *= np.hamming(num_taps)
    h /= np.sum(h)
    return h.astype(np.float32)


class NFMTransferMonitor:
    def __init__(
        self,
        freq_hz: int,
        fs: int = 2_000_000,
        audio_fs: int = 48_000,
        lna_gain: int = 16,
        vga_gain: int = 16,
        amp: int = 0,
        cutoff_hz: float = 12_000.0,
    ):
        self.freq_hz = int(freq_hz)
        self.fs = int(fs)
        self.decim = int(round(self.fs / audio_fs))
        self.audio_fs = int(round(self.fs / self.decim))

        self.lna_gain = int(lna_gain)
        self.vga_gain = int(vga_gain)
        self.amp = int(amp)

        self.lp = fir_lowpass(129, cutoff_hz, self.fs)
        self.zi = np.zeros(len(self.lp) - 1, dtype=np.float32)

        self.prev_iq = 0.0 + 0.0j

        # De-emphasis 75us
        self.tau = 75e-6
        self.de_y = 0.0

        self.proc: subprocess.Popen | None = None
        self.buf = bytearray()

    def start(self):
        if not HACKRF_TRANSFER or not shutil.which("hackrf_transfer"):
            # igual intentamos con la ruta fija
            pass

        cmd = [
            HACKRF_TRANSFER,
            "-f", str(self.freq_hz),
            "-s", str(self.fs),
            "-l", str(self.lna_gain),
            "-g", str(self.vga_gain),
            "-a", "1" if self.amp else "0",
            "-B",  # binary to stdout (recomendado)
            "-r", "-",  # stdout
        ]

        # Nota: en algunas builds -r - funciona; si no, quitamos -r y usamos -o? (me pasas stderr)
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        print(f"[NFM] freq={self.freq_hz/1e6:.6f} MHz | fs={self.fs} | audio_fs={self.audio_fs} | decim={self.decim}")
        print("[NFM] Ctrl+C para detener")

        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        with sd.OutputStream(dtype="float32", blocksize=1024, callback=self._audio_cb):
            try:
                while True:
                    sd.sleep(200)
                    self._drain_stderr_nonblocking()
            except KeyboardInterrupt:
                pass
            finally:
                self.stop()

    def stop(self):
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

    def _drain_stderr_nonblocking(self):
        if not self.proc or not self.proc.stderr:
            return
        try:
            data = self.proc.stderr.read1(4096)
            if data:
                txt = data.decode(errors="ignore").strip()
                if txt:
                    # imprime mensajes útiles de hackrf_transfer
                    print(txt)
        except Exception:
            pass

    def _audio_cb(self, outdata, frames, _time, _status):
        if not self.proc or not self.proc.stdout:
            outdata[:] = 0
            return

        # necesitamos suficientes bytes para procesar
        # hackrf_transfer entrega int8 intercalado I,Q: 2 bytes por muestra compleja
        need_bytes = frames * self.decim * 2 * 4  # un poco extra para FIR+FFT
        try:
            chunk = self.proc.stdout.read(need_bytes)
        except Exception:
            outdata[:] = 0
            return

        if not chunk:
            outdata[:] = 0
            return

        # Convertir a IQ compleja
        data = np.frombuffer(chunk, dtype=np.int8)
        if data.size < 4:
            outdata[:] = 0
            return
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

        # FIR lowpass (streaming)
        fm2 = np.concatenate([self.zi, fm])
        y = np.convolve(fm2, self.lp, mode="valid").astype(np.float32)
        self.zi = fm2[-(len(self.lp) - 1):].copy()

        # Decimar a audio rate
        y = y[:: self.decim]

        # De-emphasis
        y = self._deemph(y)

        # Limitador suave
        y = np.tanh(y * 2.5).astype(np.float32)

        # Rellenar outdata
        if y.size < frames:
            out = np.zeros(frames, dtype=np.float32)
            out[: y.size] = y
        else:
            out = y[:frames]

        outdata[:, 0] = out

    def _deemph(self, x: np.ndarray) -> np.ndarray:
        dt = 1.0 / float(self.audio_fs)
        alpha = dt / (self.tau + dt)
        y = np.empty_like(x, dtype=np.float32)
        yy = float(self.de_y)
        for idx, xx in enumerate(x):
            yy = yy + alpha * (float(xx) - yy)
            y[idx] = yy
        self.de_y = yy
        return y


if __name__ == "__main__":
    # Cambia esta frecuencia a una portadora NFM real
    FREQ_HZ = 148_350_000

    mon = NFMTransferMonitor(
        freq_hz=FREQ_HZ,
        fs=1_920_000,
        lna_gain=32,
        vga_gain=32,
        amp=0,
        cutoff_hz=6000.0,
    )
    mon.start()
