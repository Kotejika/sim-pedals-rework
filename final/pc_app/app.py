"""Sim Pedals - settings & calibration.  Windows / Linux / macOS.  python app.py [--sim]"""
import json
import sys

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

import protocol as P
from device import HubError, RealHub, SimHub
from processing import AxisState, axis_process
from widgets import CurveEditor, Plot

APP_STYLE = r"""
QMainWindow, QWidget { color: #F1F4FA; font-family: "Segoe UI"; font-size: 10pt; }
QLabel#Brand { color: #E7E6FF; font-size: 13pt; font-weight: 700; letter-spacing: 2px; }
QLabel#Status { color: #D9E4F4; background: rgba(12,18,30,218); border: 1px solid rgba(151,177,220,85); border-radius: 9px; padding: 8px 12px; }
QGroupBox { background: rgba(13,20,34,222); border: 1px solid rgba(160,183,220,90); border-radius: 12px; margin-top: 13px; padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 6px; color: #D9C8FF; }
QTabWidget::pane { background: rgba(13,20,34,190); border: 1px solid rgba(160,183,220,95); border-radius: 12px; top: -1px; }
QTabBar::tab { background: rgba(24,34,52,235); color: #C8D4E6; border: 1px solid rgba(160,183,220,70); border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 9px 18px; margin-right: 4px; }
QTabBar::tab:selected { background: rgba(95,79,150,240); color: white; }
QPushButton { background: rgba(38,52,75,245); color: #F2F5FB; border: 1px solid rgba(166,189,225,115); border-radius: 8px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover { background: rgba(66,83,117,250); border-color: #BFAEFF; }
QPushButton:pressed { background: rgba(91,72,138,250); }
QPushButton#PrimaryButton { background: #765CC2; border-color: #A58DEB; font-weight: 700; }
QPushButton#PrimaryButton:hover { background: #8A70D4; }
QCheckBox { spacing: 8px; color: #E5EAF5; }
QComboBox, QSpinBox, QDoubleSpinBox { background: rgba(17,27,43,245); color: #F4F6FB; border: 1px solid rgba(158,180,216,115); border-radius: 7px; padding: 5px 8px; min-height: 22px; }
QComboBox QAbstractItemView { background: #1B2639; color: #F4F6FB; selection-background-color: #765CC2; }
QSlider::groove:horizontal { background: rgba(143,160,192,105); height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #C7B3FF; border: 1px solid #F2ECFF; width: 15px; margin: -6px 0; border-radius: 7px; }
QProgressBar { background: rgba(7,12,22,220); border: 1px solid rgba(160,183,220,100); border-radius: 7px; height: 16px; text-align: center; color: white; }
QProgressBar::chunk { background: #8B74D6; border-radius: 6px; }
QLabel { background: transparent; }
"""

APP_DIR = Path(__file__).resolve().parent
WALLPAPER_PATH = APP_DIR / "assets" / "sachi-room.svg"


class WallpaperWidget(QWidget):
    """Original, fully clothed Sachi-inspired fan-art background; not an official image."""
    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self._renderer = QSvgRenderer(str(image_path)) if image_path.exists() else None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111827"))
        if self._renderer and self._renderer.isValid():
            source = self._renderer.viewBoxF()
            if source.isEmpty():
                source = QRectF(0, 0, 1920, 1080)
            scale = max(self.width() / source.width(), self.height() / source.height())
            w, h = source.width() * scale, source.height() * scale
            target = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
            self._renderer.render(painter, target)
        painter.end()


SLOT_ITEMS = [("Auto (next free)", P.SLOT_AUTO), ("Off", P.SLOT_OFF)] + \
             [("%d: %s" % (i + 1, n), i) for i, n in enumerate(P.HID_AXIS_NAMES)]


