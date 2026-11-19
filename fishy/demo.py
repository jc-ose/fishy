"""Milestone 1 layout spike: view sample fishbones with live style switching.

Run with:  uv run python -m fishy.demo
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QToolBar,
)

from fishy.layout import PRESETS, layout_fishbone
from fishy.samples import SAMPLES
from fishy.views.diagram_view import DiagramScene, DiagramView, qt_measure


class DemoWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("fishy — layout spike")
        self.resize(1400, 850)
        self.measure = qt_measure()

        self.scene = DiagramScene(self)
        self.view = DiagramView(self.scene, self)
        self.setCentralWidget(self.view)
        self.scene.boneSelected.connect(self._on_bone_selected)

        bar = QToolBar("Diagram", self)
        bar.setMovable(False)
        self.addToolBar(bar)

        self.sample_box = QComboBox()
        self.sample_box.addItems(list(SAMPLES))
        self.sample_box.currentTextChanged.connect(self._load_sample)
        bar.addWidget(QLabel(" Sample: "))
        bar.addWidget(self.sample_box)

        self.style_box = QComboBox()
        self.style_box.addItems(list(PRESETS))
        self.style_box.currentTextChanged.connect(lambda _: self.relayout())
        bar.addWidget(QLabel("  Style: "))
        bar.addWidget(self.style_box)
        bar.addSeparator()

        self.status_action = QAction("Status dots", self, checkable=True, checked=True)
        self.status_action.toggled.connect(lambda _: self.relayout())
        bar.addAction(self.status_action)

        fit = QAction("Fit", self, shortcut=QKeySequence("Ctrl+0"))
        fit.triggered.connect(self.view.fit_all)
        bar.addAction(fit)
        bar.addSeparator()
        for fmt in ("PNG", "SVG", "PDF"):
            act = QAction(f"Export {fmt}", self)
            act.triggered.connect(lambda _=False, f=fmt: self._export(f))
            bar.addAction(act)

        self.info = QLabel()
        self.statusBar().addPermanentWidget(self.info)
        self._load_sample(self.sample_box.currentText())

    def _load_sample(self, name: str) -> None:
        self.problem, self.categories = SAMPLES[name]()
        self.relayout()
        self.view.fit_all()

    def relayout(self) -> None:
        style = replace(PRESETS[self.style_box.currentText()],
                        show_status=self.status_action.isChecked())
        t0 = time.perf_counter()
        diagram = layout_fishbone(self.problem, self.categories, style, self.measure)
        t1 = time.perf_counter()
        self.scene.set_diagram(diagram)
        t2 = time.perf_counter()
        bones = sum(1 for lab in diagram.labels if lab.bone_id is not None)
        self.info.setText(f"{bones} bones · layout {1000 * (t1 - t0):.1f} ms · "
                          f"scene {1000 * (t2 - t1):.1f} ms ")

    def _on_bone_selected(self, bone_id: int | None) -> None:
        if bone_id is None:
            self.statusBar().clearMessage()
            return
        item = self.scene._labels.get(bone_id)
        if item:
            self.statusBar().showMessage(f"Selected bone #{bone_id}: {item.label.text}")

    def _export(self, fmt: str) -> None:
        ext = fmt.lower()
        path, _ = QFileDialog.getSaveFileName(self, f"Export {fmt}", f"fishbone.{ext}",
                                              f"{fmt} (*.{ext})")
        if not path:
            return
        getattr(self.view, f"export_{ext}")(path)
        self.statusBar().showMessage(f"Exported {path}", 5000)


def main() -> int:
    app = QApplication(sys.argv)
    win = DemoWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
