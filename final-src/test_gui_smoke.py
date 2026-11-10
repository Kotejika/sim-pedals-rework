import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from app import Main
from device import SimHub
from widgets import BackgroundWidget

app = QApplication.instance() or QApplication([])
window = Main(sim=True)
try:
    window.connect_hub()
    assert isinstance(window.hub, SimHub), "simulator did not connect"
    window.tick()
    assert isinstance(window.centralWidget(), BackgroundWidget), "background widget is missing"
    assert window.centralWidget()._renderer.isValid(), "anime SVG did not load"
    assert len(window.plot.data) == 3, "live plot axes were not initialized"
finally:
    window.close()
    app.processEvents()
print("Desktop GUI smoke test OK")
