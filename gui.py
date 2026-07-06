"""
PyQt5 GUI for controlling 3x TDC001/KDC101 + MTS50/M stages simultaneously.
Each motor has its own panel. Global controls at the top.
"""

import sys
import json
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QDoubleSpinBox, QComboBox,
    QFrame, QGridLayout, QMessageBox, QSizePolicy, QLineEdit,
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont

from devices import (
    MotorStage, find_devices, diagnose,
    TRAVEL_MIN, TRAVEL_MAX, VEL_MAX, ACC_MAX, MIN_STEP,
)


COLORS = ["#2196F3", "#4CAF50", "#FF9800"]  # Blue, Green, Orange
NAMES = ["Motor 1", "Motor 2", "Motor 3"]


class StatusIndicator(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self.set_inactive()

    def set_active(self):
        self.setStyleSheet("background-color:#4CAF50;border-radius:6px;border:1px solid #388E3C;")

    def set_inactive(self):
        self.setStyleSheet("background-color:#9E9E9E;border-radius:6px;border:1px solid #757575;")

    def set_warning(self):
        self.setStyleSheet("background-color:#FF9800;border-radius:6px;border:1px solid #F57C00;")

    def set_error(self):
        self.setStyleSheet("background-color:#F44336;border-radius:6px;border:1px solid #D32F2F;")


class MotorPanel(QGroupBox):
    """Self-contained panel for one motor stage."""

    def __init__(self, index, color, name, parent=None):
        super().__init__(parent)
        self.index = index
        self.color = color
        self.name = name
        self.stage = None
        self._serial_by_port = {}   # port URL -> serial number (from last scan)

        self.setTitle(f"  {name}  ")
        self.setStyleSheet(f"""
            MotorPanel {{
                border: 2px solid {color};
                border-radius: 6px;
                margin-top: 8px;
                font-weight: bold;
            }}
            MotorPanel::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: {color};
            }}
        """)
        self._build_ui()
        self._set_controls_enabled(False)

    def set_nickname(self, nickname):
        self.name = nickname or f"Motor {self.index + 1}"
        self.setTitle(f"  {self.name}  ")
        self.nick_edit.setText(self.name)

    def _on_nick_changed(self):
        text = self.nick_edit.text().strip()
        if text:
            self.name = text
            self.setTitle(f"  {self.name}  ")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 18, 10, 10)

        # ── Nickname row
        nick_lay = QHBoxLayout()
        nick_lay.addWidget(QLabel("Name:"))
        self.nick_edit = QLineEdit(self.name)
        self.nick_edit.setPlaceholderText("Motor name...")
        self.nick_edit.editingFinished.connect(self._on_nick_changed)
        nick_lay.addWidget(self.nick_edit)
        layout.addLayout(nick_lay)

        # ── Connection row
        conn = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        conn.addWidget(self.port_combo)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setFixedWidth(90)
        self.btn_connect.clicked.connect(self._toggle_connection)
        conn.addWidget(self.btn_connect)

        self.conn_ind = StatusIndicator()
        conn.addWidget(self.conn_ind)
        layout.addLayout(conn)

        # ── Status
        status_grid = QGridLayout()
        status_grid.setSpacing(6)

        bold = QFont()
        bold.setBold(True)

        self.lbl_pos = QLabel("— mm")
        self.lbl_pos.setFont(bold)
        self.lbl_pos.setStyleSheet("font-size:16px;")
        status_grid.addWidget(QLabel("Pos:"), 0, 0)
        status_grid.addWidget(self.lbl_pos, 0, 1)

        self.ind_homed = StatusIndicator()
        self.ind_homed.setToolTip("Home: green=homed, orange=homing in progress (15\u201360 s), gray=not homed")
        status_grid.addWidget(QLabel("Home:"), 0, 2)
        status_grid.addWidget(self.ind_homed, 0, 3)

        self.ind_moving = StatusIndicator()
        self.ind_moving.setToolTip("Motion: orange=moving, red=motion error")
        status_grid.addWidget(QLabel("Mov:"), 0, 4)
        status_grid.addWidget(self.ind_moving, 0, 5)

        self.ind_enabled = StatusIndicator()
        self.ind_enabled.setToolTip("Enabled: green=motor channel enabled (energized)")
        status_grid.addWidget(QLabel("En:"), 0, 6)
        status_grid.addWidget(self.ind_enabled, 0, 7)

        layout.addLayout(status_grid)

        # ── Zero / home ("set home" = make the current position 0.0000 mm)
        zero_lay = QHBoxLayout()
        self.btn_setzero = QPushButton("Set Home")
        self.btn_setzero.setToolTip("Define the current position as 0.0000 mm (home/reference)")
        self.btn_setzero.clicked.connect(self._set_zero)
        zero_lay.addWidget(self.btn_setzero)

        self.btn_clearzero = QPushButton("↺")
        self.btn_clearzero.setFixedWidth(32)
        self.btn_clearzero.setToolTip("Clear the zero offset (return to hardware coordinates)")
        self.btn_clearzero.clicked.connect(self._clear_zero)
        zero_lay.addWidget(self.btn_clearzero)

        self.lbl_zero = QLabel("")
        self.lbl_zero.setStyleSheet("color:#888;")
        zero_lay.addWidget(self.lbl_zero)
        zero_lay.addStretch()
        layout.addLayout(zero_lay)

        # ── Move controls
        move_lay = QHBoxLayout()
        move_lay.addWidget(QLabel("Target:"))
        self.spin_abs = QDoubleSpinBox()
        self.spin_abs.setRange(TRAVEL_MIN, TRAVEL_MAX)
        self.spin_abs.setDecimals(4)
        self.spin_abs.setSingleStep(0.1)
        self.spin_abs.setSuffix(" mm")
        self.spin_abs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.spin_abs.setMinimumWidth(95)
        self.spin_abs.setToolTip("Absolute target \u2014 travel range 0\u201350 mm (MTS50/M-Z8)")
        move_lay.addWidget(self.spin_abs, 1)

        self.btn_go = QPushButton("Go")
        self.btn_go.setFixedWidth(44)
        self.btn_go.clicked.connect(self._move_absolute)
        move_lay.addWidget(self.btn_go)

        move_lay.addWidget(QLabel("Rel:"))
        self.spin_rel = QDoubleSpinBox()
        self.spin_rel.setRange(-TRAVEL_MAX, TRAVEL_MAX)
        self.spin_rel.setDecimals(4)
        self.spin_rel.setSingleStep(0.1)
        self.spin_rel.setSuffix(" mm")
        self.spin_rel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.spin_rel.setMinimumWidth(95)
        self.spin_rel.setToolTip("Relative move \u2014 stops at the 0/50 mm limits if exceeded")
        move_lay.addWidget(self.spin_rel, 1)

        self.btn_rel = QPushButton("Go")
        self.btn_rel.setFixedWidth(44)
        self.btn_rel.clicked.connect(self._move_relative)
        move_lay.addWidget(self.btn_rel)
        layout.addLayout(move_lay)

        # ── Jog
        jog_lay = QHBoxLayout()
        self.btn_jog_rev = QPushButton("◀")
        self.btn_jog_rev.setFixedWidth(32)
        self.btn_jog_rev.clicked.connect(lambda: self._jog("reverse"))
        jog_lay.addWidget(self.btn_jog_rev)

        self.spin_jog = QDoubleSpinBox()
        self.spin_jog.setRange(MIN_STEP, TRAVEL_MAX)
        self.spin_jog.setDecimals(4)
        self.spin_jog.setValue(0.1)
        self.spin_jog.setSingleStep(0.01)
        self.spin_jog.setPrefix("Step: ")
        self.spin_jog.setSuffix(" mm")
        self.spin_jog.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.spin_jog.setMinimumWidth(140)
        self.spin_jog.setToolTip("Jog step \u2014 min 0.0008 mm (0.8 \u00b5m, min repeatable step).\n0.1 \u00b5m is achievable but not repeatable.")
        jog_lay.addWidget(self.spin_jog, 1)

        self.btn_jog_fwd = QPushButton("▶")
        self.btn_jog_fwd.setFixedWidth(32)
        self.btn_jog_fwd.clicked.connect(lambda: self._jog("forward"))
        jog_lay.addWidget(self.btn_jog_fwd)

        self.btn_home = QPushButton("Home")
        self.btn_home.setFixedWidth(54)
        self.btn_home.clicked.connect(self._home)
        jog_lay.addWidget(self.btn_home)

        self.btn_enable = QPushButton("Enable")
        self.btn_enable.setCheckable(True)
        self.btn_enable.setFixedWidth(56)
        self.btn_enable.setToolTip("Enable/disable the motor channel")
        self.btn_enable.clicked.connect(self._toggle_enable)
        jog_lay.addWidget(self.btn_enable)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setFixedWidth(44)
        self.btn_stop.setStyleSheet("background-color:#F44336;color:white;font-weight:bold;")
        self.btn_stop.clicked.connect(self._stop)
        jog_lay.addWidget(self.btn_stop)
        layout.addLayout(jog_lay)

        # ── Velocity
        vel_lay = QHBoxLayout()
        vel_lay.addWidget(QLabel("Vel:"))
        self.spin_vel = QDoubleSpinBox()
        self.spin_vel.setRange(0.01, VEL_MAX)
        self.spin_vel.setDecimals(3)
        self.spin_vel.setValue(2.0)
        self.spin_vel.setSuffix(" mm/s")
        self.spin_vel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.spin_vel.setMinimumWidth(108)
        self.spin_vel.setToolTip("Velocity \u2014 hardware max 2.4 mm/s (higher values are clamped)")
        vel_lay.addWidget(self.spin_vel, 1)

        vel_lay.addWidget(QLabel("Acc:"))
        self.spin_acc = QDoubleSpinBox()
        self.spin_acc.setRange(0.01, ACC_MAX)
        self.spin_acc.setDecimals(3)
        self.spin_acc.setValue(1.5)
        self.spin_acc.setSuffix(" mm/s²")
        self.spin_acc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.spin_acc.setMinimumWidth(120)
        self.spin_acc.setToolTip("Acceleration \u2014 hardware max 4.5 mm/s\u00b2 (higher values are clamped)")
        vel_lay.addWidget(self.spin_acc, 1)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setFixedWidth(62)
        self.btn_apply.clicked.connect(self._apply_velocity)
        vel_lay.addWidget(self.btn_apply)
        layout.addLayout(vel_lay)
        layout.addStretch(1)

    # ── Connection ───────────────────────────────────────────────────

    def populate_ports(self, devices):
        self._serial_by_port = {d["port"]: d.get("serial_number", "") for d in devices}
        current = self.port_combo.currentData()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for dev in devices:
            sn = dev.get("serial_number", "")
            label = f"SN: {sn}" if sn else dev["port"]
            self.port_combo.addItem(label, dev["port"])
        if current:
            idx = self.port_combo.findData(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        self.port_combo.blockSignals(False)

    def current_serial(self):
        """Serial number of the currently selected device ('' if none)."""
        return self._serial_by_port.get(self.port_combo.currentData(), "")

    def _toggle_connection(self):
        if self.stage:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_combo.currentData()
        if not port:
            return
        try:
            self.stage = MotorStage(serial_port=port, home=False)
            self.btn_connect.setText("Disconnect")
            self.conn_ind.set_active()
            self._set_controls_enabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Connection Error",
                                 f"{self.name} failed to connect:\n\n{e}\n\n"
                                 "The device may be in use by another program "
                                 "(close legacy Thorlabs APT/Kinesis software).")

    def disconnect(self):
        if self.stage:
            try:
                self.stage.close()
            except Exception:
                pass
            self.stage = None
        self.btn_connect.setText("Connect")
        self.conn_ind.set_inactive()
        self._set_controls_enabled(False)
        self.lbl_pos.setText("— mm")
        self.ind_homed.set_inactive()
        self.ind_moving.set_inactive()
        self.ind_enabled.set_inactive()
        self.lbl_zero.setText("")
        self.spin_abs.setRange(TRAVEL_MIN, TRAVEL_MAX)

    @property
    def is_connected(self):
        return self.stage is not None

    # ── Actions ──────────────────────────────────────────────────────

    def _warn(self, text):
        """Show a warning on the main-window status label (if reachable)."""
        win = self.window()
        if hasattr(win, "lbl_status"):
            win.lbl_status.setText(f"⚠ {self.name}: {text}")

    def _home(self):
        if self.stage:
            self.stage.home()
            self._warn("homing started \u2014 the stage travels to the limit switch "
                       "and back (15\u201360 s); the Home light shows orange meanwhile")

    def _move_absolute(self):
        if self.stage:
            target = self.spin_abs.value()
            lo, hi = self.stage.min_position_mm, self.stage.max_position_mm
            if target < lo or target > hi:
                self._warn(f"target {target:.4f} mm is outside the travel range "
                           f"[{lo:.4f}, {hi:.4f}] \u2014 clamped to the limit")
            self.stage.move_absolute(target)

    def _move_relative(self):
        if self.stage:
            delta = self.spin_rel.value()
            target = self.stage.position_mm + delta
            lo, hi = self.stage.min_position_mm, self.stage.max_position_mm
            if target < lo or target > hi:
                self._warn(f"relative move exceeds the limit (target {target:.4f} mm) "
                           f"\u2014 it will stop at {max(lo, min(hi, target)):.4f} mm")
            self.stage.move_relative(delta)

    def _jog(self, direction):
        if self.stage:
            step = self.spin_jog.value()
            vel = self.spin_vel.value()
            acc = self.spin_acc.value()
            sign = 1 if direction == "forward" else -1
            target = self.stage.position_mm + sign * step
            lo, hi = self.stage.min_position_mm, self.stage.max_position_mm
            if target < lo or target > hi:
                self._warn("jog step exceeds the travel limit \u2014 it will stop at the limit")
            self.stage.set_jog_params(step, vel, acc)
            self.stage.jog(direction)

    def _stop(self):
        if self.stage:
            self.stage.stop()

    def _toggle_enable(self):
        if self.stage:
            self.stage.set_enabled(self.btn_enable.isChecked())

    def _set_zero(self):
        """Make the current position 0.0000 mm (user-defined home/reference)."""
        if self.stage:
            self.stage.set_zero()
            off = self.stage.zero_offset_mm
            # Let absolute targets span the full physical travel in new coords.
            self.spin_abs.setRange(TRAVEL_MIN - off, TRAVEL_MAX - off)

    def _clear_zero(self):
        if self.stage:
            self.stage.reset_zero()
            self.spin_abs.setRange(TRAVEL_MIN, TRAVEL_MAX)

    def _apply_velocity(self):
        if self.stage:
            v, a = self.spin_vel.value(), self.spin_acc.value()
            self.stage.set_velocity(v, a)
            win = self.window()
            if hasattr(win, "lbl_status"):
                win.lbl_status.setText(
                    f"{self.name}: velocity {min(v, VEL_MAX):.3f} mm/s, "
                    f"acceleration {min(a, ACC_MAX):.3f} mm/s\u00b2 applied")

    # ── Status Update ────────────────────────────────────────────────

    def update_status(self):
        if not self.stage:
            return
        try:
            s = self.stage.status
            self.lbl_pos.setText(f"{self.stage.position_mm:.4f} mm")
            # Home light: orange while homing runs (can take 15-60 s — the stage
            # drives to the limit switch and back), green once homed.
            if s["homing"]:
                self.ind_homed.set_warning()
            elif s["homed"]:
                self.ind_homed.set_active()
            else:
                self.ind_homed.set_inactive()
            self.ind_moving.set_warning() if self.stage.is_moving else self.ind_moving.set_inactive()
            if s["motion_error"]:
                self.ind_moving.set_error()
            enabled = s["channel_enabled"]
            self.ind_enabled.set_active() if enabled else self.ind_enabled.set_inactive()
            # Keep the toggle in sync with the device without re-triggering it.
            self.btn_enable.blockSignals(True)
            self.btn_enable.setChecked(enabled)
            self.btn_enable.blockSignals(False)
            off = getattr(self.stage, "zero_offset_mm", 0.0)
            self.lbl_zero.setText(f"\u2302 offset: {off:+.4f} mm" if abs(off) > 1e-9 else "")
        except Exception:
            pass

    def _set_controls_enabled(self, enabled):
        for w in (self.spin_abs, self.spin_rel, self.spin_jog, self.spin_vel, self.spin_acc,
                  self.btn_go, self.btn_rel, self.btn_jog_rev, self.btn_jog_fwd,
                  self.btn_home, self.btn_enable, self.btn_stop, self.btn_apply,
                  self.btn_setzero, self.btn_clearzero):
            w.setEnabled(enabled)


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Thorlabs APT — 3x MTS50/M Stage Control")
        self.setMinimumSize(1180, 380)
        self.resize(1300, 430)

        self.panels = []
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_all)
        self._build_ui()
        self._scan(show_dialog=False)   # scan at startup + apply saved nicknames
        self._poll_timer.start(200)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Global toolbar
        toolbar = QHBoxLayout()

        self.btn_scan = QPushButton("Scan Devices")
        self.btn_scan.clicked.connect(self._scan)
        toolbar.addWidget(self.btn_scan)

        self.btn_connect_all = QPushButton("Connect All")
        self.btn_connect_all.clicked.connect(self._connect_all)
        toolbar.addWidget(self.btn_connect_all)

        self.btn_disconnect_all = QPushButton("Disconnect All")
        self.btn_disconnect_all.clicked.connect(self._disconnect_all)
        toolbar.addWidget(self.btn_disconnect_all)

        toolbar.addWidget(self._vsep())

        self.btn_home_all = QPushButton("Home All")
        self.btn_home_all.clicked.connect(self._home_all)
        toolbar.addWidget(self.btn_home_all)

        self.btn_stop_all = QPushButton("STOP ALL")
        self.btn_stop_all.setStyleSheet(
            "background-color:#F44336;color:white;font-weight:bold;padding:4px 12px;"
        )
        self.btn_stop_all.clicked.connect(self._stop_all)
        toolbar.addWidget(self.btn_stop_all)

        toolbar.addWidget(self._vsep())

        self.btn_guide = QPushButton("Guide")
        self.btn_guide.setToolTip("Hardware specs, light meanings, and usage tips")
        self.btn_guide.clicked.connect(self._show_guide)
        toolbar.addWidget(self.btn_guide)

        toolbar.addStretch()

        self.lbl_status = QLabel("Ready")
        toolbar.addWidget(self.lbl_status)
        root.addLayout(toolbar, 0)

        # ── 3 Motor panels side by side
        panels_lay = QHBoxLayout()
        panels_lay.setSpacing(8)
        for i in range(3):
            panel = MotorPanel(i, COLORS[i], NAMES[i])
            panel.nick_edit.editingFinished.connect(self._save_nicknames)
            # When the panel's device changes, show that serial's saved name.
            panel.port_combo.currentIndexChanged.connect(
                lambda _idx, p=panel: self._apply_nickname(p))
            self.panels.append(panel)
            panels_lay.addWidget(panel, 1)
        root.addLayout(panels_lay, 1)

    def _vsep(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setFixedHeight(26)
        return sep

    def _show_guide(self):
        from PyQt5.QtWidgets import QDialog, QTextBrowser, QVBoxLayout as VBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Guide — Hardware & Usage")
        dlg.resize(680, 640)
        browser = QTextBrowser(dlg)
        browser.setOpenExternalLinks(True)
        browser.setHtml("""
<h2>Hardware</h2>
<p><b>Controller:</b> Thorlabs TDC001 (T-Cube DC Servo) &nbsp;|&nbsp;
<b>Stage:</b> MTS50/M-Z8 (50&nbsp;mm)</p>

<h2>Supported ranges and accuracy</h2>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>Property</th><th>Value</th><th>Note</th></tr>
<tr><td>Travel range</td><td>0 – 50 mm</td><td>software-limited; out-of-range targets are clamped with a warning</td></tr>
<tr><td>Max velocity</td><td>2.4 mm/s</td><td>higher requests are clamped</td></tr>
<tr><td>Max acceleration</td><td>4.5 mm/s²</td><td>higher requests are clamped</td></tr>
<tr><td>Min repeatable step</td><td><b>0.8 µm</b> (0.0008 mm)</td><td>measured on this hardware: 0.76–0.82 µm ✓</td></tr>
<tr><td>Min achievable step</td><td>0.1 µm</td><td><i>not</i> repeatable (in our measurement one step did not move at all)</td></tr>
<tr><td>Encoder resolution</td><td>34,554.96 counts/mm</td><td>1 count ≈ 0.0289 µm (same scale as Kinesis)</td></tr>
<tr><td>Bidirectional repeatability</td><td>1.6 µm</td><td>datasheet</td></tr>
<tr><td>Backlash</td><td>&lt; 6 µm</td><td>the encoder sits on the motor, so backlash is <i>invisible</i> on screen —
for precision work always approach a target from the same direction</td></tr>
<tr><td>Home accuracy</td><td>± 4 µm</td><td>datasheet</td></tr>
</table>
<p><i>Accuracy note: the position readout comes from the motor encoder (closed loop; in our
measurements command = encoder, 0.00 µm error). For absolute physical accuracy the
datasheet values apply.</i></p>

<h2>Meaning of the lights</h2>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>Light</th><th>Color</th><th>Meaning</th></tr>
<tr><td rowspan="3"><b>Home</b></td><td>🟢 green</td><td>homed (absolute positions are reliable)</td></tr>
<tr><td>🟠 orange</td><td><b>homing in progress</b> — the stage drives to the limit switch and back, takes 15–60 s; wait for it to finish</td></tr>
<tr><td>⚪ gray</td><td>not homed since this power-up</td></tr>
<tr><td rowspan="2"><b>Mov</b></td><td>🟠 orange</td><td>moving</td></tr>
<tr><td>🔴 red</td><td>motion/position error reported</td></tr>
<tr><td><b>En</b></td><td>🟢 green</td><td>motor channel enabled (energized). Toggle with "Enable"</td></tr>
</table>

<h2>Tips</h2>
<ul>
<li><b>Home first</b> after power-up — until homed, absolute positions are relative
to wherever the stage happened to be at power-on.</li>
<li>The motion light may turn off ~1&nbsp;s before the servo finishes its final fine
approach; the move is complete once the position value stops changing.</li>
<li><b>Set Home</b> makes the current position 0.0000 (a software reference; the motor
does not move and the physical 0–50 mm limits are preserved). <b>↺</b> returns to
hardware coordinates.</li>
<li>Out-of-range targets and over-limit velocity/acceleration are clamped
automatically; a ⚠ warning appears in the status bar at the top.</li>
<li>Motor names are saved per serial number — a name follows its motor even if you
plug it into a different USB port.</li>
</ul>
""")
        lay = VBox(dlg)
        lay.addWidget(browser)
        dlg.exec_()

    # ── Global Actions ───────────────────────────────────────────────

    def _scan(self, show_dialog=True):
        devices = find_devices()
        for panel in self.panels:
            panel.populate_ports(devices)
        # Auto-assign a different device to each panel.
        for i, panel in enumerate(self.panels):
            if i < len(devices) and i < panel.port_combo.count():
                panel.port_combo.setCurrentIndex(i)
        # Restore each panel's saved name from its device's serial number.
        for panel in self.panels:
            self._apply_nickname(panel)

        if devices:
            self.lbl_status.setText(f"{len(devices)} device(s) found")
        else:
            # No device: explain *why* instead of a silent empty list.
            self.lbl_status.setText("0 devices found")
            if show_dialog:
                info = diagnose()
                QMessageBox.warning(self, "No devices found", info["message"])

    def _connect_all(self):
        for panel in self.panels:
            if not panel.is_connected and panel.port_combo.currentData():
                panel.connect()
        n = sum(1 for p in self.panels if p.is_connected)
        self.lbl_status.setText(f"{n} motor(s) connected")

    def _disconnect_all(self):
        for panel in self.panels:
            panel.disconnect()
        self.lbl_status.setText("All motors disconnected")

    def _home_all(self):
        for panel in self.panels:
            if panel.is_connected:
                panel.stage.home()
        self.lbl_status.setText("Homing all motors...")

    def _stop_all(self):
        for panel in self.panels:
            if panel.is_connected:
                panel.stage.stop()
        self.lbl_status.setText("All motors stopped")

    # ── Polling ──────────────────────────────────────────────────────

    def _poll_all(self):
        for panel in self.panels:
            panel.update_status()

    # ── Nickname persistence ────────────────────────────────────────

    def _apply_nickname(self, panel):
        """Name a panel from the saved name for its selected device's serial."""
        sn = panel.current_serial()
        if not sn:
            return
        names = _load_config().get("nicknames", {})
        if isinstance(names, dict) and names.get(sn):
            panel.set_nickname(names[sn])

    def _save_nicknames(self):
        """Persist names keyed by SERIAL NUMBER, so a name follows its physical
        motor regardless of which panel/USB port it lands on. Merges — motors
        that aren't connected right now keep their saved names. Only real custom
        names are stored; a name left at (or reverted to) the default is dropped
        so defaults never follow a serial around."""
        cfg = _load_config()
        names = cfg.get("nicknames", {})
        if not isinstance(names, dict):
            names = {}
        for panel in self.panels:
            sn = panel.current_serial()
            if not sn:
                continue
            default = f"Motor {panel.index + 1}"
            if panel.name and panel.name != default:
                names[sn] = panel.name
            else:
                names.pop(sn, None)
        cfg["nicknames"] = names
        _save_config(cfg)

    def closeEvent(self, event):
        self._save_nicknames()
        self._poll_timer.stop()
        for panel in self.panels:
            panel.disconnect()
        event.accept()
