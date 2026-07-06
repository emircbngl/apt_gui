"""
Direct APT protocol driver for Thorlabs TDC001 / KDC101 + MTS50/M stages.

Cross-platform device discovery:
  * macOS  -> libusb via pyftdi (Thorlabs custom PID 0xFAF0 is NOT claimed by
              Apple's FTDI VCP driver, so it is reachable through libusb),
              with a /dev/cu.usbserial-* fallback via pyserial.
  * Windows/Linux -> pyserial COM/tty ports (FTDI VCP driver creates them).

No dependency on thorlabs-apt-device.
"""

import re
import sys
import struct
import time
import threading

# ── MTS50/M-Z8 constants ─────────────────────────────────────────────────
# Z8-series scaling per the official APT protocol scaling chapter and Kinesis:
# 512 counts/motor-rev x 67.49:1 gearhead x 1 mm leadscrew = 34554.96 counts/mm
# (the widespread 34304 = 512 x 67 is an old integer approximation with ~0.73%
# physical scale error — ~363 um over the full 50 mm travel).
# Derived factors then match the documented Z8 values:
#   velocity 772,981.37 dev-units/(mm/s), acceleration 263.84 dev-units/(mm/s^2)
COUNTS_PER_MM = 34554.96
TIME_UNIT = 2048 / 6e6

# MTS50/M-Z8 stage limits
TRAVEL_MIN = 0.0       # mm
TRAVEL_MAX = 50.0      # mm
VEL_MAX = 2.4          # mm/s
ACC_MAX = 4.5          # mm/s^2
MIN_STEP = 0.0008      # mm (0.8 um min repeatable incremental movement)
BACKLASH = 0.006       # mm (< 6 um)
HOME_ACCURACY = 0.004  # mm (+/- 4.0 um)

# ── USB identifiers ──────────────────────────────────────────────────────
# FTDI vendor id used by every APT motion controller (TDC001, KDC101, BSC, ...).
# APT controllers are ALWAYS FTDI-based; Thorlabs' own VID 0x1313 is for cameras
# and power meters, not motion stages, so we deliberately do not scan it.
VID = 0x0403
# Product id the TDC001/KDC101 ship with out of the box.
PID = 0xFAF0
# Known APT product ids. We still accept *any* FTDI pid (see _is_apt_pid) so a
# differently-configured cube is discovered instead of silently ignored.
APT_PIDS = {0xFAF0, 0xFAF1, 0xFAF2, 0xFAF3, 0xFAF4, 0xFAF5, 0xFAF6}

# ── APT message ids ──────────────────────────────────────────────────────
HW_REQ_INFO = 0x0005
HW_GET_INFO = 0x0006
HW_START_UPDATEMSGS = 0x0011
HW_STOP_UPDATEMSGS = 0x0012
HW_DISCONNECT = 0x0002
MOT_MOVE_HOME = 0x0443
MOT_MOVE_ABSOLUTE = 0x0453
MOT_MOVE_RELATIVE = 0x0448
MOT_MOVE_JOG = 0x046A
MOT_MOVE_VELOCITY = 0x0457
MOT_MOVE_STOP = 0x0465
MOT_SET_VELPARAMS = 0x0413
MOT_REQ_VELPARAMS = 0x0414
MOT_GET_VELPARAMS = 0x0415
MOT_SET_JOGPARAMS = 0x0416
MOT_REQ_JOGPARAMS = 0x0417
MOT_GET_JOGPARAMS = 0x0418
MOT_REQ_DCSTATUSUPDATE = 0x0490
MOT_GET_DCSTATUSUPDATE = 0x0491
MOT_ACK_DCSTATUSUPDATE = 0x0492
MOT_SET_ENASTATE = 0x0210
MOT_REQ_ENASTATE = 0x0211
MOT_MOVE_HOMED = 0x0444
MOD_IDENTIFY = 0x0223

# EndPoints
HOST = 0x01
RACK = 0x50
BAY0 = 0x21


