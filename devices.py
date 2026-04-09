"""
Direct APT protocol driver for Thorlabs TDC001 + MTS50/M.
Windows version — uses pyserial (FTDI VCP driver creates COM ports).
"""

import struct
import time
import threading

import serial
from serial.tools.list_ports import comports

# MTS50/M-Z8 constants (from Thorlabs datasheet HA0210T Rev K)
COUNTS_PER_MM = 34304
TIME_UNIT = 2048 / 6e6

# MTS50/M-Z8 stage limits
TRAVEL_MIN = 0.0       # mm
TRAVEL_MAX = 50.0      # mm
VEL_MAX = 2.4          # mm/s
ACC_MAX = 4.5           # mm/s^2
MIN_STEP = 0.0008      # mm (0.8 um min repeatable incremental movement)
BACKLASH = 0.006       # mm (< 6 um)
HOME_ACCURACY = 0.004  # mm (+/- 4.0 um)

# Thorlabs FTDI
VID = 0x0403
PID = 0xFAF0

# APT message IDs
HW_REQ_INFO = 0x0005
HW_STOP_UPDATEMSGS = 0x0012
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
MOT_REQ_DCSTATUSUPDATE = 0x0490
MOT_GET_DCSTATUSUPDATE = 0x0491
MOT_SET_ENASTATE = 0x0210
MOT_MOVE_HOMED = 0x0444
MOD_IDENTIFY = 0x0223

# EndPoints
HOST = 0x01
RACK = 0x50
BAY0 = 0x21


def mm_to_counts(mm):
    return int(mm * COUNTS_PER_MM)


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


def find_devices():
    """List connected Thorlabs APT devices on Windows COM ports."""
    devices = []
    for p in comports():
        if p.vid == VID and p.pid == PID:
            devices.append({
                "port": p.device,
                "serial_number": p.serial_number or "",
                "description": p.description or "APT Device",
            })
    return devices


class MotorStage:
    """
    Direct APT protocol driver for TDC001 + MTS50/M stage.
    All positions in mm, velocity in mm/s, acceleration in mm/s^2.
    """

    def __init__(self, serial_port, home=False):
        self.serial_port = serial_port
        self._port = serial.Serial(
            serial_port, baudrate=115200, bytesize=8,
            parity="N", stopbits=1, timeout=0.1
        )
        self._port.reset_input_buffer()
        self._port.reset_output_buffer()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._status = {
            "position": 0, "velocity": 0,
            "forward_limit_switch": False, "reverse_limit_switch": False,
            "moving_forward": False, "moving_reverse": False,
            "homing": False, "homed": False, "settled": False,
            "motor_connected": False, "channel_enabled": False,
            "motion_error": False,
        }
        self._vel_max = 0
        self._vel_acc = 0

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        time.sleep(0.5)
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
        if len(data) < 20:
            return
        msg_id = struct.unpack("<H", data[0:2])[0]
        if msg_id != MOT_GET_DCSTATUSUPDATE:
            return
        pos = struct.unpack("<i", data[8:12])[0]
        vel = struct.unpack("<H", data[12:14])[0]
        flags = struct.unpack("<I", data[16:20])[0]
        self._status["position"] = pos
        self._status["velocity"] = vel
        self._status["forward_limit_switch"] = bool(flags & 0x01)
        self._status["reverse_limit_switch"] = bool(flags & 0x02)
        self._status["moving_forward"] = bool(flags & 0x10)
        self._status["moving_reverse"] = bool(flags & 0x20)
        self._status["homing"] = bool(flags & 0x200)
        self._status["homed"] = bool(flags & 0x400)
        self._status["settled"] = bool(flags & 0x2000)
        self._status["motor_connected"] = bool(flags & 0x100000)
        self._status["channel_enabled"] = bool(flags & 0x80000000)
        self._status["motion_error"] = bool(flags & 0x1000)

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
    def position_mm(self):
        return counts_to_mm(self._status["position"])

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
        position_mm = max(TRAVEL_MIN, min(TRAVEL_MAX, position_mm))
        counts = mm_to_counts(position_mm)
        payload = struct.pack("<hi", 0x0001, counts)
        self._send_long(MOT_MOVE_ABSOLUTE, payload)

    def move_relative(self, distance_mm):
        target = self.position_mm + distance_mm
        target = max(TRAVEL_MIN, min(TRAVEL_MAX, target))
        counts = mm_to_counts(target - self.position_mm)
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
        stop_mode = 0x02
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
