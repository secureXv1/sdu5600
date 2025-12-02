# drivers/hackrf_driver.py

"""
Driver para HackRF

- Soporta dos backends posibles:
    * pyhackrf2       (recomendado)  → pip install pyhackrf2
    * python-hackrf   (alternativo)  → pip install python-hackrf

- Implementa la interfaz RadioDriver (drivers/base.py):
    connect(), disconnect(), set_frequency(), set_mode(),
    get_smeter(), get_spectrum().

Notas:
- Este driver está pensado para recibir llamadas periódicas a
  get_spectrum() desde la GUI (por ejemplo, con un QTimer).
- El SCAN de bandas, squelch y grabación lo montaremos aparte
  en un "ScanEngine" que usará este driver debajo.
"""

import logging
from math import log10
from typing import Optional, Tuple

import numpy as np

from drivers.base import RadioDriver

log = logging.getLogger(__name__)

# ------------------------------------------------
#  Intentamos cargar librerías HackRF
# ------------------------------------------------

HAS_HACKRF = False
HACKRF_BACKEND = None

try:
    # Backend 1: pyhackrf2
    from pyhackrf2 import HackRF  # type: ignore
    HACKRF_BACKEND = "pyhackrf2"
    HAS_HACKRF = True
    log.info("[HackRFDriver] Backend: pyhackrf2")
except Exception:
    try:
        # Backend 2: python-hackrf
        # NOTA: la API exacta puede variar, revisa la doc si te da error.
        import python_hackrf as hackrf  # type: ignore
        HACKRF_BACKEND = "python-hackrf"
        HAS_HACKRF = True
        log.info("[HackRFDriver] Backend: python-hackrf")
    except Exception:
        HAS_HACKRF = False
        HACKRF_BACKEND = None
        log.warning(
            "[HackRFDriver] No se encontró ni pyhackrf2 ni python-hackrf. "
            "El driver funcionará en modo SIMULADOR."
        )