def _is_macos():
    return sys.platform == "darwin"


def _is_windows():
    return sys.platform.startswith("win")


def _is_apt_pid(pid):
    return pid in APT_PIDS


def _register_ftdi(vid, pid):
    """Register a custom FTDI product with pyftdi so its ftdi:// URL can be
    parsed/opened. pyftdi keys its registry by the *name*, so each (vid, pid)
    must get a UNIQUE name — otherwise registering a second PID under a shared
    name silently overwrites the first, making that device un-openable."""
    try:
        from pyftdi.ftdi import Ftdi
        Ftdi.add_custom_product(vid, pid, f"Thorlabs APT {vid:04x}:{pid:04x}")
    except ValueError:
        pass  # this exact PID value is already registered — fine
    except Exception:
        pass


# ── Unit conversions ─────────────────────────────────────────────────────

def mm_to_counts(mm):
    return int(round(mm * COUNTS_PER_MM))


def counts_to_mm(counts):
    return counts / COUNTS_PER_MM


def _vel_to_counts(mmps):
    return int(mmps * COUNTS_PER_MM * TIME_UNIT * 65536)


def _acc_to_counts(mmpsps):
    return int(mmpsps * COUNTS_PER_MM * TIME_UNIT * TIME_UNIT * 65536)


def _counts_to_vel(counts):
    return counts / (COUNTS_PER_MM * TIME_UNIT * 65536)


def _counts_to_acc(counts):
    return counts / (COUNTS_PER_MM * TIME_UNIT * TIME_UNIT * 65536)


# ── FTDI D2XX (Windows) ──────────────────────────────────────────────────
# Thorlabs Kinesis/APT installs the FTDI *D2XX* driver on Windows, under which
# the device does NOT appear as a COM port — so pyserial can't see it. We talk
# to it directly through the D2XX driver via the `ftd2xx` package, exposing a
# small pyserial-compatible shim so MotorStage doesn't care which path it got.

class _D2xxSerial:
    """Minimal pyserial-like wrapper over an FTDI D2XX handle."""

    def __init__(self, serial=None, index=0):
        import ftd2xx
        if serial:
            sn = serial.encode() if isinstance(serial, str) else serial
            if isinstance(sn, (bytes, bytearray)):
                sn = sn.split(b"\x00", 1)[0]   # openEx matches the serial EXACTLY
            self._h = ftd2xx.openEx(sn)
        else:
            self._h = ftd2xx.open(index)
        # FTDI init per Thorlabs APT protocol (Issue 24, Sec 2.1): 115200 8-N-1,
        # purge bracketed by 50 ms dwells, reset, THEN arm RTS/CTS and assert RTS.
        self._h.setBaudRate(115200)
        try:
            self._h.setDataCharacteristics(8, 0, 0)  # BITS_8, STOP_BITS_1, PARITY_NONE
        except Exception:
            pass
        time.sleep(0.05)
        try:
            self._h.purge(1 | 2)                     # PURGE_RX | PURGE_TX
        except Exception:
            pass
        time.sleep(0.05)
        try:
            self._h.resetDevice()
        except Exception:
            pass
        try:
            self._h.setFlowControl(0x0100, 0, 0)     # FLOW_RTS_CTS
        except Exception:
            pass
        try:
            self._h.setRts()                          # spec asserts RTS explicitly
        except Exception:
            pass
        try:
            self._h.setTimeouts(100, 100)            # ms (read, write) — bounds stalls
        except Exception:
            pass
        try:
            self._h.setLatencyTimer(1)               # 1 ms (FTDI default 16 ms is sluggish)
        except Exception:
            pass

    @classmethod
    def from_url(cls, url):
        rest = url[len("d2xx://"):]
        if rest.startswith("index/"):
            return cls(index=int(rest.split("/", 1)[1] or 0))
        return cls(serial=rest)

    def write(self, data):
        return self._h.write(bytes(data))

    def read(self, n):
        try:
            avail = self._h.getQueueStatus()
        except Exception:
            avail = 0
        if not avail:
            return b""
        return self._h.read(min(n, avail))

    def flush(self):
        pass

    def reset_input_buffer(self):
        try:
            self._h.purge(1)   # PURGE_RX
        except Exception:
            pass

    def reset_output_buffer(self):
        try:
            self._h.purge(2)   # PURGE_TX
        except Exception:
            pass

    def close(self):
        try:
            self._h.close()
        except Exception:
            pass


