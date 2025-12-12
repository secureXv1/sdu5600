# core/radio_manager.py
import json
from pathlib import Path
from drivers.hackrf_driver import HackRFDriver
from drivers.icom8600_driver import Icom8600Driver
from drivers.aor5700_driver import Aor5700Driver


class RadioManager:
    def __init__(self, config_path="config/radios.json"):
        self.radios = []
        self._load_config(config_path)

    def _load_config(self, path):
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)

        # Leer lista desde la clave "radios"
        radios_cfg = data.get("radios", []) if isinstance(data, dict) else data

        for cfg in radios_cfg:
            driver = self._create_driver(cfg)
            rtype = (cfg.get("type") or "").upper()

            self.radios.append({
                "id": cfg["id"],
                "name": cfg["name"],
                "type": rtype,
                "config": cfg,
                "driver": driver,
            })

    def _create_driver(self, cfg):
        t = (cfg.get("type") or "").upper()
        if t == "HACKRF":
            return HackRFDriver(cfg)
        elif t == "ICOM8600":
            return Icom8600Driver(cfg)
        elif t == "AOR5700":
            return Aor5700Driver(cfg)
        else:
            raise ValueError(f"Tipo de radio no soportado: {t}")

    # Opcional: por si la UI quiere llamar a esto
    def connect_all(self):
        for r in self.radios:
            try:
                r["driver"].connect()
            except Exception as e:
                print(f"[RadioManager] Error conectando {r['name']}: {e}")

    def get_radios(self):
        return self.radios
