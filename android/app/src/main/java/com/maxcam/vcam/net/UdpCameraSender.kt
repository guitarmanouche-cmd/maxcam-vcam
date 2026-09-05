package com.maxcam.vcam.net

import android.util.Log
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Fire-and-forget UDP sender. Runs its own thread so callers (the GL render
 * thread) never block on socket I/O; if the queue backs up we drop the
 * oldest sample rather than fall behind, since a stale pose is worse than a
 * missing one for a live camera feed.
 */
class UdpCameraSender {

    private val queue = LinkedBlockingQueue<ByteArray>(QUEUE_CAPACITY)
    private val running = AtomicBoolean(false)
    private var socket: DatagramSocket? = null
    private var address: InetAddress? = null
    private var port: Int = 0
    private var thread: Thread? = null
    private var seq = 0
    private val sentCount = AtomicInteger(0)
    private val droppedCount = AtomicInteger(0)
    private var lastStatsLogMs = 0L

    val isConnected: Boolean get() = running.get()

    @Throws(IOException::class)
    fun connect(host: String, port: Int) {
        disconnect()
        Log.i(TAG, "connecting to $host:$port")
        address = InetAddress.getByName(host)
        this.port = port
        socket = DatagramSocket()
        running.set(true)
        seq = 0
        sentCount.set(0)
        droppedCount.set(0)
        thread = Thread({ runLoop() }, "UdpCameraSender").apply {
            isDaemon = true
            start()
        }
        Log.i(TAG, "connected, socket bound to local port ${socket?.localPort}")
    }

    fun disconnect() {
        if (running.get()) Log.i(TAG, "disconnecting (sent=${sentCount.get()}, dropped=${droppedCount.get()})")
        running.set(false)
        thread?.interrupt()
        thread = null
        socket?.close()
        socket = null
        queue.clear()
    }

    /** Call from the GL render thread once per frame. Non-blocking. */
    fun sendPose(
        flags: Int,
        timestampSeconds: Double,
        posX: Float, posY: Float, posZ: Float,
        quatX: Float, quatY: Float, quatZ: Float, quatW: Float,
    ) {
        if (!running.get()) return
        val packet = CameraPacket(
            flags = flags,
            seq = seq++,
            timestampSeconds = timestampSeconds,
            posX = posX, posY = posY, posZ = posZ,
            quatX = quatX, quatY = quatY, quatZ = quatZ, quatW = quatW,
        )
        if (!queue.offer(packet.toBytes())) {
            queue.poll()
            queue.offer(packet.toBytes())
        }
    }

    private fun runLoop() {
        val sock = socket ?: return
        val addr = address ?: return
        while (running.get()) {
            val bytes = try {
                queue.take()
            } catch (e: InterruptedException) {
                break
            }
            try {
                sock.send(DatagramPacket(bytes, bytes.size, addr, port))
                sentCount.incrementAndGet()
                logStatsThrottled()
            } catch (e: IOException) {
                droppedCount.incrementAndGet()
                if (running.get()) Log.w(TAG, "send failed: ${e.message}")
            }
        }
    }

    /** Once/sec — confirms whether poses are actually flowing, not just whether connect() succeeded. */
    private fun logStatsThrottled() {
        val now = System.currentTimeMillis()
        if (now - lastStatsLogMs < 1000) return
        lastStatsLogMs = now
        Log.d(TAG, "sent=${sentCount.get()} dropped=${droppedCount.get()} -> $address:$port")
    }

    companion object {
        private const val TAG = "UdpCameraSender"
        private const val QUEUE_CAPACITY = 4
    }
}
