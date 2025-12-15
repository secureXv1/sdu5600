# core/radio_manager.py
import json
from pathlib import Path

from drivers.hackrf_driver import HackRFDriver
from drivers.icom8600_driver import Icom8600Driver
from drivers.aor5700_driver import Aor5700Driver

from core.audio_engine import AudioEngine


class RadioManager:
    def __init__(self, config_path: str = "config/radios.json"):
        self.radios = []
        self.audio = AudioEngine()
        self._load_config(config_path)

    def _load_config(self, path: str):
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)

        radios_cfg = data.get("radios", []) if isinstance(data, dict) else data

        for cfg in radios_cfg:
            driver = self._create_driver(cfg)
            rtype = (cfg.get("type") or "").upper()

            self.radios.append({
                "id": cfg.get("id"),
                "name": cfg.get("name"),
                "type": rtype,
                "config": cfg,
                "driver": driver,
            })

    def _create_driver(self, cfg):
        t = (cfg.get("type") or "").upper()
        if t == "HACKRF":
            return HackRFDriver(cfg)
        if t == "ICOM8600":
            return Icom8600Driver(cfg)
        if t == "AOR5700":
            return Aor5700Driver(cfg)
        raise ValueError(f"Tipo de radio no soportado: {t}")

    def connect_all(self):
        for r in self.radios:
            try:
                r["driver"].connect()
            except Exception as e:
                print(f"[RadioManager] Error conectando {r['name']}: {e}")

    def get_radios(self):
        return self.radios

    # ===== Audio control =====
    def start_audio(self, driver, freq_mhz: float, mode: str):
        if hasattr(driver, "set_center_freq"):
            driver.set_center_freq(freq_mhz * 1e6)
        self.audio.start(driver, mode)




    def stop_audio(self):
        self.audio.stop()
