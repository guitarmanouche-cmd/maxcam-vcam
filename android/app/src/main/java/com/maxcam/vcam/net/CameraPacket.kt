package com.maxcam.vcam.net

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Wire format for one camera-pose sample sent over UDP to the 3ds Max server.
 * Little-endian, fixed 44 bytes. Field order and sizes must stay in sync with
 * the `struct` format string in max/maxcam_server.py ("<2sBBId3f4f") — see
 * docs/PROTOCOL.md for the authoritative spec.
 */
data class CameraPacket(
    val flags: Int,
    val seq: Int,
    val timestampSeconds: Double,
    val posX: Float, val posY: Float, val posZ: Float,
    val quatX: Float, val quatY: Float, val quatZ: Float, val quatW: Float,
) {
    fun toBytes(): ByteArray {
        val buf = ByteBuffer.allocate(SIZE_BYTES).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(MAGIC[0]); buf.put(MAGIC[1])
        buf.put(VERSION)
        buf.put(flags.toByte())
        buf.putInt(seq)
        buf.putDouble(timestampSeconds)
        buf.putFloat(posX); buf.putFloat(posY); buf.putFloat(posZ)
        buf.putFloat(quatX); buf.putFloat(quatY); buf.putFloat(quatZ); buf.putFloat(quatW)
        return buf.array()
    }

    companion object {
        const val SIZE_BYTES = 2 + 1 + 1 + 4 + 8 + 12 + 16 // 44
        val MAGIC = byteArrayOf('M'.code.toByte(), 'C'.code.toByte())
        const val VERSION: Byte = 1

        const val FLAG_RECORDING = 1 shl 0
        const val FLAG_RECENTER_EVENT = 1 shl 1
    }
}