# ── Port open ────────────────────────────────────────────────────────────

def _open_port(serial_port):
    """Open a port. ftdi:// -> pyftdi (libusb); d2xx:// -> FTDI D2XX (Windows);
    anything else -> pyserial COM/tty port."""
    if serial_port.startswith("ftdi://"):
        # Register the exact vid:pid this URL points at (pyftdi refuses to parse
        # a URL whose product id it doesn't know), plus the default 0xFAF0.
        m = re.match(r"ftdi://0x([0-9a-fA-F]+):0x([0-9a-fA-F]+):", serial_port)
        if m:
            _register_ftdi(int(m.group(1), 16), int(m.group(2), 16))
        _register_ftdi(VID, PID)
        from pyftdi.serialext import serial_for_url
        port = serial_for_url(serial_port, baudrate=115200, timeout=0.1)
    elif serial_port.startswith("d2xx://"):
        port = _D2xxSerial.from_url(serial_port)
    else:
        import serial
        port = serial.Serial(serial_port, baudrate=115200, bytesize=8,
                             parity="N", stopbits=1, timeout=0.1)
    port.reset_input_buffer()
    port.reset_output_buffer()
    return port


# ── Discovery ────────────────────────────────────────────────────────────

def _find_serial_ports():
    """FTDI/APT devices exposed as OS serial ports (pyserial). Cross-platform."""
    found = []
    try:
        from serial.tools.list_ports import comports
    except Exception:
        return found
    for p in comports():
        if getattr(p, "vid", None) == VID:
            found.append({
                "port": p.device,
                "serial_number": getattr(p, "serial_number", "") or "",
                "description": (getattr(p, "product", None)
                                or getattr(p, "description", None) or "APT Device"),
                "pid": getattr(p, "pid", None),
                "kind": "serial",
            })
    return found


def _find_d2xx_devices():
    """FTDI devices via the D2XX driver (Windows / Thorlabs Kinesis). Works even
    when the device has no VCP COM port. Returns list of dicts."""
    found = []
    try:
        import ftd2xx
    except Exception:
        return found
    try:
        count = ftd2xx.createDeviceInfoList()
    except Exception:
        return found

    def _cstr(v):
        # D2XX 'serial'/'description' are NUL-padded C strings — strip padding, or
        # openEx() (exact serial match) will never find the device.
        if isinstance(v, (bytes, bytearray)):
            return v.split(b"\x00", 1)[0].decode(errors="ignore")
        return str(v or "")

    for i in range(count):
        try:
            # update=False: the list was already built by createDeviceInfoList()
            # above; update=True would re-scan the bus on every iteration.
            info = ftd2xx.getDeviceInfoDetail(i, update=False)
        except Exception:
            continue
        dev_id = info.get("id", 0) or 0
        vid = (dev_id >> 16) & 0xFFFF
        pid = dev_id & 0xFFFF
        if vid and vid != VID:
            continue  # a non-FTDI device somehow in the list
        sn = _cstr(info.get("serial", b""))
        desc = _cstr(info.get("description", b""))
        if not sn:
            continue  # can't address it reliably without a serial
        found.append({
            "port": f"d2xx://{sn}",
            "serial_number": sn,
            "description": desc or "APT Device",
            "pid": pid or None,
            "kind": "d2xx",
        })
    return found


