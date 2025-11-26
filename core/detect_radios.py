import json
from pathlib import Path

import serial.tools.list_ports

# ============================================================
#  HackRF: autodetección OPCIONAL
#  - Si no hay librería instalada → no rompe nada
#  - Si luego instalas pyhackrf2 o python-hackrf, se activará
# ============================================================

HAS_HACKRF = False
HACKRF_BACKEND = None

try:
    # Opción 1: pyhackrf2  →  pip install pyhackrf2
    from pyhackrf2 import HackRF  # type: ignore
    HACKRF_BACKEND = "pyhackrf2"
    HAS_HACKRF = True
    print("[INFO] HackRF backend: pyhackrf2")
except Exception:
    try:
        # Opción 2: python-hackrf  →  pip install python-hackrf
        import python_hackrf  # type: ignore
        HACKRF_BACKEND = "python-hackrf"
        HAS_HACKRF = True
        print("[INFO] HackRF backend: python-hackrf")
    except Exception:
        HAS_HACKRF = False
        HACKRF_BACKEND = None
        print("⚠ No se encontró ninguna librería de HackRF (pyhackrf2 / python-hackrf).")
        print("   La autodetección de HackRF quedará desactivada por ahora.")


# === Constantes de detección (ajustables según tu equipo real) ===

# VID/PID típicos: estos son ejemplos, deberás afinarlos viendo
# la salida real de serial.tools.list_ports.comports()
ICOM_VIDS = ["VID_0C26"]      # Icom (ajustar si es necesario)
AOR_HINTS = ["AOR", "AR-5700", "AR5700", "5700"]  # texto en descripción

CONFIG_PATH = Path("config/radios.json")


def load_config(path: Path = CONFIG_PATH) -> dict:
    """
    Carga radios.json.
    Aquí asumimos que ya existe y es válido.
    """
    if not path.exists():
        raise FileNotFoundError(f"No se encontró {path}. Crea config/radios.json primero.")
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(cfg: dict, path: Path = CONFIG_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# === Utilidad: listar todos los puertos (útil para afinar VID/descr) ===

def debug_list_ports():
    print("\n[DEBUG] Puertos serie detectados:")
    for port in serial.tools.list_ports.comports():
        print(f"  - {port.device} | desc={port.description} | hwid={port.hwid}")
    print("")


# === Detectores específicos ===

def detect_icom8600_port() -> str | None:
    """
    Trata de localizar un ICOM por VID o por descripción.
    Devuelve nombre de puerto (ej. 'COM4') o None si no encuentra.
    """
    for port in serial.tools.list_ports.comports():
        hwid = (port.hwid or "").upper()
        desc = (port.description or "").upper()

        # Caso 1: VID típico de ICOM
        if any(vid in hwid for vid in ICOM_VIDS):
            return port.device

        # Caso 2: texto en la descripción
        if "ICOM" in desc or "IC-R8600" in desc or "R8600" in desc:
            return port.device

    return None


def detect_aor5700_port() -> str | None:
    """
    Trata de localizar un AOR AR5700 por descripción/hwid.
    De nuevo, esto es una heurística y se afina viendo
    la info real que devuelve list_ports.
    """
    for port in serial.tools.list_ports.comports():
        hwid = (port.hwid or "").upper()
        desc = (port.description or "").upper()

        if any(h in desc for h in [h.upper() for h in AOR_HINTS]):
            return port.device

        # Si más adelante conoces un VID concreto de AOR,
        # lo agregas aquí, ej:
        # if "VID_1234" in hwid:
        #     return port.device

    return None


def detect_hackrf_index() -> int | None:
    """
    Detectar HackRF si hay librería disponible.
    Si no, devolvemos None y el usuario podrá configurar
    manualmente el índice más adelante.
    """
    if not HAS_HACKRF:
        print("   (HackRF no disponible: librería no instalada)")
        return None

    if HACKRF_BACKEND == "pyhackrf2":
        try:
            dev = HackRF()   # si no hay dispositivo, lanzará error
            dev.close()
            return 0         # asumimos índice 0 para el primero
        except Exception as e:
            print(f"   Error usando pyhackrf2: {e}")
            return None

    if HACKRF_BACKEND == "python-hackrf":
        try:
            # Truco simple: si 'hackrf_info' no explota, asumimos que hay uno.
            python_hackrf.main(["info"])
            return 0
        except Exception as e:
            print(f"   Error usando python-hackrf: {e}")
            return None

    return None


# === Función principal de autodetección ===

def autodetect_and_update_config(path: Path = CONFIG_PATH):
    # (opcional) ver todos los puertos para diagnóstico
    debug_list_ports()

    cfg = load_config(path)

    # Recorremos radios declarados en radios.json
    for r in cfg.get("radios", []):
        r_type = (r.get("type") or "").upper()
        auto = bool(r.get("auto_detect", True))

        if not auto:
            # Usuario forzó configuración manual, no tocamos
            continue

        print(f"\n[INFO] Autodetectando radio {r.get('id')} ({r_type})")

        if r_type == "ICOM8600":
            port = detect_icom8600_port()
            if port:
                print(f"  → ICOM8600 detectado en {port}")
                r["preferred_port"] = port
            else:
                print("  → No se encontró ningún ICOM8600")
                r["preferred_port"] = None

        elif r_type == "AOR5700":
            port = detect_aor5700_port()
            if port:
                print(f"  → AOR5700 detectado en {port}")
                r["preferred_port"] = port
            else:
                print("  → No se encontró ningún AOR5700")
                r["preferred_port"] = None

        elif r_type == "HACKRF":
            idx = detect_hackrf_index()
            if idx is not None:
                print(f"  → HackRF detectado en índice {idx}")
                r["device_index"] = idx
            else:
                print("  → No se encontró ningún HackRF")
                r["device_index"] = None

        else:
            print(f"  → Tipo {r_type} no soportado todavía")

    save_config(cfg, path)
    print(f"\n[OK] Configuración actualizada en {path.resolve()}")


if __name__ == "__main__":
    autodetect_and_update_config()
