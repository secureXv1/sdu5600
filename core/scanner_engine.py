from __future__ import annotations

import time
import threading
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from core.recorder import WavRecorder

MONTHS = ["ENE","FEB","MAR","ABR","MAY","JUN","JUL","AGO","SEP","OCT","NOV","DIC"]

def ts_name(dt: datetime) -> str:
    dd = f"{dt.day:02d}"
    mon = MONTHS[dt.month - 1]
    yy = f"{dt.year % 100:02d}"
    hh = f"{dt.hour:02d}"
    mm = f"{dt.minute:02d}"
    return f"{dd}{mon}{yy}_{hh}-{mm}"

def sanitize_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "SCAN"

def fmt_freq_mhz(mhz: float) -> str:
    return f"{float(mhz):.6f}".rstrip("0").rstrip(".")

@dataclass
class ScanStatus:
    bank_kind: str      # "range" | "freq"
    bank_name: str
    state: str          # "SCAN" | "HOLD" | "IDLE"
    freq_mhz: float
    level_db: float

class ScannerEngine:
    def __init__(self, manager, store, recordings_root="recordings"):
        self.manager = manager
        self.store = store
        self.recordings_root = Path(recordings_root)

        self._t: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self.is_running = False

        # grabación (ICR clip fijo)
        self._rec: Optional[WavRecorder] = None
        self._rec_done = threading.Event()
        self._armed = False
        self._armed_path: Optional[Path] = None
        self._armed_until = 0.0

        self._on_status: Optional[Callable[[ScanStatus], None]] = None
        self.driver = None
        self.kind_filter = "ALL"

    # ---------- API ----------
    def start(self, driver, kind_filter: str = "ALL", on_status: Optional[Callable[[ScanStatus], None]] = None):
        if self.is_running:
            return
        self.driver = driver
        self.kind_filter = (kind_filter or "ALL").upper().strip()
        self._on_status = on_status

        self._stop_evt.clear()
        self.is_running = True
        self.manager.set_on_audio(self._on_audio)

        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self):
        self._stop_evt.set()
        self.is_running = False

        self._stop_recording()
        try:
            self.manager.stop_audio()
        except Exception:
            pass
        try:
            self.manager.set_on_audio(None)
        except Exception:
            pass

        try:
            if self._t and self._t.is_alive():
                self._t.join(timeout=1.5)
        except Exception:
            pass
        self._t = None

    # ---------- status ----------
    def _emit_status(self, bank_kind: str, bank_name: str, state: str, freq_mhz: float, level_db: float):
        cb = self._on_status
        if cb is None:
            return
        try:
            cb(ScanStatus(
                bank_kind=bank_kind,
                bank_name=bank_name,
                state=state,
                freq_mhz=float(freq_mhz),
                level_db=float(level_db),
            ))
        except Exception:
            pass

    # ---------- grabación ----------
    def _arm_recording(self, folder: Path, fname: str, hold_seconds: int):
        self._stop_recording()
        folder.mkdir(parents=True, exist_ok=True)

        self._armed = True
        self._armed_path = folder / fname
        self._armed_until = time.time() + float(hold_seconds)
        self._rec_done.clear()

    def _stop_recording(self):
        self._armed = False
        self._armed_path = None
        self._armed_until = 0.0

        if self._rec:
            try:
                self._rec.stop()
            except Exception:
                pass
        self._rec = None

    def _on_audio(self, audio_float32):
        # si no estamos armados / grabando, no hacemos nada
        if self._rec is None and not self._armed:
            return

        now = time.time()

        # crea WAV SOLO al primer audio real (evita archivos vacíos)
        if self._rec is None and self._armed:
            try:
                self._rec = WavRecorder(str(self._armed_path), sample_rate=48_000)
                self._rec.start()
            except Exception:
                self._stop_recording()
                self._rec_done.set()
                return

        # escribe audio
        try:
            self._rec.write_float32(audio_float32)
        except Exception:
            self._stop_recording()
            self._rec_done.set()
            return

        # termina clip fijo
        if now >= self._armed_until:
            self._stop_recording()
            self._rec_done.set()

    # ---------- nivel (squelch) ----------
    def _level_db(self, tuned_hz: float) -> float:
        # Smeter si existe
        try:
            if hasattr(self.driver, "get_smeter"):
                return float(self.driver.get_smeter())
        except Exception:
            pass

        # Peak cerca de la frecuencia sintonizada (evita disparos por ruido lejano)
        if hasattr(self.driver, "get_spectrum"):
            try:
                freqs, levels = self.driver.get_spectrum()
                if freqs is None or levels is None or len(freqs) == 0:
                    return -999.0

                import numpy as np
                freqs_np = np.asarray(freqs, dtype=float)
                levels_np = np.asarray(levels, dtype=float)

                f0 = float(tuned_hz)
                idx = int(np.argmin(np.abs(freqs_np - f0)))

                bw = 12_500.0  # +/- 12.5 kHz
                df = abs(freqs_np[1] - freqs_np[0]) if len(freqs_np) >= 2 else 1.0
                n = max(2, int(bw / max(df, 1.0)))

                lo = max(0, idx - n)
                hi = min(len(levels_np), idx + n + 1)
                return float(levels_np[lo:hi].max())
            except Exception:
                return -999.0

        return -999.0

    # ---------- bancos ----------
    def _active_range_banks(self):
        return [b for b in self.store.list_banks("range") if b.get("active")]

    def _active_freq_banks(self):
        return [b for b in self.store.list_banks("freq") if b.get("active")]

    # ---------- loop ----------
    def _loop(self):
        s = self.store.settings()
        squelch_db = float(s["squelch_db"])
        settle_s = float(s["settle_ms"]) / 1000.0
        hold_s = float(max(1, int(s["hold_seconds"])))  # clip fijo en segundos
        loop_forever = bool(s["loop"])

        session_ts = ts_name(datetime.now())

        try:
            while not self._stop_evt.is_set():
                did_any = False

                banks_range = self._active_range_banks() if self.kind_filter in ("ALL","RANGE") else []
                banks_freq  = self._active_freq_banks()  if self.kind_filter in ("ALL","FREQ")  else []

                if not banks_range and not banks_freq:
                    break

                # --- FRECUENCIAS ---
                for bank in banks_freq:
                    if self._stop_evt.is_set():
                        break
                    did_any = True
                    bank_name = sanitize_name(bank.get("name", "FREQ"))
                    bank_folder = self.recordings_root / f"{bank_name}_{session_ts}"

                    items = bank.get("items") or bank.get("channels") or []
                    for it in items:
                        if self._stop_evt.is_set():
                            break
                        f = float(it.get("freq_mhz"))
                        mode = (it.get("mode") or "NFM").upper().strip()
                        self._scan_single_freq("freq", bank_name, bank_folder, f, mode, squelch_db, settle_s, hold_s)

                # --- RANGOS ---
                for bank in banks_range:
                    if self._stop_evt.is_set():
                        break
                    did_any = True
                    bank_name = sanitize_name(bank.get("name", "RANGE"))
                    bank_folder = self.recordings_root / f"{bank_name}_{session_ts}"

                    mode = (bank.get("mode") or "NFM").upper().strip()
                    r = bank.get("range") or {}
                    start = float(r["start_mhz"])
                    stop = float(r["stop_mhz"])
                    step_mhz = float(r["ts_khz"]) / 1000.0

                    f = start
                    while f <= stop and not self._stop_evt.is_set():
                        self._scan_single_freq("range", bank_name, bank_folder, f, mode, squelch_db, settle_s, hold_s)
                        f = f + step_mhz

                if not loop_forever or not did_any:
                    break

        finally:
            self._stop_recording()
            try:
                self.manager.stop_audio()
            except Exception:
                pass
            try:
                self.manager.set_on_audio(None)
            except Exception:
                pass
            self.is_running = False

    def _scan_single_freq(self, bank_kind, bank_name, bank_folder, freq_mhz, mode, squelch_db, settle_s, hold_s):
        f = float(freq_mhz)
        mode = (mode or "").upper().strip()

        # sintoniza
        try:
            if hasattr(self.driver, "connect"):
                self.driver.connect()
        except Exception:
            pass

        if hasattr(self.driver, "set_center_freq"):
            try:
                self.driver.set_center_freq(f * 1e6)
            except Exception:
                return

        # UI: “voy por aquí”
        self._emit_status(bank_kind, bank_name, "SCAN", f, -999.0)

        # estabiliza
        if settle_s > 0:
            time.sleep(settle_s)

        # nivel
        level = self._level_db(tuned_hz=f * 1e6)
        self._emit_status(bank_kind, bank_name, "SCAN", f, level)

        # si no supera squelch, sigue
        if level < squelch_db:
            return

        # nombre del clip
        dt = datetime.now()
        ts = ts_name(dt)
        fstr = fmt_freq_mhz(f)
        fname = f"{ts}_{fstr}_{mode}.wav"

        # arma grabación (sin wav vacío)
        self._arm_recording(bank_folder, fname, hold_seconds=int(hold_s))

        # inicia audio
        try:
            self.manager.start_audio(self.driver, f, mode)
        except Exception:
            self._stop_recording()
            return

        # HOLD fijo: espera a que termine clip o usuario detenga
        self._emit_status(bank_kind, bank_name, "HOLD", f, level)
        t0 = time.time()

        while not self._stop_evt.is_set():
            if self._rec_done.is_set():
                break

            # si no llegó audio en 0.8s, aborta (no wav vacío)
            if self._rec is None and self._armed and (time.time() - t0) > 0.8:
                self._stop_recording()
                break

            time.sleep(0.03)

        try:
            self.manager.stop_audio()
        except Exception:
            pass
