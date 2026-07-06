#!/usr/bin/env python3
"""
Thorlabs APT Stage Controller
Replaces legacy Thorlabs APT software for TDC001/KDC101 + MTS50/M.

Usage:
    python main.py            starts the GUI
    python main.py --diag     prints device-detection diagnostics (esp. Windows)
"""

import sys


def _print_diagnostics():
    """Print everything relevant to device detection. Paste this when a device
    isn't being found so the cause is unambiguous."""
    import platform
    print("=" * 60)
    print("Thorlabs APT — Device Detection Diagnostics")
    print("=" * 60)
    print("Platform :", platform.platform())
    print("Python   :", sys.version.split()[0], platform.machine())

    from devices import find_devices, diagnose, VID, PID

    print("\n--- find_devices() ---")
    devs = find_devices()
    print(f"Found: {len(devs)}")
    for d in devs:
        print(f"  {d['port']}  SN={d['serial_number']}  "
              f"pid={d['pid']}  kind={d['kind']}  ({d['description']})")

    print("\n--- All serial ports (pyserial) ---")
    try:
        from serial.tools.list_ports import comports
        ports = list(comports())
        if not ports:
            print("  (no COM/tty ports at all)")
        for p in ports:
            print(f"  {p.device}  VID={p.vid} PID={p.pid}  "
                  f"SN={p.serial_number}  desc={p.description}")
    except Exception as e:
        print("  pyserial error:", repr(e))

    print("\n--- FTDI D2XX (Windows) ---")
    try:
        import ftd2xx
        n = ftd2xx.createDeviceInfoList()
        print(f"  ftd2xx installed. D2XX sees {n} device(s):")
        for i in range(n):
            try:
                info = ftd2xx.getDeviceInfoDetail(i)
                print(f"   [{i}] id=0x{info.get('id',0):08x} "
                      f"serial={info.get('serial')} desc={info.get('description')}")
            except Exception as e:
                print(f"   [{i}] unreadable: {e!r}")
    except ImportError:
        print("  ftd2xx package NOT INSTALLED  ->  pip install ftd2xx")
    except Exception as e:
        print("  ftd2xx error:", repr(e))

    print("\n--- diagnose() message ---")
    print(diagnose()["message"])
    print("=" * 60)


def main():
    if "--diag" in sys.argv or "-d" in sys.argv:
        _print_diagnostics()
        return

    from PyQt5.QtWidgets import QApplication
    from gui import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
