from __future__ import annotations
import subprocess, threading, queue, time, shutil
import numpy as np
import sounddevice as sd

HACKRF_TRANSFER = shutil.which("hackrf_transfer") or r"C:\Program Files\PothosSDR\bin\hackrf_transfer.exe"

def fir_lowpass(num_taps: int, cutoff_hz: float, fs: float) -> np.ndarray:
    fc = float(cutoff_hz) / float(fs)
    n = np.arange(num_taps) - (num_taps - 1) / 2.0
    h = 2 * fc * np.sinc(2 * fc * n)
    h *= np.hamming(num_taps)
    h /= np.sum(h)
    return h.astype(np.float32)

class WBFM:
    def __init__(self, freq_mhz: float):
        self.freq_hz = int(freq_mhz * 1e6)

        self.fs = 2_400_000          # HackRF sample rate
        self.audio_fs = 48_000
        self.decim = self.fs // self.audio_fs  # 50 exacto

        self.lna = 32
        self.vga = 24
        self.amp = 0

        # Filtramos DESPUÉS de demod (más simple y robusto)
        self.lp = fir_lowpass(129, 16_000.0, self.fs)  # audio baseband (16k)
        self.zi = np.zeros(len(self.lp) - 1, dtype=np.float32)

        self.prev = 0.0 + 0.0j

        # deemphasis 75us (Américas)
        self.tau = 75e-6
        self.de_y = 0.0

        self.q = queue.Queue(maxsize=40)  # buffer audio
        self.stop_flag = threading.Event()
        self.proc: subprocess.Popen | None = None

    def start(self):
        cmd = [
            HACKRF_TRANSFER,
            "-f", str(self.freq_hz),
            "-s", str(self.fs),
            "-l", str(self.lna),
            "-g", str(self.vga),
            "-a", "1" if self.amp else "0",
            "-B",
            "-r", "-",  # stdout
        ]

        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

        t = threading.Thread(target=self._rx_thread, daemon=True)
        t.start()

        print(f"[FM] {self.freq_hz/1e6:.3f} MHz | fs={self.fs} | audio_fs={self.audio_fs} | decim={self.decim}")
        print("[FM] Ctrl+C para detener")

        sd.default.samplerate = self.audio_fs
        sd.default.channels = 1

        with sd.OutputStream(dtype="float32", blocksize=1024, callback=self._audio_cb):
            try:
                while True:
                    time.sleep(0.25)
                    self._drain_stderr()
            except KeyboardInterrupt:
                pass
            finally:
                self.stop_flag.set()
                if self.proc:
                    try: self.proc.kill()
                    except Exception: pass

    def _drain_stderr(self):
        if not self.proc or not self.proc.stderr:
            return
        try:
            data = self.proc.stderr.read1(4096)
            if data:
                txt = data.decode(errors="ignore").strip()
                if txt:
                    print(txt)
        except Exception:
            pass

    def _rx_thread(self):
        assert self.proc and self.proc.stdout
        stdout = self.proc.stdout

        # leemos por bloques grandes para ser eficientes
        # 2 bytes por muestra compleja (I,Q). Tomamos ~0.1s de IQ:
        bytes_per_sec = self.fs * 2
        chunk_bytes = int(bytes_per_sec * 0.1)

        while not self.stop_flag.is_set():
            raw = stdout.read(chunk_bytes)
            if not raw:
                break

            d = np.frombuffer(raw, dtype=np.int8)
            if d.size < 4:
                continue
            if d.size % 2:
                d = d[:-1]

            i = d[0::2].astype(np.float32)
            q = d[1::2].astype(np.float32)
            iq = (i + 1j * q) / 128.0

            # FM demod: angle(x[n]*conj(x[n-1]))
            x = np.empty_like(iq)
            x[0] = self.prev
            x[1:] = iq[:-1]
            self.prev = iq[-1]
            fm = np.angle(iq * np.conj(x)).astype(np.float32)

            # Lowpass (a fs) + decimar
            fm2 = np.concatenate([self.zi, fm])
            y = np.convolve(fm2, self.lp, mode="valid").astype(np.float32)
            self.zi = fm2[-(len(self.lp) - 1):].copy()

            y = y[:: self.decim]  # -> ~48k

            # deemphasis
            y = self._deemph(y)

            # gain + limiter
            y = np.tanh(y * 8.0).astype(np.float32)

            # encolar (no bloquear)
            try:
                self.q.put_nowait(y)
            except queue.Full:
                pass

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

    def _audio_cb(self, outdata, frames, _time, _status):
        # sacar del buffer; si no hay, silencio
        out = np.zeros(frames, dtype=np.float32)
        filled = 0

        while filled < frames:
            try:
                chunk = self.q.get_nowait()
            except queue.Empty:
                break

            n = min(frames - filled, chunk.size)
            out[filled:filled+n] = chunk[:n]
            filled += n

            # si sobró, reinsertar el resto al frente (simple: volver a meter)
            rest = chunk[n:]
            if rest.size:
                try:
                    self.q.put_nowait(rest)
                except queue.Full:
                    pass
                break

        outdata[:, 0] = out

if __name__ == "__main__":
    # Cambia aquí la emisora
    FM_MHZ = 91.4
    WBFM(FM_MHZ).start()
