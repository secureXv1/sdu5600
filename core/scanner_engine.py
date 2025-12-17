from __future__ import annotations

import re
import time
import threading
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
    bank_kind: str        # "range" | "freq"
    bank_name: str
    state: str            # "SCAN" | "HOLD" | "IDLE"
    freq_mhz: float
    level_db: float

class ScannerEngine:
    def __init__(self, manager, store, recordings_root="recordings"):
        self.manager = manager
        self.store = store
        self.recordings_root = Path(recordings_root)

        self._t: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self.is_running: bool = False

        self._rec: Optional[WavRecorder] = None
        self._rec_freq_mhz: Optional[float] = None
        self._last_above_ts: float = 0.0

        self._on_status: Optional[Callable[[ScanStatus], None]] = None
        self._rec_done = threading.Event()
        self._armed = False
        self._armed_path = None
        self._armed_until = 0.0


    def start(self, driver, kind_filter: str = "ALL", on_status: Optional[Callable[[ScanStatus], None]] = None):
        if self.is_running:
            return
        self.driver = driver
        self.kind_filter = (kind_filter or "ALL").upper().strip()
        self._on_status = on_status

        self._stop_evt.clear()
        self.is_running = True
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

    def _on_audio(self, audio_float32):
        # Si no estamos grabando ni armados -> nada
        if self._rec is None and not self._armed:
            return

        now = time.time()

        # Si estamos armados pero aún no abrimos archivo, lo abrimos al primer audio real
        if self._rec is None and self._armed:
            try:
                self._rec = WavRecorder(str(self._armed_path), sample_rate=48000)
                self._rec.start()
            except Exception:
                self._armed = False
                self._armed_path = None
                self._rec = None
                self._rec_done.set()
                return

        # Escribimos audio
        try:
            self._rec.write_float32(audio_float32)
        except Exception:
            self._stop_recording()
            self._rec_done.set()
            return

        # ¿Se cumplió el tiempo fijo? (ICR-style: clip fijo y seguimos)
        if now >= self._armed_until:
            self._stop_recording()
            self._rec_done.set()


    def _arm_recording(self, folder: Path, fname: str, hold_seconds: int):
        # NO crear archivo aún: lo creamos cuando llegue el primer audio (evita wav vacío)
        self._stop_recording()
        folder.mkdir(parents=True, exist_ok=True)

        self._armed = True
        self._armed_path = (folder / fname)
        self._armed_until = time.time() + float(hold_seconds)
        self._rec_done.clear()


    def _stop_recording(self):
        # limpiar armado
        self._armed = False
        self._armed_path = None
        self._armed_until = 0.0

        if self._rec:
            try:
                self._rec.stop()
            except Exception:
                pass
        self._rec = None

    def _active_range_banks(self):
        return [b for b in self.store.list_banks("range") if b.get("active")]

    def _active_freq_banks(self):
        return [b for b in self.store.list_banks("freq") if b.get("active")]

    def _level_db(self, tuned_hz: float) -> float:
        # 1) Smeter si existe
        try:
            if hasattr(self.driver, "get_smeter"):
                return float(self.driver.get_smeter())
        except Exception:
            pass

        # 2) Espectro: peak cerca de la frecuencia sintonizada (evita disparos por ruido lejano)
        if hasattr(self.driver, "get_spectrum"):
            try:
                freqs, levels = self.driver.get_spectrum()
                if freqs is None or levels is None or len(freqs) == 0:
                    return -999.0
                try:
                    import numpy as np
                    freqs_np = np.asarray(freqs, dtype=float)
                    levels_np = np.asarray(levels, dtype=float)
                    f0 = float(tuned_hz)
                    idx = int(np.argmin(np.abs(freqs_np - f0)))

                    bw = 12_500.0  # +/- 12.5 kHz
                    if len(freqs_np) >= 2:
                        df = abs(freqs_np[1] - freqs_np[0])
                        n = max(2, int(bw / df))
                    else:
                        n = 8

                    lo = max(0, idx - n)
                    hi = min(len(levels_np), idx + n + 1)
                    return float(levels_np[lo:hi].max())
                except Exception:
                    return float(max(levels))
            except Exception:
                return -999.0

        return -999.0

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

    def _loop(self):
        s = self.store.settings()
        squelch_db = float(s["squelch_db"])
        settle_s = float(s["settle_ms"]) / 1000.0
        hold_s = float(max(0, int(s["hold_seconds"])))  # hold_seconds = duración del clip
        loop_forever = bool(s["loop"])

        self.manager.set_on_audio(self._on_audio)

        session_ts = ts_name(datetime.now())

        try:
            while not self._stop_evt.is_set():
                did_any = False

                banks_range = self._active_range_banks() if self.kind_filter in ("ALL", "RANGE") else []
                banks_freq  = self._active_freq_banks()  if self.kind_filter in ("ALL", "FREQ")  else []

                if not banks_range and not banks_freq:
                    break

                # ---- FRECUENCIAS ----
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

                # ---- RANGOS ----
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

                    rg = bank.get("rf_gain") or {}
                    if hasattr(self.driver, "set_rf_gain"):
                        try:
                            self.driver.set_rf_gain(
                                lna_db=int(rg.get("lna_db", 32)),
                                vga_db=int(rg.get("vga_db", 20)),
                                amp=bool(rg.get("amp", False)),
                            )
                        except Exception:
                            pass

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

        # deja estabilizar (muy importante para RSSI)
        if settle_s > 0:
            time.sleep(settle_s)

        level = self._level_db(tuned_hz=f * 1e6)
        self._emit_status(bank_kind, bank_name, "SCAN", f, level)

        # ¿supera squelch? => capturar clip fijo (ICR-style)
        if level < squelch_db:
            return

        # nombre del archivo: 17DIC25_14-48_98.275_FM.wav
        dt = datetime.now()
        ts = ts_name(dt)
        fstr = fmt_freq_mhz(f)
        fname = f"{ts}_{fstr}_{mode}.wav"

        # arma grabación (NO crea wav hasta que llegue audio real)
        self._arm_recording(bank_folder, fname, hold_seconds=int(hold_s))

        # inicia audio
        try:
            self.manager.start_audio(self.driver, f, mode)
        except Exception:
            self._stop_recording()
            return

        # espera a que termine el clip o el usuario detenga
        t0 = time.time()
        self._emit_status(bank_kind, bank_name, "HOLD", f, level)

        while not self._stop_evt.is_set():
            if self._rec_done.is_set():
                break

            # si en 0.8s no llegó audio -> aborta sin wav vacío
            if self._rec is None and self._armed and (time.time() - t0) > 0.8:
                self._stop_recording()
                break

            time.sleep(0.03)

        # detener audio para seguir escaneo limpio
        try:
            self.manager.stop_audio()
        except Exception:
            pass
