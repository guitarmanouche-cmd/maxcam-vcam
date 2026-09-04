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
`ar_pose_to_max_matrix()`) remaps axes as a starting point:

```
max_x =  ar_x
max_y = -ar_z
max_z =  ar_y
```

with the matching quaternion axis swap. **This has not been calibrated
against the real device yet** — verify by walking/rotating the phone in a
known direction and confirming the Max viewport camera moves the way you'd
expect (forward on the phone = camera dollies forward in Max, etc.), then
adjust signs as needed. This is the one part of the pipeline that can't be
gotten right by reading the docs alone — it has to be checked against the
tracked phone in hand.

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
(`SCENE_SCALE` in `maxcam_server.py`, currently `1.0`).
