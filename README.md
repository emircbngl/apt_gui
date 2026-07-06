# Thorlabs APT Stage Controller

A modern PyQt5 GUI that replaces the legacy Thorlabs APT / Kinesis software.
Controls **3× Thorlabs TDC001 / KDC101 controllers + MTS50/M-Z8 stages
(50 mm)** simultaneously. A single codebase runs on both **macOS** and
**Windows**.

It speaks the APT protocol directly (`devices.py`) — no dependency on
`thorlabs-apt-device` or other wrapper layers.

## Features

- Control 3 motors side by side (color-coded panels)
- Live position and status indicators (home / motion / enabled)
- Absolute positioning, relative moves, jog, and home
- **Homing progress feedback**: the Home light shows orange while homing runs
  (the stage drives to the limit switch and back, 15–60 s), green when homed
- **"Set Home"**: define the current position as 0.0000 mm (a software offset —
  the hardware encoder is untouched, so the physical 0–50 mm limits are
  preserved); ↺ returns to hardware coordinates
- Adjustable velocity and acceleration with hardware-limit clamping and
  status-bar warnings for out-of-range requests
- Automatic device discovery + **diagnostics that explain why nothing was
  found** (power / cable / driver checklist)
- Built-in **Guide** dialog: hardware specs, verified accuracy figures,
  light meanings, usage tips
- Per-motor nicknames persisted **by serial number** (a name follows its
  physical motor across USB ports), stored in `config.json`

## Connection method per platform

| Platform | Method | Driver |
|----------|--------|--------|
| macOS    | libusb (pyftdi) | No extra driver needed — Thorlabs' custom PID (0xFAF0) is not claimed by Apple's FTDI driver, so it is reachable directly through libusb. Requires `brew install libusb`. |
| Windows  | FTDI **D2XX** (`ftd2xx`), COM fallback | The Thorlabs Kinesis/APT installer sets up the FTDI **D2XX** driver, under which the device does **not** appear as a COM port; it is accessed directly through D2XX. (If the VCP driver is installed instead, COM ports are tried as a fallback.) Requires `pip install ftd2xx`; the D2XX DLL is already present if Kinesis is installed. |

Devices enumerate as `VID 0x0403 : PID 0xFAF0` ("APT DC Motor Controller",
manufacturer "Thorlabs").

## Installation

### Common (Python 3.9+)

```bash
pip install -r requirements.txt
```

### macOS extra step

```bash
brew install libusb
```

### Windows
Double-click `INSTALL.bat` — it downloads Python if missing, installs the
dependencies, and optionally builds a standalone single-file `.exe`.

## Running

```bash
python main.py
```

On Windows, `RUN.bat` (runs the built `.exe` if present, otherwise
`python main.py`).

Usage: **Scan Devices → Connect All → Home / move**. If a scan finds 0
devices, a dialog explains why (usually USB/power cabling).

## Building a standalone Windows .exe

```batch
build_windows.bat
```

Produces `dist\ThorlabsAPT.exe`, which can be copied to any Windows PC —
no Python required.

## Project structure

| File | Purpose |
|------|---------|
| `main.py` | Entry point (`--diag` prints detection diagnostics) |
| `gui.py` | PyQt5 interface |
| `devices.py` | Cross-platform APT protocol driver + discovery + `diagnose()` |
| `config.json` | Motor nicknames (keyed by serial number) |
| `requirements.txt` | Python dependencies |
| `INSTALL.bat` / `RUN.bat` / `build_windows.bat` | Windows helper scripts |

## Stage (MTS50/M-Z8) specifications

- Travel range: 0–50 mm
- Max velocity: 2.4 mm/s
- Max acceleration: 4.5 mm/s²
- Min repeatable incremental movement: 0.0008 mm (0.8 µm) — verified on
  hardware (measured 0.76–0.82 µm steps)
- Min achievable incremental movement: 0.0001 mm (0.1 µm) — not repeatable
- Encoder resolution: 34,554.96 counts/mm (official Z8-series scale, same as
  Kinesis; 1 count ≈ 0.0289 µm)
- Backlash: < 6 µm (invisible to the on-screen readout — the encoder sits on
  the motor; approach targets from the same direction for best repeatability)
- Home location accuracy: ±4 µm

## If no devices are found (troubleshooting)

1. **Power:** Is the T-Cube/K-Cube connected to external power (power supply
   or hub)? It does not run on USB alone; without power it does not appear
   on USB at all.
2. **Cable:** Is the USB cable a **data** cable? Charge-only cables power the
   device but have no data lines → invisible.
3. **Connection:** Try a different USB port/cable, ideally directly into the
   computer (no hub).
4. **Busy:** Legacy Thorlabs APT/Kinesis software locks the device while
   running — close it.
5. On macOS make sure `brew install libusb` is installed.
6. **On Windows** (most common cause): the Thorlabs D2XX driver is used and
   the device does not appear as a COM port. Make sure `pip install ftd2xx`
   is done and Kinesis/APT software is **closed**.

### Diagnostics command

If devices are still not found, run this and share the output — it makes the
cause unambiguous:

```bash
python main.py --diag
```

The in-app "Scan Devices" also shows a dialog summarizing these checks.
