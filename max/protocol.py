"""
Wire format shared with the Android sender. Keep in sync with
android/app/src/main/java/com/maxcam/vcam/net/CameraPacket.kt and
docs/PROTOCOL.md — there's no version negotiation, just a magic+version
tripwire.
"""

import struct
from dataclasses import dataclass

MAGIC = b"MC"
VERSION = 1

FLAG_RECORDING = 1 << 0
FLAG_RECENTER_EVENT = 1 << 1

# magic(2s) version(B) flags(B) seq(I) timestamp(d) pos(3f) quat(4f)
_STRUCT = struct.Struct("<2sBBId3f4f")
PACKET_SIZE = _STRUCT.size  # 44


@dataclass
class CameraPacket:
    flags: int
    seq: int
    timestamp: float
    pos: tuple  # (x, y, z) meters, ARCore space
    quat: tuple  # (x, y, z, w)

    @property
    def is_recording(self) -> bool:
        return bool(self.flags & FLAG_RECORDING)


def decode(data: bytes) -> CameraPacket:
    """Raises ValueError if the packet is malformed or from an unsupported version."""
    if len(data) != PACKET_SIZE:
        raise ValueError(f"expected {PACKET_SIZE} bytes, got {len(data)}")

    magic, version, flags, seq, timestamp, px, py, pz, qx, qy, qz, qw = _STRUCT.unpack(data)
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic!r}")
    if version != VERSION:
        raise ValueError(f"unsupported version {version}")

    return CameraPacket(
        flags=flags,
        seq=seq,
        timestamp=timestamp,
        pos=(px, py, pz),
        quat=(qx, qy, qz, qw),
    )