class AxisTab(QWidget):
    def __init__(self, win, axis):
        super().__init__()
        self.win, self.axis = win, axis
        self.is_brake = axis == 2
        self.cfg = P.AxisCfg()
        self.raw = 0
        self.rec = False
        self.rec_min = self.rec_max = None
        self._loading = False
        self.timer = QTimer(self, singleShot=True, interval=120, timeout=self.push)

        left = QVBoxLayout()
        self.bar = QProgressBar(maximum=1000)
        self.bar.setFormat("%p/10 %")
        self.lbl = QLabel("raw: -")
        left.addWidget(self.bar)
        left.addWidget(self.lbl)

        g = QGroupBox("Calibration")
        f = QFormLayout(g)
        self.min_lbl, self.max_lbl = QLabel("-"), QLabel("-")
        b1, b2 = QPushButton("Set 0 % = current"), QPushButton("Set 100 % = current")
        b1.clicked.connect(lambda: self.set_cal(True))
        b2.clicked.connect(lambda: self.set_cal(False))
        self.rec_btn = QPushButton("Record min/max (move the pedal fully, click again)")
        self.rec_btn.setCheckable(True)
        self.rec_btn.toggled.connect(self.toggle_rec)
        f.addRow("0 % raw:", self.min_lbl)
        f.addRow("100 % raw:", self.max_lbl)
        f.addRow(b1)
        f.addRow(b2)
        f.addRow(self.rec_btn)
        left.addWidget(g)

        g2 = QGroupBox("Shape")
        f2 = QFormLayout(g2)
        self.inv = QCheckBox("Invert")
        self.inv.toggled.connect(self.edited)
        self.flt = QSlider(Qt.Horizontal, minimum=0, maximum=8)
        self.flt.valueChanged.connect(self.edited)
        self.flt_lbl = QLabel("off")
        self.dz_lo = QDoubleSpinBox(decimals=1, minimum=0, maximum=100, suffix=" %")
        self.dz_hi = QDoubleSpinBox(decimals=1, minimum=0, maximum=100, suffix=" %")
        self.dz_lo.valueChanged.connect(self.edited)
        self.dz_hi.valueChanged.connect(self.edited)
        self.slot = QComboBox()
        for name, v in SLOT_ITEMS:
            self.slot.addItem(name, v)
        self.slot.currentIndexChanged.connect(self.edited)
        h = QHBoxLayout()
        h.addWidget(self.flt)
        h.addWidget(self.flt_lbl)
        f2.addRow(self.inv)
        f2.addRow("Smoothing:", h)
        f2.addRow("Deadzone start:", self.dz_lo)
        f2.addRow("Deadzone end (=100 %):", self.dz_hi)
        f2.addRow("USB axis:", self.slot)
        left.addWidget(g2)

        if self.is_brake:
            g3 = QGroupBox("Load cell (kg)")
            f3 = QFormLayout(g3)
            self.kg_max = QDoubleSpinBox(decimals=1, minimum=1, maximum=200, value=100, suffix=" kg")
            self.kg_max.valueChanged.connect(self.edited)
            self.kg_known = QDoubleSpinBox(decimals=1, minimum=1, maximum=200, value=30, suffix=" kg")
            self.kg_now = QLabel("- kg")
            tare = QPushButton("Tare (pedal unloaded)")
            tare.clicked.connect(lambda: self.win.tare(self.axis))
            meas = QPushButton("Calibrate with the known weight now applied")
            meas.clicked.connect(self.cal_weight)
            f3.addRow("100 % output at:", self.kg_max)
            f3.addRow(tare)
            f3.addRow("Known weight:", self.kg_known)
            f3.addRow(meas)
            f3.addRow("Force now:", self.kg_now)
            left.addWidget(g3)
        left.addStretch(1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Response curve (drag points)"))
        self.curve = CurveEditor()
        self.curve.changed.connect(self.edited)
        right.addWidget(self.curve)
        lin = QPushButton("Reset to linear")
        lin.clicked.connect(lambda: (self.curve.set_curve([i * 100 for i in range(11)]), self.edited()))
        right.addWidget(lin)
        right.addStretch(1)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addLayout(right, 1)
        self.prev = AxisState()

    # ----- config <-> widgets
    def load(self, cfg):
        self._loading = True
        self.cfg = cfg
        self.inv.setChecked(bool(cfg.flags & P.AXF_INVERT))
        self.flt.setValue(cfg.filter)
        self.dz_lo.setValue(cfg.dz_lo / 10)
        self.dz_hi.setValue(cfg.dz_hi / 10)
        self.slot.setCurrentIndex(max(0, self.slot.findData(cfg.slot)))
        self.curve.set_curve(cfg.curve)
        if self.is_brake and cfg.full_scale_dkg:
            self.kg_max.setValue(cfg.full_scale_dkg / 10)
        self.update_labels()
        self._loading = False

    def update_labels(self):
        self.min_lbl.setText(str(self.cfg.cal_min))
        self.max_lbl.setText(str(self.cfg.cal_max))
        self.flt_lbl.setText("off" if self.cfg.filter == 0 else "1/%d" % (1 << self.cfg.filter))

    def edited(self, *_):
        if self._loading:
            return
        c = self.cfg
        c.flags = (c.flags & ~P.AXF_INVERT) | (P.AXF_INVERT if self.inv.isChecked() else 0)
        c.filter = self.flt.value()
        c.dz_lo = int(round(self.dz_lo.value() * 10))
        c.dz_hi = int(round(self.dz_hi.value() * 10))
        c.slot = self.slot.currentData()
        c.curve = list(self.curve.curve)
        if self.is_brake:
            c.full_scale_dkg = int(round(self.kg_max.value() * 10))
        self.update_labels()
        self.timer.start()

    def push(self):
        self.win.send_axis(self.axis, self.cfg)

    # ----- calibration helpers
    def set_cal(self, is_min):
        if is_min:
            self.cfg.cal_min = self.raw
        else:
            self.cfg.cal_max = self.raw
        self.update_labels()
        self.push()

    def toggle_rec(self, on):
        if on:
            self.rec_min = self.rec_max = self.raw
        elif self.rec_min is not None and self.rec_max - self.rec_min > 10:
            self.cfg.cal_min, self.cfg.cal_max = self.rec_min, self.rec_max
            self.update_labels()
            self.push()
        self.rec = on

    def cal_weight(self):
        span = self.raw - self.cfg.cal_min
        if span <= 0:
            QMessageBox.warning(self, "Calibration", "Tare first, then load the pedal and click again.")
            return
        per_kg = span / self.kg_known.value()
        self.cfg.cal_max = int(self.cfg.cal_min + per_kg * self.kg_max.value())
        self.update_labels()
        self.push()

    # ----- live
    def live(self, raw, out):
        self.raw = raw
        if self.rec:
            self.rec_min, self.rec_max = min(self.rec_min, raw), max(self.rec_max, raw)
        _, f = axis_process(self.cfg, self.prev, raw)   # preview uses same math as the hub
        span = self.cfg.cal_max - self.cfg.cal_min
        lin = 0 if span == 0 else max(0.0, min(1.0, (f - self.cfg.cal_min) / span))
        if self.cfg.flags & P.AXF_INVERT:
            lin = 1 - lin
        self.bar.setValue(int(out / 65535 * 1000))
        self.lbl.setText("raw: %d    output: %d / 65535" % (raw, out))
        self.curve.marker = (lin * 1000, out / 65535 * 1000)
        self.curve.update()
        if self.is_brake:
            kg = (f - self.cfg.cal_min) / span * self.kg_max.value() if span else 0
            self.kg_now.setText("%.1f kg" % kg)


class Main(QMainWindow):
    def __init__(self, sim=False):
        super().__init__()
        self.setWindowTitle("Sim Pedals • Precision Control")
        self.resize(1180, 820)
        self.setMinimumSize(920, 680)
        self.setStyleSheet(APP_STYLE)
        self.hub = None
        self.sim_forced = sim

        c = WallpaperWidget(WALLPAPER_PATH)
        self.setCentralWidget(c)
        v = QVBoxLayout(c)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        brand = QLabel("SIM PEDALS  /  PRECISION CONTROL")
        brand.setObjectName("Brand")
        self.status = QLabel("not connected")
        self.status.setObjectName("Status")
        self.status.setMinimumWidth(220)
        self.status.setWordWrap(True)
        self.sim_cb = QCheckBox("Simulator")
        self.sim_cb.setChecked(sim)
        conn = QPushButton("Connect")
        conn.clicked.connect(self.connect_hub)
        save = QPushButton("Save to hub")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self.save_hub)
        dfl = QPushButton("Defaults")
        dfl.clicked.connect(self.defaults)
        top.addWidget(brand)
        top.addStretch(1)
        top.addWidget(conn)
        top.addWidget(self.sim_cb)
        top.addWidget(self.status, 1)
        top.addWidget(save)
        top.addWidget(dfl)
        v.addLayout(top)

        self.tabs = QTabWidget()
        self.axes = [AxisTab(self, a) for a in range(3)]
        for t, n in zip(self.axes, P.PEDAL_AXES):
            self.tabs.addTab(t, n)
        v.addWidget(self.tabs, 1)

        self.plot = Plot()
        v.addWidget(self.plot)

        g = QGroupBox("Pedals power / system")
        h = QHBoxLayout(g)
        self.idle = QSpinBox(minimum=0, maximum=255, value=20, suffix=" min")
        self.idle.setSpecialValueText("never")
        sidle = QPushButton("Set auto-off")
        sidle.clicked.connect(lambda: self.hub_call("dev_cmd", P.DEV_PEDALS, P.DEVCMD_SET_IDLE, self.idle.value()))
        slp = QPushButton("Sleep pedals now")
        slp.clicked.connect(lambda: self.hub_call("dev_cmd", P.DEV_PEDALS, P.DEVCMD_SLEEP, 0))
        boot = QPushButton("Hub -> bootloader (firmware update)")
        boot.clicked.connect(self.boot)
        prof_s = QPushButton("Save profile…")
        prof_s.clicked.connect(self.save_profile)
        prof_l = QPushButton("Load profile…")
        prof_l.clicked.connect(self.load_profile)
        for w in (QLabel("Auto-off:"), self.idle, sidle, slp, prof_s, prof_l, boot):
            h.addWidget(w)
        v.addWidget(g)

        self.timer = QTimer(self, interval=33, timeout=self.tick)
        QTimer.singleShot(200, self.connect_hub)

    # ----- hub access
    def hub_call(self, name, *a):
        if not self.hub:
            self.status.setText("not connected")
            return None
        try:
            return getattr(self.hub, name)(*a)
        except HubError as e:
            self.status.setText("error: %s" % e)
        except Exception as e:      # device unplugged etc.
            self.status.setText("lost connection: %s" % e)
            self.disconnect()
        return None

    def disconnect(self):
        self.timer.stop()
        if self.hub:
            try:
                self.hub.close()
            except Exception:
                pass
        self.hub = None

    def connect_hub(self):
        self.disconnect()
        try:
            hub = SimHub() if self.sim_cb.isChecked() else RealHub()
            hub.open()
            self.hub = hub
            info = hub.info()
            for a, t in enumerate(self.axes):
                t.load(hub.get_axis(P.DEV_PEDALS, a))
                t.prev = AxisState()
            self.idle.setValue(info["idle_min"])
            hub.stream(True)
            self.status.setText("hub fw %s%s" % (info["fw"], " (simulator)" if self.sim_cb.isChecked() else ""))
            self.timer.start()
        except Exception as e:
            self.hub = None
            self.status.setText("connect failed: %s" % e)

    def send_axis(self, a, cfg):
        self.hub_call("set_axis", P.DEV_PEDALS, a, cfg)

    def tare(self, a):
        self.hub_call("tare", P.DEV_PEDALS, a)
        cfg = self.hub_call("get_axis", P.DEV_PEDALS, a)
        if cfg:
            self.axes[a].load(cfg)

    def save_hub(self):
        if not self.hub:
            self.status.setText("not connected")
            return
        try:
            self.hub.save()
        except Exception as e:
            self.status.setText("save failed: %s" % e)
        else:
            self.status.setText("saved to hub flash")

    def defaults(self):
        if QMessageBox.question(self, "Defaults", "Reset all axes to factory defaults?") == QMessageBox.Yes:
            self.hub_call("defaults")
            self.connect_hub()

    def boot(self):
        if QMessageBox.question(self, "Bootloader", "Reboot the hub into the UF2 bootloader?\n"
                                "It will appear as a USB drive; copy the .uf2 file onto it.") == QMessageBox.Yes:
            self.hub_call("bootloader")
            self.disconnect()
            self.status.setText("hub rebooted into bootloader")

    def tick(self):
        if not self.hub:
            return
        try:
            devs = self.hub.poll_live()
        except Exception as e:
            self.status.setText("lost connection: %s" % e)
            self.disconnect()
            return
        if devs is None:
            if not self.hub.connected():
                self.status.setText("lost connection")
                self.disconnect()
            return
        st = getattr(self.hub, "stats", None)
        stxt = ""
        if st:
            stxt = " | radio rx=%d bad=%d ack=%d" % (st["rx_ok"], st["rx_bad"], st["tx_ok"])
            if st["rx_bad"]:
                stxt += " (last bad: len %d, byte0 0x%02X)" % (st["bad_len"], st["bad_b0"])
        if not devs:
            self.status.setText("hub OK, no pedals heard yet" + stxt)
            return
        for d in devs:
            if d.dev_type != P.DEV_PEDALS:
                continue
            for a, t in enumerate(self.axes):
                if a < len(d.raw):
                    t.live(d.raw[a], d.out[a])
            self.plot.push(d.out)
            txt = "pedals: %s | %d pkt/s" % ("LINK OK" if d.alive else "NO LINK", d.pps)
            if d.batt_mv:
                txt += " | battery %.2f V%s" % (d.batt_mv / 1000, " (LOW)" if d.low_batt else "")
            if d.sensor_err:
                txt += " | ADS1220 ERROR"
                if d.diag:
                    diag = d.diag
                    names = {1: "GPIO/device not ready", 2: "register readback mismatch",
                             3: "DRDY timeout", 4: "GPIO SPI transfer error"}
                    r0, r1, r2, r3 = diag["regs"]
                    txt += " [%s; regs %02X %02X %02X %02X; DRDY %s; tries %d]" % (
                        names.get(diag["error"], "error %d" % diag["error"]), r0, r1, r2, r3,
                        "LOW (active)" if diag["drdy_active"] else "HIGH (idle)", diag["tries"])
                else:
                    txt += " [diagnostic extension missing; check Hub/firmware pair]"
            self.status.setText(txt + stxt)

    # ----- profiles
    def save_profile(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save profile", "pedals_profile.json", "JSON (*.json)")
        if path:
            data = {"axes": [{**t.cfg.__dict__} for t in self.axes], "idle": self.idle.value()}
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except OSError as e:
                QMessageBox.critical(self, "Profile save failed", str(e))

    def load_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load profile", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            axes = data["axes"]
            if not isinstance(axes, list) or len(axes) != 3:
                raise ValueError("Profile must contain exactly three pedal axes")
            configs = [P.AxisCfg(**item) for item in axes]
            for t, cfg in zip(self.axes, configs):
                t.load(cfg)
                t.push()
            self.idle.setValue(int(data.get("idle", 20)))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, "Profile load failed", str(e))

    def closeEvent(self, e):
        self.hub_call("stream", False)
        self.disconnect()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    w = Main(sim="--sim" in sys.argv)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
