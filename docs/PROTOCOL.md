# Wire protocol

Phone (Android/Kotlin) → 3ds Max (Python), one UDP datagram per tracked
frame, fire-and-forget. No handshake, no delivery guarantee — a lost or
out-of-order packet is fine, it's just a stale camera sample.

## Packet layout (44 bytes, little-endian)

| Field       | Type       | Bytes | Notes                                        |
|-------------|------------|-------|-----------------------------------------------|
| magic       | `2s`       | 2     | ASCII `"MC"`                                  |
| version     | `uint8`    | 1     | `1`                                           |
| flags       | `uint8`    | 1     | bit0 = recording, bit1 = recenter event (reserved, unused by the server today) |
| seq         | `uint32`   | 4     | wraps, monotonically increasing per sender    |
| timestamp   | `float64`  | 8     | seconds, ARCore `Frame.timestamp` (nanoseconds) / 1e9 |
| pos.x/y/z   | `float32`×3| 12    | meters, see coordinate systems below          |
| quat.x/y/z/w| `float32`×4| 16    | unit quaternion, see coordinate systems below |

Python `struct` format string: `"<2sBBId3f4f"`.

Kotlin side: [`CameraPacket.kt`](../android/app/src/main/java/com/maxcam/vcam/net/CameraPacket.kt).
Python side: [`max/protocol.py`](../max/protocol.py).
Keep both in sync if the format ever changes — there is no version
negotiation, `version` is just a tripwire so the server can refuse to decode
a payload it doesn't understand.

## Coordinate systems

- **ARCore** (what the phone sends): right-handed, **Y-up**. Camera looks
  down its local **-Z**. Units: meters.
- **3ds Max** (what the server writes into the scene): right-handed,
  **Z-up**. Free Camera looks down its local **-Y** (Max convention: camera
  "forward" is world -Y before any rotation).

The server ([`maxcam_server.py`](../max/maxcam_server.py),
`_ar_pose_to_max_matrix()`) applies one remap, to world-space coordinates
only: `(x, y, z) -> (x, -z, y)`, identically to positions and to each of
the three rotation basis vectors.

This was calibrated against the real device on 2026-09-04, in two rounds:

1. First cut used a straight row-for-row copy of the rotation basis
   vectors plus the world remap. Reported as inverted pitch *and* yaw
   (raising the phone rotated the Max camera down; left/right also
   flipped), and the camera appeared to only rotate, not translate.
2. That was misdiagnosed as a camera-local-axis-role mismatch (assuming
   Max cameras look down local -Y vs ARCore's -Z) and "fixed" by swapping
   which source row feeds Max's row2 vs row3. That made it *worse* — the
   camera rendered upside down. Swapping two rows of an orthonormal basis
   is an odd permutation (determinant -1, a mirror), which is provably
   wrong regardless of any convention question.
3. Verified directly instead of assuming: created a fresh identity-
   transform Freecamera in the live Max session, placed marker boxes on
   each world axis, and captured what the camera actually saw
   (`capture_viewport` via the 3ds Max MCP bridge). Result: an identity
   Max camera looks down world **-Z**, with local Y as "up" and local X as
   "right" — the *same* local-axis role layout as ARCore/OpenGL (right =
   local X, up = local Y, backward = local Z), not the "-Y forward"
   convention commonly quoted for Max cameras. So no role swap is needed
   at all — the straight row-for-row copy from step 1 was structurally
   correct, and the real remaining bug was elsewhere:
   - **`SCENE_SCALE`**: this scene's system units are centimeters, ARCore
     reports meters, and `SCENE_SCALE` was `1.0` — a 20cm hand movement
     became a 2mm camera move, i.e. invisible. Now `100.0`.
   - The apparent pitch/yaw inversion in step 1 has not been re-tested in
     isolation since fixing the scale — retest after both fixes before
     assuming there's still a rotation bug.

If rotation still looks wrong after this, don't guess again — repeat the
identity-camera probe (step 3) rather than reasoning from a remembered
convention; that's what actually resolved it here.

## Recenter

The phone owns the recenter transform: on "Recenter" it captures the
current ARCore pose as a new local reference and sends all subsequent
samples relative to it (`MainActivity.relativeToReference`). The Max server
therefore never sees ARCore's raw session-origin coordinates — position
(0,0,0) / identity rotation in a packet means "wherever the operator was
standing when they last pressed Recenter." The `flags` recenter bit is
reserved for a future "snap the Max camera to a specific scene position on
recenter" feature; the server ignores it for now.

## Scene-unit scale

Positions are meters. If the Max scene's system unit isn't meters, multiply
`pos.x/y/z` by a scale factor in the server before writing the transform
(`SCENE_SCALE` in `maxcam_server.py`, currently `100.0` — the test scene's
system units are centimeters).

## Writing the pose into the scene

Not a wire-format concern, but the other half of "why does the camera look
wrong", and the part that actually caused the squashing/shaking — confirmed
with an isolated test on 2026-09-05, not just suspected: keyed a plain
Point at frame 0 (`[0,0,0]`) and frame 10 (`[100,0,0]`), parked the time
slider at frame 5 (correctly interpolated to `[50,0,0]`), then set
`.pos = [999,0,0]` with animate mode **off**. Result: frame 0's key became
`[949,0,0]` and frame 10's became `[1049,0,0]` — both shifted by the same
+949 delta. Assigning to an already-keyed property outside animate mode in
Max isn't "preview this value at the current time", it's "shift every
existing key by the delta needed to show this value now".

`maxcam_server.py`'s live-preview path sets `.transform` on `PhoneCam`
unconditionally, every packet, with animate off — harmless while `PhoneCam`
has no keys, but the moment it picks up any (which used to happen because
recording baked keys onto the same object the live path was also writing
to), every subsequent live tick would silently re-shift the *entire*
curve — position, rotation, and scale together, since they're all part of
`.transform`. That's what was squashing and juddering the camera, not the
coordinate remap or the quaternion. Fix: recording now bakes onto a
completely separate object, `PhoneCam_Take` (see
[`../max/README.md`](../max/README.md)), that the live path never touches,
so there's no shared curve left to shift.

The remap itself was cleared of suspicion by measurement on 2026-09-05:
fed a unit quaternion, `_ar_pose_to_max_matrix()` returns a matrix with row
lengths 1/1/1 and determinant 1. It's still normalized defensively before
use (see the function itself) as cheap insurance, not because this was
ever demonstrated to be the cause.