def _find_ftdi_devices():
    """FTDI devices reachable through libusb (macOS path). Returns list of dicts."""
    found = []
    try:
        import usb.core
        import usb.util
    except Exception:
        return found

    try:
        devs = usb.core.find(find_all=True, idVendor=VID)
    except Exception:
        return found
    for d in devs or []:
        pid = d.idProduct
        _register_ftdi(VID, pid)  # unique-name registration so the URL opens
        # Best-effort string reads; a claimed device may refuse them.
        sn = ""
        desc = "Thorlabs APT" if _is_apt_pid(pid) else "FTDI device"
        try:
            if d.iSerialNumber:
                sn = usb.util.get_string(d, d.iSerialNumber) or ""
        except Exception:
            pass
        try:
            if d.iProduct:
                desc = usb.util.get_string(d, d.iProduct) or desc
        except Exception:
            pass
        # Prefer a serial-number URL; fall back to bus:address if unreadable.
        if sn:
            url = f"ftdi://0x{VID:04x}:0x{pid:04x}:{sn}/1"
        else:
            url = f"ftdi://0x{VID:04x}:0x{pid:04x}:{d.bus:x}:{d.address:x}/1"
        found.append({
            "port": url,
            "serial_number": sn,
            "description": desc,
            "pid": pid,
            "kind": "ftdi",
        })
    return found


def find_devices():
    """List connected Thorlabs APT devices. Returns a list of dicts:
    {port, serial_number, description, pid, kind}. Never raises."""
    devices = []
    seen = set()

    def _add(dev):
        key = dev.get("serial_number") or dev.get("port")
        if key in seen:
            return
        seen.add(key)
        devices.append(dev)

    if _is_macos():
        # Primary path on macOS: libusb (custom PID isn't grabbed by the VCP driver).
        for dev in _find_ftdi_devices():
            _add(dev)
        # Fallback: if a VCP driver *did* claim it, it shows up as /dev/cu.usbserial-*.
        for dev in _find_serial_ports():
            _add(dev)
    elif _is_windows():
        # Primary path on Windows: D2XX (Thorlabs Kinesis installs this driver,
        # under which the device has NO COM port and pyserial can't see it).
        for dev in _find_d2xx_devices():
            _add(dev)
        # Fallback: if the FTDI VCP driver is installed instead, it's a COM port.
        for dev in _find_serial_ports():
            _add(dev)
    else:
        for dev in _find_serial_ports():
            _add(dev)

    return devices