class HackRFDriver(RadioDriver):
    """
    Implementación concreta para HackRF.

    Espera un dict `config` que viene de radios.json, por ejemplo:
      {
        "id": "R2",
        "name": "UHF HackRF",
        "type": "HACKRF",
        "device_index": 0,
        "auto_detect": true
      }

    Opcionalmente puedes agregar:
      "center_hz": 435000000,
      "sample_rate": 8000000,
      "fft_size": 4096
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.dev = None

        # Parámetros básicos
        self.center_hz: float = float(config.get("center_hz", 435_000_000))  # 435 MHz UHF por defecto
        self.sample_rate: float = float(config.get("sample_rate", 8_000_000))  # 8 MSPS típico
        self.fft_size: int = int(config.get("fft_size", 4096))
        self.lna_gain: int = int(config.get("lna_gain", 16))   # valores típicos, afínalos según tu equipo
        self.vga_gain: int = int(config.get("vga_gain", 16))

        self._mode: str = "NFM"  # modo lógico, realmente para HackRF es solo IQ
        self._device_index: Optional[int] = config.get("device_index", 0)

        # Buffer interno opcional
        self._last_spectrum: Optional[Tuple[np.ndarray, np.ndarray]] = None

        log.info(
            "[HackRFDriver] init center=%.3f MHz, sr=%.1f Msps, fft_size=%d",
            self.center_hz / 1e6,
            self.sample_rate / 1e6,
            self.fft_size,
        )

    # ------------------------------------------------
    #  Métodos abstractos implementados
    # ------------------------------------------------

    def connect(self):
        """
        Abre el dispositivo HackRF.

        Si no hay backend disponible, se queda en modo simulador.
        """
        if not HAS_HACKRF:
            log.warning("[HackRFDriver] Sin backend HackRF; modo SIMULADOR.")
            self.connected = True  # conectado lógicamente, pero simulado
            return

        if self.connected:
            return

        try:
            if HACKRF_BACKEND == "pyhackrf2":
                # pyhackrf2: crea instancia, configura
                self.dev = HackRF(device_index=self._device_index)
                self.dev.set_sample_rate(self.sample_rate)
                self.dev.set_freq(self.center_hz)
                self.dev.set_lna_gain(self.lna_gain)
                self.dev.set_vga_gain(self.vga_gain)
                self.dev.set_amp_enable(False)  # ajusta a tus necesidades

            elif HACKRF_BACKEND == "python-hackrf":
                # Ejemplo genérico; revisa la doc de python-hackrf si falla
                self.dev = hackrf.HackRF()
                self.dev.open(self._device_index)
                self.dev.set_samplerate(self.sample_rate)
                self.dev.set_freq(self.center_hz)
                self.dev.set_lna_gain(self.lna_gain)
                self.dev.set_vga_gain(self.vga_gain)

            self.connected = True
            log.info("[HackRFDriver] Conectado correctamente.")
        except Exception as e:
            log.exception("[HackRFDriver] Error al conectar: %s", e)
            self.dev = None
            self.connected = False
            raise

    def disconnect(self):
        """
        Cierra el dispositivo HackRF.
        """
        if not self.connected:
            return

        try:
            if self.dev is not None:
                # Intentamos cerrar según backend
                if HACKRF_BACKEND == "pyhackrf2":
                    self.dev.close()
                elif HACKRF_BACKEND == "python-hackrf":
                    self.dev.close()
        except Exception as e:
            log.warning("[HackRFDriver] Error al cerrar: %s", e)
        finally:
            self.dev = None
            self.connected = False
            log.info("[HackRFDriver] Desconectado.")

    def set_frequency(self, hz: float):
        """
        Cambia la frecuencia central del HackRF.
        """
        self.center_hz = float(hz)

        if not self.connected or self.dev is None or not HAS_HACKRF:
            # En simulador solo actualizamos la variable
            log.debug("[HackRFDriver] set_frequency (SIM) -> %.3f MHz", hz / 1e6)
            return

        try:
            if HACKRF_BACKEND == "pyhackrf2":
                self.dev.set_freq(self.center_hz)
            elif HACKRF_BACKEND == "python-hackrf":
                self.dev.set_freq(self.center_hz)
            log.info("[HackRFDriver] Frecuencia ajustada a %.3f MHz", hz / 1e6)
        except Exception as e:
            log.exception("[HackRFDriver] Error al set_frequency: %s", e)

    def set_mode(self, mode: str):
        """
        Modo "lógico" (AM/FM/NFM/USB/LSB) para integrarse con la GUI.
        En HackRF realmente solo recibimos IQ crudo, así que aquí
        solo lo almacenamos para referencia.
        """
        self._mode = mode.upper()
        log.debug("[HackRFDriver] set_mode -> %s (solo lógico, sin DSP interno)", self._mode)

    def get_smeter(self) -> float:
        """
        Devuelve un nivel de señal promedio (en dBFS aprox).
        Implementación: reutiliza get_spectrum() y promedia.
        """
        freqs, levels = self.get_spectrum()
        if levels.size == 0:
            return -120.0
        # Media en dB
        return float(levels.mean())

    def get_spectrum(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Captura un bloque de muestras IQ, calcula FFT y devuelve:
            (freqs_hz, levels_db)

        - freqs_hz: array de frecuencias relativas a center_hz
        - levels_db: nivel en dBFS aproximado
        """
        if not self.connected or not HAS_HACKRF or self.dev is None:
            # SIMULADOR: genera ruido + uno o dos picos
            freqs = np.linspace(self.center_hz - self.sample_rate / 2,
                                self.center_hz + self.sample_rate / 2,
                                self.fft_size)
            # Ruido base
            noise = np.random.normal(0, 1, self.fft_size)
            # FFT ficticia
            P = np.abs(np.fft.fftshift(np.fft.fft(noise)))
            P = P / (P.max() + 1e-9)
            levels_db = 20 * np.log10(P + 1e-9) - 80  # alrededor de -80 dB
            self._last_spectrum = (freqs, levels_db)
            return freqs, levels_db

        # MODO REAL
        try:
            # -----------------------------
            #  Captura de muestras IQ
            # -----------------------------
            # Ojo: la API concreta depende del backend.
            # Aquí dejamos un ejemplo genérico con pyhackrf2.
            if HACKRF_BACKEND == "pyhackrf2":
                # Captura síncrona: capturamos fft_size muestras complejas
                num_samples = self.fft_size
                # La librería suele devolver un np.ndarray de dtype=np.complex64
                iq = self.dev.receive_samples(num_samples)  # ajusta el método si es diferente

            elif HACKRF_BACKEND == "python-hackrf":
                # Ejemplo genérico: python-hackrf puede usar callbacks;
                # para simplificar, asumimos que hay un método sync similar.
                num_samples = self.fft_size
                iq = self.dev.receive_samples(num_samples)  # revisa doc si te da error

            else:
                # No debería pasar
                raise RuntimeError("Backend HackRF desconocido")

            # Aseguramos que sea array complejo
            iq = np.asarray(iq).astype(np.complex64, copy=False)

            # -----------------------------
            #  FFT y espectro
            # -----------------------------
            fft_vals = np.fft.fftshift(np.fft.fft(iq, n=self.fft_size))
            psd = np.abs(fft_vals) ** 2

            # Normalizamos a dBFS aproximado
            psd = psd / (psd.max() + 1e-12)
            levels_db = 10 * np.log10(psd + 1e-12)  # dB

            # Eje de frecuencias absoluto en Hz
            freqs = np.linspace(
                self.center_hz - self.sample_rate / 2,
                self.center_hz + self.sample_rate / 2,
                self.fft_size
            )

            self._last_spectrum = (freqs, levels_db)
            return freqs, levels_db

        except Exception as e:
            log.exception("[HackRFDriver] Error en get_spectrum: %s", e)
            # Si algo falla, devolvemos último espectro o uno vacío
            if self._last_spectrum is not None:
                return self._last_spectrum
            return np.array([]), np.array([])

    # ------------------------------------------------
    #  Extensión opcional: start_scan()
    # ------------------------------------------------
    def start_scan(self, band_cfg):
        """
        Método opcional de la interfaz base.
        Más adelante podemos implementar un SCAN nativo aquí,
        pero la idea es manejarlo desde un ScanEngine externo.

        band_cfg podría ser un dict:
          {
            "start_hz": 120_000_000,
            "end_hz": 122_000_000,
            "step_hz": 6_500,
          }
        """
        log.info("[HackRFDriver] start_scan llamado con band_cfg=%r (no implementado aún)", band_cfg)
        # Aquí no hacemos nada por ahora.
        # El motor de scan externo irá iterando set_frequency() + get_smeter().
        return
