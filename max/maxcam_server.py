"""
UDP listener that drives a 3ds Max Free Camera from the MaxCam VCam Android
app. Requires 3ds Max 2022+ (built-in Python 3 / pymxs).

Run inside 3ds Max:
    Scripting > Run Script...  ->  max/maxcam_server.py
or from the MAXScript listener:
    python.ExecuteFile @"...\\max\\maxcam_server.py"

Re-running the script stops any server it previously started (tracked via
the `builtins` module, which survives re-execution within the same Max
session), so it's safe to just hit Run Script again after editing.

See docs/PROTOCOL.md for the wire format and the coordinate-system caveat —
the ARCore -> Max axis remap below is a starting point, not calibrated
against a real device yet.
"""

import builtins
import os
import socket
import sys
import threading

import pymxs

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import protocol  # noqa: E402

rt = pymxs.runtime

# --- configuration -------------------------------------------------------

UDP_HOST = "0.0.0.0"
UDP_PORT = 40111                  # must match the port entered in the Android app
CAMERA_NAME = "PhoneCam"          # Free Camera object the server drives (auto-created if missing)
SCENE_SCALE = 1.0                 # multiply incoming meters by this to match the scene's system units
POLL_INTERVAL_MS = 16             # ~60 Hz main-thread apply rate
SMOOTHING_ALPHA = 1.0             # 1.0 = no smoothing; lower = smoother but laggier


# --- coordinate remap: ARCore (right-handed, Y-up) -> Max (right-handed, Z-up) ---

def _remap_vec(v):
    """TODO calibrate against the real device — verify forward/up match before trusting this."""
    x, y, z = v
    return (x, -z, y)


def _ar_pose_to_max_matrix(pos, quat, scale):
    q = rt.quat(quat[0], quat[1], quat[2], quat[3])
    ar_rot = rt.rotate(rt.matrix3(1), q)

    def _row(p):
        return _remap_vec((p.x, p.y, p.z))

    r1 = _row(ar_rot.row1)
    r2 = _row(ar_rot.row2)
    r3 = _row(ar_rot.row3)
    tx, ty, tz = _remap_vec(pos)

    return rt.matrix3(
        rt.point3(*r1),
        rt.point3(*r2),
        rt.point3(*r3),
        rt.point3(tx * scale, ty * scale, tz * scale),
    )


# --- server ----------------------------------------------------------------

class MaxCamServer:
    def __init__(self, host=UDP_HOST, port=UDP_PORT, camera_name=CAMERA_NAME):
        self.host = host
        self.port = port
        self.camera_name = camera_name

        self._sock = None
        self._recv_thread = None
        self._running = threading.Event()

        self._lock = threading.Lock()
        self._latest = None  # protocol.CameraPacket
        self._latest_seq_applied = None

        self._smoothed_pos = None
        self._smoothed_quat = None

        self._timer = None

    # -- lifecycle --

    def start(self):
        if self._running.is_set():
            print("[MaxCamServer] already running")
            return

        self._ensure_camera()

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.settimeout(0.5)

        self._running.set()
        self._recv_thread = threading.Thread(target=self._recv_loop, name="MaxCamServerRecv", daemon=True)
        self._recv_thread.start()

        self._start_timer()
        print(f"[MaxCamServer] listening on {self.host}:{self.port}, driving camera '{self.camera_name}'")

    def stop(self):
        self._running.clear()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._recv_thread:
            self._recv_thread.join(timeout=1.0)
            self._recv_thread = None
        self._stop_timer()
        print("[MaxCamServer] stopped")

    def _ensure_camera(self):
        cam = rt.getNodeByName(self.camera_name)
        if cam is None:
            cam = rt.Freecamera()
            cam.name = self.camera_name
            print(f"[MaxCamServer] created Free Camera '{self.camera_name}'")
        return cam

    # -- network thread: only touches the socket and the shared queue slot --

    def _recv_loop(self):
        while self._running.is_set():
            try:
                data, _addr = self._sock.recvfrom(protocol.PACKET_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                packet = protocol.decode(data)
            except ValueError as e:
                print(f"[MaxCamServer] bad packet: {e}")
                continue
            with self._lock:
                self._latest = packet

    # -- Qt timer on the main thread: the only place it's safe to touch the Max scene --

    def _start_timer(self):
        try:
            from PySide6 import QtCore
        except ImportError:
            from PySide2 import QtCore

        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._apply_latest)
        self._timer.start(POLL_INTERVAL_MS)

    def _stop_timer(self):
        if self._timer:
            self._timer.stop()
            self._timer = None

    def _apply_latest(self):
        with self._lock:
            packet = self._latest
        if packet is None or packet.seq == self._latest_seq_applied:
            return
        self._latest_seq_applied = packet.seq

        cam = rt.getNodeByName(self.camera_name)
        if cam is None:
            return  # camera deleted/renamed; keep listening in case it reappears

        pos, quat = self._smooth(packet.pos, packet.quat)
        cam.transform = _ar_pose_to_max_matrix(pos, quat, SCENE_SCALE)

    def _smooth(self, pos, quat):
        a = SMOOTHING_ALPHA
        if a >= 1.0 or self._smoothed_pos is None:
            self._smoothed_pos = pos
            self._smoothed_quat = quat
            return pos, quat

        sp, sq = self._smoothed_pos, self._smoothed_quat
        new_pos = tuple(sp[i] + a * (pos[i] - sp[i]) for i in range(3))
        # Linear (not spherical) blend on the quaternion — fine close to
        # alpha=1 / at high poll rates; switch to slerp if jitter shows up
        # at lower alpha.
        new_quat = tuple(sq[i] + a * (quat[i] - sq[i]) for i in range(4))
        self._smoothed_pos, self._smoothed_quat = new_pos, new_quat
        return new_pos, new_quat


# --- entry point: safe to re-run from Run Script / the listener ------------

def run(host=UDP_HOST, port=UDP_PORT, camera_name=CAMERA_NAME):
    existing = getattr(builtins, "_maxcam_server", None)
    if existing is not None:
        existing.stop()

    server = MaxCamServer(host=host, port=port, camera_name=camera_name)
    server.start()
    builtins._maxcam_server = server  # persists across re-running this file in the same Max session
    return server


def stop():
    existing = getattr(builtins, "_maxcam_server", None)
    if existing is not None:
        existing.stop()
        builtins._maxcam_server = None


run()
