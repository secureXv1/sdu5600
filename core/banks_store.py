from __future__ import annotations
import json, uuid
from pathlib import Path

ALLOWED_MODES = {"FM","NFM","AM","LSB","USB","WFM"}

class BanksStore:
    def __init__(self, path="config/banks.json"):
        self.path = Path(path)
        self.data = {"freq_banks": [], "range_banks": [], "scan_settings": {}}
        self.load()

    def load(self):
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def settings(self):
        s = self.data.get("scan_settings") or {}
        return {
            "squelch_db": float(s.get("squelch_db", -55)),
            "settle_ms": int(s.get("settle_ms", 180)),
            "hold_seconds": int(s.get("hold_seconds", 5)),
            "loop": bool(s.get("loop", True)),
        }

    def list_banks(self, kind: str):
        return list(self.data["freq_banks" if kind=="freq" else "range_banks"])

    def _limit_check(self, kind: str, creating: bool):
        if not creating:
            return
        key = "freq_banks" if kind=="freq" else "range_banks"
        if len(self.data[key]) >= 20:
            raise ValueError("Máximo 20 bancos para este tipo.")

    def upsert_bank(self, kind: str, bank: dict):
        key = "freq_banks" if kind=="freq" else "range_banks"
        arr = self.data[key]

        creating = not bank.get("id")
        self._limit_check(kind, creating)

        if creating:
            bank["id"] = f"{'fb' if kind=='freq' else 'rb'}_{uuid.uuid4().hex[:8]}"

        bank["name"] = (bank.get("name") or "").strip()
        if not bank["name"]:
            raise ValueError("El banco debe tener nombre.")
        bank["active"] = bool(bank.get("active", False))

        # Validaciones mínimas
        if kind == "range":
            r = bank.get("range") or {}
            start = float(r.get("start_mhz", 0))
            stop = float(r.get("stop_mhz", 0))
            ts = float(r.get("ts_khz", 0))
            mode = (bank.get("mode") or "").upper().strip()
            if not (start > 0 and stop > start):
                raise ValueError("Rango inválido (inicio/fin).")
            if ts <= 0:
                raise ValueError("TS inválido.")
            if mode not in ALLOWED_MODES:
                raise ValueError("MODE inválido.")
            bank["mode"] = mode

            rg = bank.get("rf_gain") or {}
            bank["rf_gain"] = {
                "lna_db": int(rg.get("lna_db", 32)),
                "vga_db": int(rg.get("vga_db", 20)),
                "amp": bool(rg.get("amp", False)),
            }

        # Reemplaza/Inserta
        for i, b in enumerate(arr):
            if b.get("id") == bank["id"]:
                arr[i] = bank
                self.save()
                return bank["id"]

        arr.append(bank)
        self.save()
        return bank["id"]

    def delete_bank(self, kind: str, bank_id: str):
        key = "freq_banks" if kind=="freq" else "range_banks"
        self.data[key] = [b for b in self.data[key] if b.get("id") != bank_id]
        self.save()

    def set_active(self, kind: str, bank_id: str, active: bool):
        key = "freq_banks" if kind=="freq" else "range_banks"
        for b in self.data[key]:
            if b.get("id") == bank_id:
                b["active"] = bool(active)
        self.save()
