#!/usr/bin/env python3
"""
Thorlabs APT Stage Controller
Replaces legacy Thorlabs APT software for TDC001 + MTS50/M.
"""

import sys
from PyQt5.QtWidgets import QApplication
from gui import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
