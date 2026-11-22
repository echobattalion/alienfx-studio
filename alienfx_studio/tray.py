"""System tray icon for one-click profile switching."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


def app_icon():
    icon = QIcon.fromTheme("input-keyboard")
    if not icon.isNull():
        return icon
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FF00FF"))
    p.drawRoundedRect(2, 7, 28, 18, 4, 4)
    p.end()
    return QIcon(pix)


class Tray(QSystemTrayIcon):
    def __init__(self, window, quit_fn):
        super().__init__(app_icon(), window)
        self.window = window
        self.setToolTip("AlienFX Lighting Studio")
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.rebuild)
        self.quit_fn = quit_fn
        self.setContextMenu(self.menu)
        self.activated.connect(self._activated)
        self.rebuild()

    def rebuild(self):
        self.menu.clear()
        group = QActionGroup(self.menu)
        current = self.window.profile.name
        for _, name in self.window.store.list():
            action = QAction(name, self.menu, checkable=True, checked=name == current)
            action.triggered.connect(lambda _=False, n=name: self.window.switch_profile(n))
            group.addAction(action)
            self.menu.addAction(action)
        self.menu.addSeparator()
        self.menu.addAction("Open Editor", self.window.open_editor)
        self.menu.addAction("Quit", self.quit_fn)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.window.isVisible():
                self.window.hide()
            else:
                self.window.present()
