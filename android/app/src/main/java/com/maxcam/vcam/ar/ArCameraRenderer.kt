package com.maxcam.vcam.ar

import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.util.Log
import com.google.ar.core.Session
import com.google.ar.core.TrackingState
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

/**
 * Minimal ARCore render loop: no scene drawing, just enough GL to keep the
 * camera texture flowing so [Session.update] advances and produces device
 * poses. A textured camera-background quad can be added later if a live
 * preview is wanted; the skeleton only needs the pose stream.
 */
class ArCameraRenderer(
    private val session: Session,
    private val getDisplayRotation: () -> Int,
    private val listener: PoseListener,
) : GLSurfaceView.Renderer {

    interface PoseListener {
        fun onTracking(
            timestampSeconds: Double,
            posX: Float, posY: Float, posZ: Float,
            quatX: Float, quatY: Float, quatZ: Float, quatW: Float,
        )
        fun onNotTracking()
    }

    private var cameraTextureId = -1

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        val textures = IntArray(1)
        GLES20.glGenTextures(1, textures, 0)
        cameraTextureId = textures[0]
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, cameraTextureId)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        session.setCameraTextureName(cameraTextureId)
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        GLES20.glViewport(0, 0, width, height)
        session.setDisplayGeometry(getDisplayRotation(), width, height)
    }

    override fun onDrawFrame(gl: GL10?) {
        GLES20.glClearColor(0f, 0f, 0f, 1f)
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)
        if (cameraTextureId == -1) return

        try {
            val frame = session.update()
            val camera = frame.camera
            if (camera.trackingState == TrackingState.TRACKING) {
                val t = FloatArray(3)
                val q = FloatArray(4)
                camera.pose.getTranslation(t, 0)
                camera.pose.getRotationQuaternion(q, 0)
                listener.onTracking(
                    timestampSeconds = frame.timestamp / 1_000_000_000.0,
                    posX = t[0], posY = t[1], posZ = t[2],
                    quatX = q[0], quatY = q[1], quatZ = q[2], quatW = q[3],
                )
            } else {
                logThrottled("not tracking: ${camera.trackingFailureReason}")
                listener.onNotTracking()
            }
        } catch (e: Exception) {
            logThrottled("session.update() failed: $e")
        }
    }

    private var lastLogMs = 0L

    /** ~once/sec — this runs every frame, don't flood logcat. */
    private fun logThrottled(message: String) {
        val now = System.currentTimeMillis()
        if (now - lastLogMs < 1000) return
        lastLogMs = now
        Log.d(TAG, message)
    }

    companion object {
        private const val TAG = "ArCameraRenderer"
    }
}