def diagnose():
    """Explain *why* discovery found what it found. Returns a dict with a
    human-readable `message` plus structured counts for the GUI."""
    info = {
        "platform": sys.platform,
        "usb_total": None,
        "ftdi_count": 0,
        "serial_ports": [],
        "matched": 0,
        "message": "",
    }

    matched = find_devices()
    info["matched"] = len(matched)

    # Count everything libusb can see (macOS) — helps distinguish "wrong PID"
    # from "no USB device at all".
    try:
        import usb.core
        all_usb = list(usb.core.find(find_all=True))
        info["usb_total"] = len(all_usb)
        info["ftdi_count"] = sum(1 for d in all_usb if d.idVendor == VID)
    except Exception:
        info["usb_total"] = None

    try:
        from serial.tools.list_ports import comports
        info["serial_ports"] = [p.device for p in comports()]
    except Exception:
        pass

    # Windows: is the D2XX driver present, and how many FTDI devices does it see?
    info["d2xx_available"] = None
    info["d2xx_count"] = None
    if _is_windows():
        try:
            import ftd2xx
            info["d2xx_available"] = True
            try:
                info["d2xx_count"] = ftd2xx.createDeviceInfoList()
            except Exception:
                pass
        except Exception:
            info["d2xx_available"] = False

    if matched:
        info["message"] = f"Found {len(matched)} Thorlabs device(s)."
        return info

    # Nothing matched — build actionable guidance.
    lines = ["No Thorlabs devices found.", ""]

    if _is_windows():
        if info["d2xx_available"] is False:
            lines += [
                "On Windows, Thorlabs devices talk through the D2XX driver, but",
                "the 'ftd2xx' package is not installed. Run in a command prompt:",
                "    pip install ftd2xx",
                "(or re-run INSTALL.bat).",
            ]
        elif info["d2xx_count"] == 0:
            lines += [
                "The D2XX driver sees NO FTDI devices at all. Check:",
                "  • Is the TDC001/KDC101 cube connected to external power and ON?",
                "  • Is the USB cable a DATA cable? (charge-only cables are invisible)",
                "  • Thorlabs Kinesis/APT software locks the device if open — CLOSE it.",
                "  • Does the device show up as FTDI/USB Serial in Device Manager?",
            ]
        else:
            lines += [
                f"D2XX sees {info['d2xx_count']} device(s) but none could be opened.",
                "Most likely the Thorlabs Kinesis/APT software is running and holding",
                "the device — close it and scan again.",
            ]
            if info["serial_ports"]:
                lines.append("Serial ports: " + ", ".join(info["serial_ports"]))
        info["message"] = "\n".join(lines)
        return info

    if info["usb_total"] == 0:
        lines += [
            "The operating system sees NO USB devices at all, so the problem is",
            "not in this app — the device is simply not visible over USB. Check:",
            "  • Is the TDC001/KDC101 cube connected to EXTERNAL POWER? (A T-Cube",
            "    does not run on USB alone; it needs a power supply or hub — with",
            "    no power it does not appear on USB at all.)",
            "  • Is the USB cable a DATA cable? (A charge-only cable powers the",
            "    device but has no data lines → invisible.)",
            "  • Cable/port OK? Try a different USB port/cable.",
        ]
    elif info["ftdi_count"] and not matched:
        lines += [
            f"{info['ftdi_count']} FTDI device(s) visible but none opened as APT.",
            "The device may be held by another driver/application",
            "(e.g. legacy Thorlabs APT/Kinesis software running). Close it.",
        ]
    else:
        lines += [
            "USB devices are visible but none is a Thorlabs FTDI (VID 0x0403).",
            "Make sure the device is actually plugged in and powered; try a",
            "different USB port/cable.",
        ]
        if info["serial_ports"]:
            lines.append("Serial ports seen: " + ", ".join(info["serial_ports"]))
    info["message"] = "\n".join(lines)
    return info


