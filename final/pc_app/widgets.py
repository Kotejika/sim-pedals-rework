from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPolygonF
from PySide6.QtWidgets import QWidget

import protocol as P

COLORS = [QColor("#4aa3ff"), QColor("#4ade80"), QColor("#ff6b6b")]


class CurveEditor(QWidget):
    """11-point response curve; drag points vertically. Shows the live input marker."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(260, 260)
        self.curve = [i * 100 for i in range(P.CURVE_PTS)]
        self.marker = None        # (x_permille, y_permille) of live position
        self._drag = -1
        self.setMouseTracking(True)

    def set_curve(self, c):
        self.curve = list(c)
        self.update()

    def _rect(self):
        return QRectF(28, 10, self.width() - 40, self.height() - 34)

    def _pt(self, i):
        r = self._rect()
        return QPointF(r.left() + r.width() * i / (P.CURVE_PTS - 1),
                       r.bottom() - r.height() * self.curve[i] / 1000.0)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        r = self._rect()
        p.fillRect(self.rect(), QColor(10, 16, 29, 174))
        p.setPen(QPen(pal.mid().color(), 1))
        for i in range(11):
            x = r.left() + r.width() * i / 10
            y = r.bottom() - r.height() * i / 10
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QPen(pal.text().color(), 1))
        p.drawText(2, int(r.bottom()), "0")
        p.drawText(2, int(r.top()) + 10, "100%")
        p.drawText(int(r.left()), self.height() - 4, "input")
        p.setPen(QPen(QColor("#4aa3ff"), 2))
        p.drawPolyline(QPolygonF([self._pt(i) for i in range(P.CURVE_PTS)]))
        p.setBrush(QBrush(QColor("#4aa3ff")))
        for i in range(P.CURVE_PTS):
            p.drawEllipse(self._pt(i), 5, 5)
        if self.marker:
            mx = r.left() + r.width() * self.marker[0] / 1000.0
            my = r.bottom() - r.height() * self.marker[1] / 1000.0
            p.setPen(QPen(QColor("#ff6b6b"), 1))
            p.drawLine(QPointF(mx, r.bottom()), QPointF(mx, my))
            p.setBrush(QBrush(QColor("#ff6b6b")))
            p.drawEllipse(QPointF(mx, my), 4, 4)

    def mousePressEvent(self, e):
        best, bd = -1, 14.0
        for i in range(P.CURVE_PTS):
            d = abs(self._pt(i).x() - e.position().x())
            if d < bd:
                best, bd = i, d
        self._drag = best
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag >= 0 and e.buttons() & Qt.LeftButton:
            r = self._rect()
            v = int(round((r.bottom() - e.position().y()) / r.height() * 1000))
            self.curve[self._drag] = max(0, min(1000, v))
            self.update()
            self.changed.emit()

    def mouseReleaseEvent(self, e):
        self._drag = -1


class Plot(QWidget):
    """Rolling plot of the 3 output axes (0..100 %)."""

    def __init__(self, n=3, seconds=10, hz=30):
        super().__init__()
        self.setMinimumHeight(120)
        self.n = n
        self.cap = seconds * hz
        self.data = [[] for _ in range(n)]
        self.names = ["Clutch", "Throttle", "Brake"]

    def push(self, vals):
        for i, v in enumerate(vals[:self.n]):
            d = self.data[i]
            d.append(v / 65535.0)
            if len(d) > self.cap:
                del d[0]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(10, 16, 29, 174))
        w, h = self.width() - 6, self.height() - 6
        p.setPen(QPen(self.palette().mid().color(), 1))
        for k in range(5):
            y = 3 + h * k / 4
            p.drawLine(3, int(y), 3 + w, int(y))
        for i, d in enumerate(self.data):
            if len(d) < 2:
                continue
            p.setPen(QPen(COLORS[i % 3], 2))
            pts = [QPointF(3 + w * j / (self.cap - 1), 3 + h * (1 - v)) for j, v in enumerate(d)]
            p.drawPolyline(QPolygonF(pts))
        p.setPen(self.palette().text().color())
        for i in range(self.n):
            p.setPen(QPen(COLORS[i % 3], 1))
            p.drawText(8 + i * 80, 16, self.names[i])
