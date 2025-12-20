# ui/smeter_widget.py
from __future__ import annotations
import math
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath


class SMeterWidget(QWidget):
    """
    S-Meter analógico estilo SDR Control:
    - set_level_dbm(dbm) para actualizar aguja y número
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dbm = -140.0
        self.setFixedSize(240, 120)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_level_dbm(self, dbm: float):
        try:
            self._dbm = float(dbm)
        except Exception:
            return
        self.update()

    def _map_dbm_to_angle(self, dbm: float) -> float:
        # Mapa visual parecido al screenshot: -140 (izq) a -20 (der)
        dbm = max(-140.0, min(-20.0, float(dbm)))
        t = (dbm - (-140.0)) / (120.0)  # 0..1
        # arco: ~200° -> ~-20° (en rad)
        a0 = math.radians(200.0)
        a1 = math.radians(-20.0)
        return a0 + t * (a1 - a0)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # Fondo "amarillo"
        bg = QColor(244, 202, 104)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)

        # Borde
        p.setPen(QPen(QColor(60, 40, 10), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)

        # Texto "dBm"
        p.setPen(QColor(30, 20, 10))
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(10, 18, "dBm")

        # Arco y ticks
        cx, cy = 120, 105
        r = 88
        p.setPen(QPen(QColor(30, 20, 10), 2))

        arc_rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        # startAngle y spanAngle en 1/16 grados (Qt)
        p.drawArc(arc_rect, int(200 * 16), int(-220 * 16))

        # ticks mayores
        ticks = [-140, -120, -100, -80, -60, -40, -20]
        p.setFont(QFont("Segoe UI", 8))
        for v in ticks:
            ang = self._map_dbm_to_angle(v)
            x0 = cx + (r - 6) * math.cos(ang)
            y0 = cy + (r - 6) * math.sin(ang)
            x1 = cx + (r - 18) * math.cos(ang)
            y1 = cy + (r - 18) * math.sin(ang)
            p.setPen(QPen(QColor(30, 20, 10), 2))
            p.drawLine(int(x0), int(y0), int(x1), int(y1))

            # labels
            lx = cx + (r - 34) * math.cos(ang)
            ly = cy + (r - 34) * math.sin(ang)
            p.setPen(QColor(30, 20, 10))
            p.drawText(int(lx) - 12, int(ly) + 4, f"{v}")

        # Aguja roja
        ang = self._map_dbm_to_angle(self._dbm)
        xN = cx + (r - 24) * math.cos(ang)
        yN = cy + (r - 24) * math.sin(ang)
        p.setPen(QPen(QColor(200, 30, 30), 3))
        p.drawLine(cx, cy, int(xN), int(yN))

        # Centro aguja
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(50, 30, 10))
        p.drawEllipse(cx - 4, cy - 4, 8, 8)

        # Display numérico (verde)
        box = QRectF(78, 48, 84, 26)
        p.setPen(QPen(QColor(60, 70, 20), 1))
        p.setBrush(QColor(170, 220, 95))
        p.drawRoundedRect(box, 6, 6)

        p.setPen(QColor(20, 30, 10))
        f2 = QFont("Segoe UI", 10)
        f2.setBold(True)
        p.setFont(f2)
        p.drawText(box, Qt.AlignCenter, f"{self._dbm:.1f}")
