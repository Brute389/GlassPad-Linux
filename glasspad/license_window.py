"""LicenseWindow — ported from GlassPad 1.0.2 (LicenseWindow.xaml[.cs]).

Three tabs (GlassPad / HIDMaestro / Third-party notices) reading embedded text.
On Windows the texts are EmbeddedResources; here they ship as data files next
to the package, resolved from the install tree, with the source checkout as a
fallback so it works from a git clone too.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
#: install layout: .../share/glasspad/licenses   (PKGBUILD)
_DATA_CANDIDATES = [
    os.path.join(HERE, "licenses"),
    os.path.join(os.path.dirname(os.path.dirname(HERE)), "licenses"),
    "/usr/share/glasspad/licenses",
    os.path.join(os.path.dirname(HERE), "licenses"),
]
_TABS = (
    ("GlassPad", "GlassPad-LICENSE.txt"),
    ("HIDMaestro", "HIDMaestro-LICENSE.txt"),
    ("Third-party notices", "THIRD-PARTY-NOTICES.txt"),
)


def licenses_dir() -> str | None:
    for folder in _DATA_CANDIDATES:
        if os.path.isdir(folder):
            return folder
    return None


def read_license(name: str) -> str:
    folder = licenses_dir()
    if folder is None:
        return "License texts were not installed with GlassPad."
    filename = dict(_TABS)[name] if name in dict(_TABS) else name
    path = os.path.join(folder, filename)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError as exc:
        return f"Could not read {path}: {exc}"


class LicenseWindow(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GlassPad \u2014 Open-source licenses")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.resize(760, 600)
        self.setMinimumSize(360, 300)
        self.setStyleSheet("background: #1A1A2E;")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(0)

        title = QLabel("Open-source licenses")
        title.setStyleSheet("color: white; font-size: 22px; font-weight: 600;")
        root.addWidget(title)
        root.addSpacing(16)

        tabs = QTabWidget()
        tabs.setStyleSheet(
            "QTabWidget::pane { border: 0; background: #202038; }"
            "QTabBar::tab { background: #2A2A46; color: #C8D4EE;"
            " padding: 8px 14px; border: 0; }"
            "QTabBar::tab:selected { background: #202038; color: white; }"
        )
        for name, _ in _TABS:
            view = QTextEdit()
            view.setReadOnly(True)
            view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            view.setFont(QFont("Sans Serif", 10))
            view.setStyleSheet(
                "QTextEdit { background: #202038; color: #EEF2FF; border: 0;"
                " padding: 18px; }"
            )
            view.setPlainText(read_license(name))
            tabs.addTab(view, name)
        tabs.setCurrentIndex(1)  # HIDMaestro selected, as in the XAML
        root.addWidget(tabs, 1)

        root.addSpacing(16)
        close = QPushButton("Close")
        close.setFixedSize(100, 34)
        close.setStyleSheet(
            "QPushButton { color: white; border: 0; border-radius: 6px;"
            " background: #404055; }"
            "QPushButton:hover { background: #505068; }"
        )
        close.clicked.connect(self.close)
        root.addWidget(_right_aligned(close))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


def _right_aligned(widget: QWidget) -> QWidget:
    """WPF: HorizontalAlignment="Right" on the Close button."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addStretch(1)
    layout.addWidget(widget)
    return holder
