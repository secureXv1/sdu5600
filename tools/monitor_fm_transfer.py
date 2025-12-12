from __future__ import annotations
import subprocess
import numpy as np
import sounddevice as sd
import shutil

HACKRF_TRANSFER = shutil.which("hackrf_transfer") or r"C:\Program Files\PothosSDR\bin\hackrf_transfer.exe"

def fir_lowpass(num_taps: int, cutoff_hz: float, fs: float) -> np.ndarray:
    fc = float(cutoff_hz) / float(fs)
    n = np.arange(num_taps) - (num_taps - 1) / 2.0
    h = 2 * fc * np.sinc(2 * fc * n)
    h *= np.hamming(num_taps)
    h /= np.sum(h)
    return h.astype(np.float32)

class WBFMMonitor:
    def __init__(
        self,
        freq_hz: int,
        fs: int = 2_400_000,        # 2.4 Msps
        audio_fs: int = 48_000,     # salida audio
        lna_gain: int = 24,
        vga_gain: int = 20,
        amp: int = 0,
        fm_cutoff_hz: float = 120_000.0,  # WBFM ~100-150 kHz
    ):
        self.freq_hz = int(freq_hz)
        self.fs = int(fs)
        self.decim = int(round(self.fs / audio_fs))
        self.audio_fs = int(round(self.fs / self.decim))  # debe quedar cerca de 48k

        self.lna_gain = int(lna_gain)
        self.vga_gain = int(vga_gain)
        self.amp = int(amp)

        # Filtro para la señal FM antes de demod
        self.lp_fm = fir_lowpass(129, fm_cutoff_hz, self.fs)
        self.zi_fm = np.zeros(len(self.lp_fm) - 1, dtype=np.float32)

        self.prev_iq = 0.0 + 0.0j

        # De-emphasis FM broadcast: 75 us (Américas). Si quieres 50 us, cambia tau.
        self.tau = 75e-6
        self.de_y = 0.0

        self.proc: subprocess.Popen | None = None

    def start(self):
        cmd = [
            HACKRF_TRANSFER,
            "-f", str(self.freq_hz),
            "-s", str(self.fs),
            "-l", str(self.lna_gain),
            "-g", str(self.vga_gain),
            "-a", "1" if self.amp else "0",
            "-B",
            "-r", "-",  # stdout
        ]

        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

        print(f"[FM] {self.freq_hz/1e6:.3f} MHz | fs={self.fs} | audio_fs≈{self.audio_fs} | decim={self.decim}")
        print("[FM] Ctrl+C para detener")

        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        with sd.OutputStream(dtype="float32", blocksize=1024, callback=self._audio_cb):
            try:
                while True:
                    sd.sleep(300)
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

    def _audio_cb(self, outdata, frames, _time, _status):
        if not self.proc or not self.proc.stdout:
            outdata[:] = 0
            return

        # pedimos suficientes IQ para producir "frames" de audio tras decimar
        need_iq = frames * self.decim * 2  # *2 (un poco extra)
        need_bytes = need_iq * 2           # 2 bytes por muestra compleja (I,Q)
        raw = self.proc.stdout.read(need_bytes)
        if not raw:
            outdata[:] = 0
            return

        d = np.frombuffer(raw, dtype=np.int8)
        if d.size < 4:
            outdata[:] = 0
            return
        if d.size % 2:
            d = d[:-1]

        i = d[0::2].astype(np.float32)
        q = d[1::2].astype(np.float32)
        iq = (i + 1j * q) / 128.0

        # Lowpass sobre IQ (para quedarnos con canal FM)
        iq_f, self.zi_fm = self._fir_stream_complex(iq, self.lp_fm, self.zi_fm)

        # Demod FM: diferencia de fase
        x = np.empty_like(iq_f)
        x[0] = self.prev_iq
        x[1:] = iq_f[:-1]
        self.prev_iq = iq_f[-1]
        fm = np.angle(iq_f * np.conj(x)).astype(np.float32)

        # Decimar a audio rate
        y = fm[:: self.decim]

        # De-emphasis
        y = self._deemph(y)

        # Audio gain + limiter suave
        y = np.tanh(y * 3.0).astype(np.float32)

        if y.size < frames:
            out = np.zeros(frames, dtype=np.float32)
            out[: y.size] = y
        else:
            out = y[:frames]

        outdata[:, 0] = out

    @staticmethod
    def _fir_stream_complex(iq: np.ndarray, h: np.ndarray, zi_real: np.ndarray):
        # filtramos real e imag por separado usando el mismo estado
        xr = iq.real.astype(np.float32, copy=False)
        xi = iq.imag.astype(np.float32, copy=False)

        y_r, zi_r = WBFMMonitor._fir_stream_float(xr, h, zi_real)
        y_i, zi_i = WBFMMonitor._fir_stream_float(xi, h, zi_r)  # re-usa estado encadenado
        return (y_r + 1j * y_i).astype(np.complex64), zi_i

    @staticmethod
    def _fir_stream_float(x: np.ndarray, h: np.ndarray, zi: np.ndarray):
        x2 = np.concatenate([zi, x])
        y = np.convolve(x2, h, mode="valid").astype(np.float32)
        new_zi = x2[-(len(h) - 1):].copy()
        return y, new_zi

    def _deemph(self, x: np.ndarray) -> np.ndarray:
        dt = 1.0 / float(self.audio_fs)
        alpha = dt / (self.tau + dt)
        y = np.empty_like(x, dtype=np.float32)
        yy = float(self.de_y)
        for k, xx in enumerate(x):
            yy = yy + alpha * (float(xx) - yy)
            y[k] = yy
        self.de_y = yy
        return y

if __name__ == "__main__":
    # Cambia aquí la emisora
    FREQ_MHZ = 91.4
    mon = WBFMMonitor(freq_hz=int(FREQ_MHZ * 1e6), fs=2_400_000, lna_gain=24, vga_gain=20, amp=0)
    mon.start()