class MotorStage:
    """
    Direct APT protocol driver for TDC001/KDC101 + MTS50/M stage.
    All positions in mm, velocity in mm/s, acceleration in mm/s^2.
    """

    def __init__(self, serial_port, home=False, auto_enable=True):
        self.serial_port = serial_port
        self._port = _open_port(serial_port)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._status = {
            "position": 0, "velocity": 0, "enc_count": 0,
            "forward_limit_switch": False, "reverse_limit_switch": False,
            "moving_forward": False, "moving_reverse": False,
            "homing": False, "homed": False, "settled": False,
            "motor_connected": False, "channel_enabled": False,
            "motion_error": False, "motor_current_limit_reached": False,
        }
        self._vel_max = 0
        self._vel_acc = 0
        # User-defined zero ("set home"): a software offset in true mm. The
        # hardware encoder is never touched, so travel limits stay correct.
        self.zero_offset_mm = 0.0

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        time.sleep(0.5)

        # A freshly connected channel may be disabled -> moves are silently
        # ignored. Enable it so the user's first command actually runs.
        if auto_enable:
            self.set_enabled(True)

        self._request_vel_params()

        if home:
            self.home()

    def _send_short(self, msg_id, param1=0x01, param2=0x00, dest=BAY0, source=HOST):
        data = struct.pack("<HBBBB", msg_id, param1, param2, dest, source)
        with self._lock:
            self._port.write(data)
            self._port.flush()

    def _send_long(self, msg_id, payload):
        length = len(payload)
        header = struct.pack("<HH", msg_id, length)
        dest_src = struct.pack("<BB", BAY0 | 0x80, HOST)
        with self._lock:
            self._port.write(header + dest_src + payload)
            self._port.flush()

    def _read_response(self, timeout=0.5):
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                data = self._port.read(256)
            if data:
                buf += data
            elif buf:
                break
            time.sleep(0.02)
        return buf

    def _parse_status(self, data):
        """Parse MOT_GET_DCSTATUSUPDATE response (20 bytes)."""
        if len(data) < 20:
            return
        msg_id = struct.unpack("<H", data[0:2])[0]
        if msg_id != MOT_GET_DCSTATUSUPDATE:
            return
        pos = struct.unpack("<i", data[8:12])[0]
        vel = struct.unpack("<H", data[12:14])[0]
        flags = struct.unpack("<I", data[16:20])[0]

        # Bit masks verified empirically on TDC001 hardware (raw-flag capture
        # during move/jog/home) + APT protocol spec. Note TDC001 quirks:
        # it sets 0x10 for motion in BOTH directions and never sets the jog
        # bits — we still OR them in for other APT controllers.
        self._status["position"] = pos
        self._status["velocity"] = vel
        self._status["forward_limit_switch"] = bool(flags & 0x01)
        self._status["reverse_limit_switch"] = bool(flags & 0x02)
        self._status["moving_forward"] = bool(flags & (0x10 | 0x40))
        self._status["moving_reverse"] = bool(flags & (0x20 | 0x80))
        self._status["homing"] = bool(flags & 0x200)
        self._status["homed"] = bool(flags & 0x400)
        self._status["settled"] = bool(flags & 0x2000)
        self._status["motor_connected"] = bool(flags & 0x100)      # was 0x100000 (a digital-input bit)
        self._status["channel_enabled"] = bool(flags & 0x80000000)
        self._status["motion_error"] = bool(flags & 0x4000)        # was 0x1000 = TRACKING (not an error)

    def _parse_velparams(self, data):
        if len(data) < 20:
            return
        msg_id = struct.unpack("<H", data[0:2])[0]
        if msg_id != MOT_GET_VELPARAMS:
            return
        self._vel_acc = struct.unpack("<I", data[12:16])[0]
        self._vel_max = struct.unpack("<I", data[16:20])[0]

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                self._send_short(MOT_REQ_DCSTATUSUPDATE)
                time.sleep(0.05)
                resp = self._read_response(0.3)
                if resp:
                    self._try_parse_all(resp)
            except Exception:
                pass
            self._stop_event.wait(0.15)

    def _try_parse_all(self, data):
        i = 0
        while i < len(data) - 1:
            msg_id = struct.unpack("<H", data[i:i+2])[0]
            if msg_id == MOT_GET_DCSTATUSUPDATE and i + 20 <= len(data):
                self._parse_status(data[i:i+20])
                i += 20
            elif msg_id == MOT_GET_VELPARAMS and i + 20 <= len(data):
                self._parse_velparams(data[i:i+20])
                i += 20
            elif msg_id == MOT_MOVE_HOMED:
                i += 6
            else:
                i += 1

    def _request_vel_params(self):
        self._send_short(MOT_REQ_VELPARAMS)
        time.sleep(0.3)

    @property
    def status(self):
        return dict(self._status)

    @property
    def _raw_position_mm(self):
        """True position from the encoder (hardware reference, ignores zero)."""
        return counts_to_mm(self._status["position"])

    @property
    def position_mm(self):
        """Position relative to the user-set zero/home (see set_zero())."""
        return self._raw_position_mm - self.zero_offset_mm

    def set_zero(self):
        """Define the current position as 0 (home/reference). Pure software
        offset — the hardware encoder is untouched, so 0–50 mm travel limits
        stay correct and the true reference is preserved."""
        self.zero_offset_mm = self._raw_position_mm

    def reset_zero(self):
        """Drop the zero offset; positions return to hardware coordinates."""
        self.zero_offset_mm = 0.0

    @property
    def min_position_mm(self):
        """Lower travel bound in current (zeroed) coordinates."""
        return TRAVEL_MIN - self.zero_offset_mm

    @property
    def max_position_mm(self):
        """Upper travel bound in current (zeroed) coordinates."""
        return TRAVEL_MAX - self.zero_offset_mm

    @property
    def velocity_mmps(self):
        return _counts_to_vel(self._vel_max) if self._vel_max else 0.0

    @property
    def acceleration_mmpsps(self):
        return _counts_to_acc(self._vel_acc) if self._vel_acc else 0.0

    @property
    def is_homed(self):
        return self._status["homed"]

    @property
    def is_moving(self):
        return self._status["moving_forward"] or self._status["moving_reverse"]

    @property
    def is_enabled(self):
        return self._status["channel_enabled"]

    def home(self):
        self._send_short(MOT_MOVE_HOME)

    def move_absolute(self, position_mm):
        # position_mm is in user (zeroed) coords; convert to true coords + clamp.
        true_target = position_mm + self.zero_offset_mm
        true_target = max(TRAVEL_MIN, min(TRAVEL_MAX, true_target))
        counts = mm_to_counts(true_target)
        payload = struct.pack("<hi", 0x0001, counts)
        self._send_long(MOT_MOVE_ABSOLUTE, payload)

    def move_relative(self, distance_mm):
        # Relative deltas are offset-independent; clamp in true coordinates.
        true_current = self._raw_position_mm
        true_target = max(TRAVEL_MIN, min(TRAVEL_MAX, true_current + distance_mm))
        counts = mm_to_counts(true_target - true_current)
        payload = struct.pack("<hi", 0x0001, counts)
        self._send_long(MOT_MOVE_RELATIVE, payload)

    def jog(self, direction="forward"):
        d = 0x01 if direction == "forward" else 0x02
        self._send_short(MOT_MOVE_JOG, param1=0x01, param2=d)

    def move_velocity(self, direction="forward"):
        d = 0x01 if direction == "forward" else 0x02
        self._send_short(MOT_MOVE_VELOCITY, param1=0x01, param2=d)

    def stop(self, immediate=False):
        s = 0x01 if immediate else 0x02
        self._send_short(MOT_MOVE_STOP, param1=0x01, param2=s)

    def set_velocity(self, max_velocity_mmps, acceleration_mmpsps):
        max_velocity_mmps = min(max_velocity_mmps, VEL_MAX)
        acceleration_mmpsps = min(acceleration_mmpsps, ACC_MAX)
        vel = _vel_to_counts(max_velocity_mmps)
        acc = _acc_to_counts(acceleration_mmpsps)
        payload = struct.pack("<hIII", 0x0001, 0, acc, vel)
        self._send_long(MOT_SET_VELPARAMS, payload)
        self._vel_max = vel
        self._vel_acc = acc

    def set_jog_params(self, step_mm, max_velocity_mmps, acceleration_mmpsps, continuous=False):
        max_velocity_mmps = min(max_velocity_mmps, VEL_MAX)
        acceleration_mmpsps = min(acceleration_mmpsps, ACC_MAX)
        mode = 0x02 if continuous else 0x01
        step = mm_to_counts(step_mm)
        vel = _vel_to_counts(max_velocity_mmps)
        acc = _acc_to_counts(acceleration_mmpsps)
        stop_mode = 0x02  # profiled stop
        payload = struct.pack("<hHIIII", 0x0001, mode, step, vel, acc, stop_mode)
        self._send_long(MOT_SET_JOGPARAMS, payload)

    def set_enabled(self, enabled):
        state = 0x01 if enabled else 0x02
        self._send_short(MOT_SET_ENASTATE, param1=0x01, param2=state)

    def identify(self):
        self._send_short(MOD_IDENTIFY, param1=0x00, param2=0x00)

    def close(self):
        self._stop_event.set()
        try:
            self._send_short(HW_STOP_UPDATEMSGS)
        except Exception:
            pass
        time.sleep(0.2)
        try:
            self._port.close()
        except Exception:
            pass
