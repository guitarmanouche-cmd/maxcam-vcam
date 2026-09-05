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

IMPORTANT: use Scripting > Run Script... (the file-picker) or
python.ExecuteFile with a path, NOT a script-editor tab's Evaluate/Ctrl+E —
an editor tab holds whatever text was in it when you opened it, which can
be stale if the file changed on disk since. Every run prints
"[MaxCamServer] script version <SCRIPT_VERSION>" — if that doesn't match
the version below, you're not running what you think you're running.

See docs/PROTOCOL.md for the wire format and the coordinate-system caveat —
the ARCore -> Max axis remap below is a starting point, not calibrated
against a real device yet.
"""

import builtins
import os
import socket
import sys
import threading
import time

import pymxs

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import protocol  # noqa: E402

rt = pymxs.runtime

SCRIPT_VERSION = "2026-09-05-rt-execute-fix"  # bump this string whenever you edit this file

# --- configuration -------------------------------------------------------

UDP_HOST = "0.0.0.0"
UDP_PORT = 40111                  # must match the port entered in the Android app
CAMERA_NAME = "PhoneCam"          # live-preview Free Camera (auto-created if missing) — NEVER keyed, see below
TAKE_CAMERA_NAME = "PhoneCam_Take"  # separate camera that recording bakes keys onto — NEVER live-set, see below
SCENE_SCALE = 100.0                # meters -> scene units; this scene's system units are centimeters
POLL_INTERVAL_MS = 33             # ~30 Hz main-thread apply rate — plenty for a live operator preview
SMOOTHING_ALPHA = 1.0             # 1.0 = no smoothing; lower = smoother but laggier


# --- coordinate remap: ARCore (right-handed, Y-up) -> Max (right-handed, Z-up) ---
#
# Verified empirically on-device 2026-09-04 by probing a fresh identity-
# transform Freecamera in a live Max session (create it, place marker boxes
# on each world axis, capture what the camera actually sees): an identity
# Max camera looks down world **-Z** with local Y as "up" and local X as
# "right" — i.e. structurally the *same* role layout as ARCore/OpenGL
# (right=local X, up=local Y, backward=local Z), not the "-Y forward"
# convention commonly quoted for Max cameras. So this only needs the
# world-basis remap (Y-up -> Z-up) applied identically to every row —
# no local-axis role swap.
#
# A row-swap was tried first (based on the "-Y forward" assumption) and
# produced an upside-down camera: swapping which source row feeds a
# destination row is an odd permutation (determinant -1, a mirror), which
# is a strictly worse failure mode than a sign error — don't reintroduce it
# without re-verifying the identity-camera direction the way this comment
# describes.

def _remap_vec(v):
    x, y, z = v
    return (x, -z, y)


def _normalize_quat(quat):
    qx, qy, qz, qw = quat
    mag = (qx * qx + qy * qy + qz * qz + qw * qw) ** 0.5
    if mag > 0:
        return (qx / mag, qy / mag, qz / mag, qw / mag)
    return (0.0, 0.0, 0.0, 1.0)


def _ar_pose_to_max_matrix(pos, quat, scale):
    # Conjugate (x,y,z negated, w kept): reported on-device 2026-09-04 that
    # pitch and yaw both came out inverted (tilt down -> Max tilts up, yaw
    # CW -> Max yaws CCW) with the axis roles otherwise correct — the
    # signature of MAXScript's quat->matrix rotation sense being opposite
    # ARCore's, not a per-axis sign or role bug. If this overcorrects
    # (everything now backwards the other way), revert to the plain
    # quaternion; if it's still off on one axis only, this isn't the right
    # explanation and _remap_vec/the row assignment need another look.
    #
    # Normalize defensively before building the matrix: a non-unit
    # quaternion isn't guaranteed to just come out as a uniform scale once
    # run through rotate() — depending on the internal formula it can shear
    # the result — and this is cheap insurance against that regardless of
    # how much it actually contributed to the 2026-09-05 squashing (float32
    # from ARCore is only ~1e-7 off unit length, nowhere near enough on its
    # own to explain the ~2-7x stretch that was observed).
    qx, qy, qz, qw = _normalize_quat(quat)
    q = rt.quat(-qx, -qy, -qz, qw)
    ar_rot = rt.rotate(rt.matrix3(1), q)

    def _row(p):
        return _remap_vec((p.x, p.y, p.z))

    right = _row(ar_rot.row1)     # ARCore local X (right)    -> Max local X (right)
    up = _row(ar_rot.row2)        # ARCore local Y (up)       -> Max local Y (up)
    backward = _row(ar_rot.row3)  # ARCore local Z (backward) -> Max local Z (backward)
    tx, ty, tz = _remap_vec(pos)

    return rt.matrix3(
        rt.point3(*right),
        rt.point3(*up),
        rt.point3(*backward),
        rt.point3(tx * scale, ty * scale, tz * scale),
    )


def _reset_scale(cam):
    """Force uniform scale back to 1. A camera should never need scale, but
    setting `.transform` from three basis rows lets tiny non-orthonormal
    drift (or, previously, a bad key elsewhere on the timeline interpolating
    across a huge range) show up as a stretched/squashed camera — reported
    on-device 2026-09-04. Cheap insurance regardless of root cause."""
    cam.scale = rt.point3(1, 1, 1)


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

        # -- recording state (see _update_recording) --
        self._recording = False
        self._record_start_wall = None
        self._record_start_frame = 0
        self._last_recorded_frame = None

    # -- lifecycle --

    def start(self):
        if self._running.is_set():
            print("[MaxCamServer] already running")
            return

        self._ensure_camera(self.camera_name)

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.settimeout(0.5)

        self._running.set()
        self._recv_thread = threading.Thread(target=self._recv_loop, name="MaxCamServerRecv", daemon=True)
        self._recv_thread.start()

        self._start_timer()
        print(f"[MaxCamServer] script version {SCRIPT_VERSION}")
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

    def _ensure_camera(self, name):
        cam = rt.getNodeByName(name)
        if cam is None:
            cam = rt.Freecamera()
            cam.name = name
            print(f"[MaxCamServer] created Free Camera '{name}'")

        # Default rotation controller is Euler XYZ, which Autodesk's own
        # docs say "does not allow rotations of greater than 180 degrees
        # between keys" — two keys with nearly identical real orientation
        # can land on very different Euler triples, so playback
        # flips/judders every frame (worst near straight up/down).
        # Reported on-device 2026-09-04 as the camera alternating
        # look-up/look-down each frame during playback. Autodesk's docs
        # recommend TCB specifically "for continuous rotation" — force it
        # once, up front.
        #
        # `cam.rotation.controller` (attribute-chaining) throws through
        # pymxs on a node — confirmed on-device 2026-09-05:
        # "'MXSWrapperBase' object has no attribute 'controller'" — even
        # though the exact same expression works fine typed directly into
        # the MAXScript listener, and getPropertyController/
        # setPropertyController (tried as a fix) turned out to be the wrong
        # functions entirely (returned UndefinedClass even from pure
        # MAXScript — they're for something else). This is a pymxs Python-
        # bridge gap, not a MAXScript one. Worse, the same attribute
        # pattern was used — and its exception silently swallowed — in the
        # key-clearing code below, which is why old takes were never
        # actually being deleted. Fix: do the whole thing as one MAXScript
        # string via rt.execute(), which isn't subject to pymxs's attribute
        # forwarding at all; look the node up by name again inside it
        # rather than trying to pass the pymxs object in.
        try:
            result = rt.execute(
                f'(local c = getNodeByName "{name}"; local cls = classOf c.rotation.controller as string; '
                f'if cls != "TCB_rotation" then (c.rotation.controller = TCB_rotation(); cls + " -> TCB_rotation") '
                f'else (cls + " (already TCB)"))'
            )
            print(f"[MaxCamServer] '{name}' rotation controller: {result}")
        except Exception as e:
            print(f"[MaxCamServer] could not set TCB_rotation controller on '{name}': {e}")

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
        new_transform = _ar_pose_to_max_matrix(pos, quat, SCENE_SCALE)
        # Undo recording on every transform set at 30-60 Hz is what made the
        # camera viewport crawl (reported ~2 fps 2026-09-04) — this is a
        # live preview, not something the operator needs to Ctrl+Z through.
        #
        # cam (CAMERA_NAME/PhoneCam) must NEVER receive keys — confirmed
        # live 2026-09-05: assigning a value to an already-keyed property
        # outside animate mode does NOT just preview the current time, it
        # SHIFTS EVERY EXISTING KEY by the delta needed to match the new
        # value (tested on a plain Point: two keys 0->[0,0,0], 10->[100,0,0],
        # parked at frame 5 (interpolated [50,0,0]), set .pos=[999,0,0] with
        # animate off -> key@0 became [949,0,0], key@10 became [1049,0,0] —
        # both keys shifted by the same +949 delta). That's what was
        # actually squashing/juddering the camera: once it had ANY keys
        # (from a take), every live tick below re-shifted the WHOLE curve,
        # scale included, since assigning .transform touches Position/
        # Rotation/Scale together. The recorded take now lives on a
        # completely separate object (TAKE_CAMERA_NAME/PhoneCam_Take,
        # below) that this live path never touches, so it can't happen here.
        with pymxs.undo(False):
            cam.transform = new_transform
            _reset_scale(cam)

        self._update_recording(packet.is_recording, new_transform)

    def _update_recording(self, is_recording, transform):
        """Bakes real keyframes onto TAKE_CAMERA_NAME (a camera separate
        from the live-preview one — see the comment in _apply_latest for
        why they can't be the same object) while the phone's Record button
        is held. Tracks wall-clock time since Record was pressed, converts
        it to a frame number via the scene's frame rate, and sets a key
        there each time it reaches a new frame — so stopping and scrubbing
        the timeline plays back the take at the speed it was performed.
        """
        if not is_recording:
            if self._recording:
                print(f"[MaxCamServer] recording stopped at frame {self._last_recorded_frame}")
            self._recording = False
            return

        if not self._recording:
            self._recording = True
            self._record_start_wall = time.time()
            self._record_start_frame = int(rt.currentTime) // int(rt.ticksperframe)
            self._last_recorded_frame = None
            take_cam = self._ensure_camera(TAKE_CAMERA_NAME)  # create + fix its rotation controller once, up front

            # Wipe any keys from a previous take before laying down new
            # ones. Without this, starting a second take (e.g. after
            # rewinding to frame 0) leaves the old take's keys sitting in
            # the same range as the new one, and the two interleave/fight
            # instead of the new take cleanly replacing the old — reported
            # on-device 2026-09-05. If a take is worth keeping, copy the
            # camera object out before recording over it again.
            # Same pymxs attribute-chaining gap as in _ensure_camera —
            # `take_cam.position.controller` throws through pymxs, and this
            # exact spot silently ate that exception before (`except:
            # pass`, no print), which is the actual reason old takes were
            # never being cleared. Do it as one MAXScript string instead.
            with pymxs.undo(False):
                try:
                    cleared = rt.execute(
                        f'(local c = getNodeByName "{TAKE_CAMERA_NAME}"; local n = 0; '
                        f'if (numKeys c.position.controller) > 0 then (deleteKeys c.position.controller #allKeys; n += 1); '
                        f'if (numKeys c.rotation.controller) > 0 then (deleteKeys c.rotation.controller #allKeys; n += 1); '
                        f'if (numKeys c.scale.controller) > 0 then (deleteKeys c.scale.controller #allKeys; n += 1); '
                        f'n)'
                    )
                    if cleared:
                        print(f"[MaxCamServer] cleared previous take's keys on '{TAKE_CAMERA_NAME}' ({cleared} controllers)")
                except Exception as e:
                    print(f"[MaxCamServer] could not clear previous take's keys on '{TAKE_CAMERA_NAME}': {e}")

            print(f"[MaxCamServer] recording started at frame {self._record_start_frame}")

        take_cam = rt.getNodeByName(TAKE_CAMERA_NAME)
        if take_cam is None:
            return  # take camera deleted mid-recording; keep listening in case it reappears

        elapsed = time.time() - self._record_start_wall
        frame = self._record_start_frame + round(elapsed * rt.frameRate)
        if frame == self._last_recorded_frame:
            return  # scene frame rate < packet rate; nothing new to key yet
        self._last_recorded_frame = frame

        # Construct an explicit frame-suffixed time value ("42f") via
        # rt.execute rather than passing a bare number or a hand-multiplied
        # tick count — bare numbers passed to interval()/attime() are
        # ambiguous (confirmed live: `interval 0 100` produced a 100-FRAME
        # range, i.e. 16000 ticks, so bare ints mean frames there), and
        # frame*ticksperframe passed to attime() turned out to want the
        # same frame-based value, not ticks — that mismatch was what made
        # the scene's actual current time lurch ~160x too far forward every
        # tick while Record was held, which is what caused the real-time
        # squashing/juddering (reported on-device 2026-09-04/09-05). The "f"
        # suffix sidesteps needing to know which interpretation applies.
        frame_time = rt.execute(f"{frame}f")

        end_frame = int(rt.animationRange.end) // int(rt.ticksperframe)
        if frame > end_frame:
            rt.animationRange = rt.interval(rt.animationRange.start, frame_time)

        with pymxs.undo(False):
            with pymxs.attime(frame_time):
                with pymxs.animate(True):
                    take_cam.transform = transform
                    _reset_scale(take_cam)

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
        # at lower alpha. LERP between two unit quaternions shortens the
        # result (it's a chord, not an arc), and since self._smoothed_quat
        # feeds back in as next tick's starting point, an un-renormalized
        # result would shrink further every tick alpha<1 is used — so
        # renormalize what gets stored, not just what _ar_pose_to_max_matrix
        # consumes downstream.
        new_quat = _normalize_quat(tuple(sq[i] + a * (quat[i] - sq[i]) for i in range(4)))
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
