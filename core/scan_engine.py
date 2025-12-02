# core/scan_engine.py
"""
ScanEngine genérico para cualquier RadioDriver.

Funciona así:
- Recibe un driver que implementa:
    set_frequency(hz: float)
    get_smeter() -> float   (nivel en dB aprox)
- Escanea de start_hz a end_hz con step_hz.
- Si el nivel >= squelch_db → entra en estado HOLD en esa frecuencia.
- Mientras la señal siga por encima del squelch, mantiene HOLD.
- Cuando la señal cae y pasa hold_ms sin actividad → reanuda SCAN.

Diseño:
- Es completamente independiente de Qt.
- La GUI debe llamar periódicamente a step_once() con un QTimer.
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class ScanConfig:
    start_hz: float
    end_hz: float
    step_hz: float
    squelch_db: float = -80.0
    hold_ms: int = 2000  # cuánto tiempo mantener HOLD después de que caiga la señal


class ScanEngine:
    """
    Motor de escaneo simple:
      estados: IDLE, SCAN, HOLD

    Uso típico:
      engine = ScanEngine(driver, cfg)
      engine.start()
      # en un QTimer (cada 200 ms):
      state, freq_hz, level_db = engine.step_once()
    """

    def __init__(self, driver, cfg: ScanConfig):
        self.driver = driver
        self.cfg = cfg

        self.state: str = "IDLE"
        self.running: bool = False
        self.current_hz: float = cfg.start_hz
        self._last_level_db: float = -120.0
        self._last_signal_ts: Optional[float] = None  # ms desde monotonic

    # -----------------------------------------------------
    # Control
    # -----------------------------------------------------
    def start(self):
        self.running = True
        self.state = "SCAN"
        self.current_hz = self.cfg.start_hz
        self._last_signal_ts = None

    def stop(self):
        self.running = False
        self.state = "IDLE"

    def is_running(self) -> bool:
        return self.running

    # -----------------------------------------------------
    # Paso principal (llamar desde un QTimer)
    # -----------------------------------------------------
    def step_once(self) -> Tuple[str, float, float]:
        """
        Realiza un paso de escaneo / hold.

        Devuelve:
          (state, freq_hz, level_db)
        """
        if not self.running:
            return self.state, self.current_hz, self._last_level_db

        now_ms = time.monotonic() * 1000.0

        if self.state == "SCAN":
            # Ajustamos frecuencia y medimos nivel
            self.driver.set_frequency(self.current_hz)
            level = self._safe_get_smeter()
            self._last_level_db = level

            if level >= self.cfg.squelch_db:
                # Encontramos señal → HOLD
                self.state = "HOLD"
                self._last_signal_ts = now_ms
                return self.state, self.current_hz, level

            # Sin señal → avanzamos
            self._advance_freq()
            return self.state, self.current_hz, level

        elif self.state == "HOLD":
            level = self._safe_get_smeter()
            self._last_level_db = level

            if level >= self.cfg.squelch_db:
                # Señal se mantiene ⇒ seguimos en HOLD y renovamos timestamp
                self._last_signal_ts = now_ms
                return self.state, self.current_hz, level

            # Señal cayó: esperamos hold_ms antes de soltar
            if self._last_signal_ts is None or (now_ms - self._last_signal_ts) >= self.cfg.hold_ms:
                # Reanudamos SCAN
                self.state = "SCAN"
                self._advance_freq()
                return self.state, self.current_hz, level

            # Aún en ventana de hold_ms ⇒ seguimos en HOLD
            return self.state, self.current_hz, level

        else:
            # Estado raro, volvemos a IDLE
            self.state = "IDLE"
            return self.state, self.current_hz, self._last_level_db

    # -----------------------------------------------------
    # Utilidades internas
    # -----------------------------------------------------
    def _advance_freq(self):
        self.current_hz += self.cfg.step_hz
        if self.current_hz > self.cfg.end_hz:
            self.current_hz = self.cfg.start_hz

    def _safe_get_smeter(self) -> float:
        try:
            return float(self.driver.get_smeter())
        except Exception:
            return -120.0
