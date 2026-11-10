from pathlib import Path

root = Path("project/pc_app")
app_file = root / "app.py"
widgets_file = root / "widgets.py"

def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one occurrence, found {count}")
    return text.replace(old, new, 1)

app = app_file.read_text(encoding="utf-8")
app = replace_once(app, "import json\nimport sys",
                   "import json\nimport sys\nfrom pathlib import Path",
                   "app imports")
app = replace_once(app, "from widgets import CurveEditor, Plot",
                   "from widgets import BackgroundWidget, CurveEditor, Plot",
                   "widget imports")
app = replace_once(app, "        c = QWidget()\n        self.setCentralWidget(c)",
                   "        wallpaper = Path(__file__).resolve().parent / \"assets\" / \"pit_crew_anime.svg\"\n"
                   "        c = BackgroundWidget(wallpaper)\n        self.setCentralWidget(c)",
                   "central background")
app = replace_once(app, "        self.resize(980, 760)\n",
                   "        self.resize(1240, 840)\n"
                   "        self.setMinimumSize(980, 700)\n"
                   "        self.setStyleSheet(\"\"\"\n"
                   "            QWidget { color: #e7f2fb; font-family: Segoe UI, sans-serif; }\n"
                   "            QMainWindow { background: #07111e; }\n"
                   "            QLabel { background: transparent; }\n"
                   "            QGroupBox { background: rgba(5, 15, 28, 205); border: 1px solid rgba(100,207,218,100); "
                   "border-radius: 11px; margin-top: 12px; padding: 12px 9px 9px; font-weight: 600; }\n"
                   "            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #72e5e8; }\n"
                   "            QTabWidget::pane { background: rgba(5,14,27,190); border: 1px solid #31566c; border-radius: 10px; }\n"
                   "            QTabBar::tab { background: rgba(12,27,44,220); color: #a9c5d8; padding: 9px 20px; "
                   "margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px; }\n"
                   "            QTabBar::tab:selected { background: rgba(18,54,70,240); color: #75f0e4; }\n"
                   "            QPushButton { background: rgba(19,48,67,235); border: 1px solid #3f9ca9; "
                   "border-radius: 7px; padding: 7px 12px; }\n"
                   "            QPushButton:hover { background: rgba(26,82,100,245); border-color: #74f2e7; }\n"
                   "            QComboBox, QSpinBox, QDoubleSpinBox { background: rgba(4,14,27,235); "
                   "border: 1px solid #496d80; border-radius: 6px; padding: 5px 7px; }\n"
                   "            QProgressBar { background: rgba(4,13,24,215); border: 1px solid #49697c; "
                   "border-radius: 6px; text-align: center; min-height: 21px; }\n"
                   "            QProgressBar::chunk { background: #25bfc6; border-radius: 5px; }\n"
                   "        \"\"\")\n",
                   "window styling")
# The original root layout is intentionally kept; the wallpaper widget paints behind it.
app = app.replace("DRDY pin %s", "DRDY physical level %s")
app = app.replace('"SPI device not ready"', '"GPIO / ADC initialization failed"')
app_file.write_text(app, encoding="utf-8")

widgets = widgets_file.read_text(encoding="utf-8")
widgets = replace_once(
    widgets,
    "from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPolygonF",
    "from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPolygonF, QLinearGradient",
    "QtGui imports"
)
widgets = widgets.replace("p.fillRect(self.rect(), pal.base())",
                          "p.fillRect(self.rect(), QColor(5, 13, 25, 145))")
widgets += r'''

class BackgroundWidget(QWidget):
    """Racing garage backdrop with an original anime pit-crew illustration."""
    def __init__(self, image_path=None, parent=None):
        super().__init__(parent)
        self._renderer = QSvgRenderer(str(image_path)) if image_path else QSvgRenderer()
        self.setAttribute(Qt.WA_StyledBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#07101d"))
        if self._renderer.isValid() and self.width() > 0 and self.height() > 0:
            view = self._renderer.viewBoxF()
            if view.width() > 0 and view.height() > 0:
                painter.setOpacity(0.34)
                self._renderer.render(painter, QRectF(self.rect()))
                scale = min(self.width() / view.width(), self.height() / view.height())
                w, h = view.width() * scale, view.height() * scale
                target = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
                painter.setOpacity(0.94)
                self._renderer.render(painter, target)
                painter.setOpacity(1.0)
        shade = QLinearGradient(0, 0, self.width(), 0)
        shade.setColorAt(0.0, QColor(3, 9, 19, 145))
        shade.setColorAt(0.58, QColor(3, 9, 19, 65))
        shade.setColorAt(1.0, QColor(3, 9, 19, 25))
        painter.fillRect(self.rect(), shade)
'''
widgets_file.write_text(widgets, encoding="utf-8")

print("Applied final desktop UI patch.")
