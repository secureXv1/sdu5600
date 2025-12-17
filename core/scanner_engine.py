from __future__ import annotations
import time, threading
from datetime import datetime
from pathlib import Path

from core.recorder import WavRecorder

MONTHS = ["ENE","FEB","MAR","ABR","MAY","JUN","JUL","AGO","SEP","OCT","NOV","DIC"]

def ts_name(dt: datetime):
    dd = f"{dt.day:02d}"
    mon = MONTHS[dt.month-1]
    yy = f"{dt.year%100:02d}"
    hh = f"{dt.hour:02d}"
    mm = f"{dt.minute:02d}"
    return f"{dd}{mon}{yy}_{hh}-{mm}"

class ScannerEngine:
    def __init__(self, manager, store, recordings_root="recordings"):
        self.manager = manager
        self.store = store
        self.recordings_root = Path(recordings_root)

        self._t = None
        self._stop = threading.Event()
        self.is_running = False

        self._rec = None
        self._rec_until = 0.0

    def start(self, driver):
        if self.is_running:
            return
        self.driver = driver
        self._stop.clear()
        self.is_running = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        self.is_running = False
        self._stop_recording()
        try:
            self.manager.stop_audio()
        except Exception:
            pass

    # --- recording tap ---
    def _on_audio(self, audio_float32):
        if self._rec is None:
            return
        if time.time() > self._rec_until:
            self._stop_recording()
            return
        self._rec.write_float32(audio_float32)

    def _start_recording(self, folder: Path, fname: str, hold_seconds: int):
        self._stop_recording()
        self._rec = WavRecorder(str(folder / fname), sample_rate=48000)
        self._rec.start()
        self._rec_until = time.time() + float(hold_seconds)

    def _stop_recording(self):
        if self._rec:
            try:
                self._rec.stop()
            except Exception:
                pass
        self._rec = None
        self._rec_until = 0.0

    # --- scan list ---
    def _active_range_banks(self):
        banks = []
        for b in self.store.list_banks("range"):
            if b.get("active"):
                banks.append(b)
        return banks

    def _power_db(self):
        # Usa el espectro del driver (si lo tienes en tu UI/driver).
        if hasattr(self.driver, "get_spectrum"):
            freqs, levels = self.driver.get_spectrum()
            if levels is not None and len(levels):
                return float(max(levels))
        return -999.0

    def _loop(self):
        s = self.store.settings()
        squelch_db = s["squelch_db"]
        settle = s["settle_ms"] / 1000.0
        hold = int(s["hold_seconds"])
        loop_forever = bool(s["loop"])

        # carpeta por sesión
        session = datetime.now()
        session_folder = self.recordings_root / f"{session.strftime('%Y%m%d_%H%M%S')}_SCAN"
        session_folder.mkdir(parents=True, exist_ok=True)

        # engancha callback de grabación
        self.manager.set_on_audio(self._on_audio)

        banks = self._active_range_banks()
        if not banks:
            self.is_running = False
            return

        # Program Link estilo ICR-30: escanea rangos activos secuencialmente :contentReference[oaicite:3]{index=3}
        while not self._stop.is_set():
            did_any = False
            for bank in banks:
                if self._stop.is_set():
                    break

                name = bank.get("name","RANGE")
                mode = (bank.get("mode") or "NFM").upper().strip()
                r = bank.get("range") or {}
                start = float(r["start_mhz"]); stop = float(r["stop_mhz"])
                step_mhz = float(r["ts_khz"]) / 1000.0

                rg = bank.get("rf_gain") or {}
                if hasattr(self.driver, "set_rf_gain"):
                    self.driver.set_rf_gain(
                        lna_db=int(rg.get("lna_db", 32)),
                        vga_db=int(rg.get("vga_db", 20)),
                        amp=bool(rg.get("amp", False)),
                    )

                f = start
                while f <= stop + 1e-9 and not self._stop.is_set():
                    did_any = True
                    # sintoniza
                    if hasattr(self.driver, "connect"):
                        self.driver.connect()
                    if hasattr(self.driver, "set_center_freq"):
                        self.driver.set_center_freq(f * 1e6)

                    time.sleep(settle)

                    p = self._power_db()
                    if p >= squelch_db:
                        # “parar” y grabar 5s
                        ts = ts_name(datetime.now())
                        fstr = f"{f:.6f}".rstrip("0").rstrip(".")
                        fname = f"{ts}_{fstr}_{mode}.wav"  # sin ":" para Windows
                        folder = session_folder / name
                        folder.mkdir(parents=True, exist_ok=True)

                        self._start_recording(folder, fname, hold_seconds=hold)

                        # inicia audio (mientras grabamos)
                        try:
                            self.manager.start_audio(self.driver, f, mode)
                        except Exception:
                            self._stop_recording()

                        # espera hasta que termine la grabación (hold)
                        while self._rec is not None and not self._stop.is_set():
                            time.sleep(0.05)

                        try:
                            self.manager.stop_audio()
                        except Exception:
                            pass

                    f += step_mhz

            if not loop_forever or not did_any:
                break

        self.is_running = False
