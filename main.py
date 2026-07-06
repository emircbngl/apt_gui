#!/usr/bin/env python3
"""
Thorlabs APT Stage Controller
Replaces legacy Thorlabs APT software for TDC001/KDC101 + MTS50/M.

Usage:
    python main.py            GUI'yi başlatır
    python main.py --diag     Cihaz algılama teşhisi yazar (özellikle Windows)
"""

import sys


def _print_diagnostics():
    """Print everything relevant to device detection. Paste this when a device
    isn't being found so the cause is unambiguous."""
    import platform
    print("=" * 60)
    print("Thorlabs APT — Cihaz Algılama Teşhisi")
    print("=" * 60)
    print("Platform :", platform.platform())
    print("Python   :", sys.version.split()[0], platform.machine())

    from devices import find_devices, diagnose, VID, PID

    print("\n--- find_devices() ---")
    devs = find_devices()
    print(f"Bulunan: {len(devs)}")
    for d in devs:
        print(f"  {d['port']}  SN={d['serial_number']}  "
              f"pid={d['pid']}  kind={d['kind']}  ({d['description']})")

    print("\n--- Tüm seri portlar (pyserial) ---")
    try:
        from serial.tools.list_ports import comports
        ports = list(comports())
        if not ports:
            print("  (hiç COM/tty portu yok)")
        for p in ports:
            print(f"  {p.device}  VID={p.vid} PID={p.pid}  "
                  f"SN={p.serial_number}  desc={p.description}")
    except Exception as e:
        print("  pyserial hatası:", repr(e))

    print("\n--- FTDI D2XX (Windows) ---")
    try:
        import ftd2xx
        n = ftd2xx.createDeviceInfoList()
        print(f"  ftd2xx kurulu. D2XX {n} cihaz görüyor:")
        for i in range(n):
            try:
                info = ftd2xx.getDeviceInfoDetail(i)
                print(f"   [{i}] id=0x{info.get('id',0):08x} "
                      f"serial={info.get('serial')} desc={info.get('description')}")
            except Exception as e:
                print(f"   [{i}] okunamadı: {e!r}")
    except ImportError:
        print("  ftd2xx paketi KURULU DEĞİL  ->  pip install ftd2xx")
    except Exception as e:
        print("  ftd2xx hatası:", repr(e))

    print("\n--- diagnose() mesajı ---")
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
