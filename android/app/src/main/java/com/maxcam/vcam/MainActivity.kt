package com.maxcam.vcam

import android.Manifest
import android.content.pm.PackageManager
import android.opengl.GLSurfaceView
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Session
import com.google.ar.core.exceptions.UnavailableApkTooOldException
import com.google.ar.core.exceptions.UnavailableArcoreNotInstalledException
import com.google.ar.core.exceptions.UnavailableDeviceNotCompatibleException
import com.google.ar.core.exceptions.UnavailableSdkTooOldException
import com.google.ar.core.exceptions.UnavailableUserDeclinedInstallationException
import com.maxcam.vcam.ar.ArCameraRenderer
import com.maxcam.vcam.ar.Quat
import com.maxcam.vcam.databinding.ActivityMainBinding
import com.maxcam.vcam.net.CameraPacket
import com.maxcam.vcam.net.UdpCameraSender
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : ComponentActivity(), ArCameraRenderer.PoseListener {

    private lateinit var binding: ActivityMainBinding
    private lateinit var glSurfaceView: GLSurfaceView
    private val sender = UdpCameraSender()
    private val uiHandler = Handler(Looper.getMainLooper())

    private var session: Session? = null
    private var installRequested = false

    // Recenter state, touched from both the UI thread (button press sets the
    // request) and the GL thread (onTracking consumes it and computes the
    // new reference pose). recenterRequested is the only field written from
    // the UI thread; refPos/refQuatInv are only ever touched on the GL thread.
    private val recenterRequested = AtomicBoolean(false)
    private var refPos: FloatArray? = null
    private var refQuatInv: FloatArray? = null
    private var isRecording = false
    private var lastUiUpdateMs = 0L

    private val requestCameraPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) {
                Toast.makeText(this, "Camera permission is required for AR tracking", Toast.LENGTH_LONG).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        glSurfaceView = binding.glSurfaceView

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestCameraPermission.launch(Manifest.permission.CAMERA)
        }

        binding.connectButton.setOnClickListener { onConnectClicked() }
        binding.recenterButton.setOnClickListener { recenterRequested.set(true) }
        binding.recordToggle.setOnCheckedChangeListener { _, checked -> isRecording = checked }

        loadSavedConnection()
    }

    /** IP/port typed once, then remembered — otherwise every phone sleep (screen timeout kills
     * the UDP socket, see UdpCameraSender) meant retyping the PC's IP by hand. */
    private fun loadSavedConnection() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        prefs.getString(PREF_KEY_IP, null)?.let { binding.ipInput.setText(it) }
        val savedPort = prefs.getInt(PREF_KEY_PORT, -1)
        if (savedPort > 0) {
            binding.portInput.setText(savedPort.toString())
        }
    }

    private fun saveConnection(host: String, port: Int) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
            .putString(PREF_KEY_IP, host)
            .putInt(PREF_KEY_PORT, port)
            .apply()
    }

    private fun onConnectClicked() {
        Log.d(TAG, "connect button clicked, currently connected=${sender.isConnected}")
        if (sender.isConnected) {
            sender.disconnect()
            binding.connectButton.setText(R.string.btn_connect)
            Toast.makeText(this, "Disconnected", Toast.LENGTH_SHORT).show()
            return
        }
        val host = binding.ipInput.text.toString().trim()
        val port = binding.portInput.text.toString().trim().toIntOrNull()
        if (host.isEmpty() || port == null) {
            Log.w(TAG, "connect aborted: bad input host='$host' port='${binding.portInput.text}'")
            Toast.makeText(this, "Enter a valid IP and port", Toast.LENGTH_SHORT).show()
            return
        }
        try {
            sender.connect(host, port)
            saveConnection(host, port)
            binding.connectButton.setText(R.string.btn_disconnect)
            Toast.makeText(this, "Connected to $host:$port", Toast.LENGTH_SHORT).show()
        } catch (e: IOException) {
            Log.e(TAG, "connect failed", e)
            Toast.makeText(this, "Connect failed: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    override fun onResume() {
        super.onResume()
        if (!ensureSession()) return

        try {
            session?.resume()
        } catch (e: Exception) {
            Log.e(TAG, "session.resume failed", e)
            session = null
            return
        }

        glSurfaceView.setEGLContextClientVersion(3)
        glSurfaceView.setRenderer(
            ArCameraRenderer(
                session = session!!,
                getDisplayRotation = { @Suppress("DEPRECATION") windowManager.defaultDisplay.rotation },
                listener = this,
            )
        )
        glSurfaceView.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
        glSurfaceView.onResume()
    }

    override fun onPause() {
        super.onPause()
        glSurfaceView.onPause()
        session?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        sender.disconnect()
        session?.close()
        session = null
    }

    /** Creates the ARCore session on first use, requesting install/update as needed. Returns false if not ready yet. */
    private fun ensureSession(): Boolean {
        if (session != null) return true
        try {
            when (ArCoreApk.getInstance().requestInstall(this, !installRequested)) {
                ArCoreApk.InstallStatus.INSTALL_REQUESTED -> {
                    installRequested = true
                    return false
                }
                ArCoreApk.InstallStatus.INSTALLED -> {}
            }
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED
            ) {
                return false
            }
            val newSession = Session(this)
            val config = Config(newSession).apply {
                updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                planeFindingMode = Config.PlaneFindingMode.DISABLED
                lightEstimationMode = Config.LightEstimationMode.DISABLED
            }
            newSession.configure(config)
            session = newSession
            return true
        } catch (e: UnavailableArcoreNotInstalledException) {
            toastAndLog("ARCore is not installed", e)
        } catch (e: UnavailableUserDeclinedInstallationException) {
            toastAndLog("ARCore installation declined", e)
        } catch (e: UnavailableApkTooOldException) {
            toastAndLog("ARCore APK too old, please update Play Services for AR", e)
        } catch (e: UnavailableSdkTooOldException) {
            toastAndLog("App is built against an ARCore SDK older than the installed runtime", e)
        } catch (e: UnavailableDeviceNotCompatibleException) {
            toastAndLog("This device does not support ARCore", e)
        } catch (e: Exception) {
            toastAndLog("Failed to create AR session", e)
        }
        return false
    }

    private fun toastAndLog(message: String, e: Exception) {
        Log.e(TAG, message, e)
        uiHandler.post { Toast.makeText(this, message, Toast.LENGTH_LONG).show() }
    }

    // --- ArCameraRenderer.PoseListener — called on the GL thread, once per frame. ---

    override fun onTracking(
        timestampSeconds: Double,
        posX: Float, posY: Float, posZ: Float,
        quatX: Float, quatY: Float, quatZ: Float, quatW: Float,
    ) {
        if (recenterRequested.compareAndSet(true, false)) {
            refPos = floatArrayOf(posX, posY, posZ)
            refQuatInv = Quat.conjugate(floatArrayOf(quatX, quatY, quatZ, quatW))
        }

        val (relPos, relQuat) = relativeToReference(
            floatArrayOf(posX, posY, posZ),
            floatArrayOf(quatX, quatY, quatZ, quatW),
        )

        var flags = 0
        if (isRecording) flags = flags or CameraPacket.FLAG_RECORDING

        sender.sendPose(
            flags = flags,
            timestampSeconds = timestampSeconds,
            posX = relPos[0], posY = relPos[1], posZ = relPos[2],
            quatX = relQuat[0], quatY = relQuat[1], quatZ = relQuat[2], quatW = relQuat[3],
        )

        updateStatusThrottled(R.string.status_tracking)
    }

    override fun onNotTracking() {
        updateStatusThrottled(R.string.status_not_tracking)
    }

    /** Rebase [pos]/[quat] onto the last recenter pose, or pass through ARCore's absolute pose if Recenter hasn't been pressed yet. */
    private fun relativeToReference(pos: FloatArray, quat: FloatArray): Pair<FloatArray, FloatArray> {
        val rq = refQuatInv ?: return pos to quat
        val rp = refPos ?: return pos to quat
        val delta = floatArrayOf(pos[0] - rp[0], pos[1] - rp[1], pos[2] - rp[2])
        val relPos = Quat.rotateVector(rq, delta)
        val relQuat = Quat.multiply(rq, quat)
        return relPos to relQuat
    }

    private fun updateStatusThrottled(resId: Int) {
        val now = System.currentTimeMillis()
        if (now - lastUiUpdateMs < 200) return
        lastUiUpdateMs = now
        uiHandler.post { binding.statusText.setText(resId) }
    }

    companion object {
        private const val TAG = "MaxCamVCam"
        private const val PREFS_NAME = "maxcam_vcam_prefs"
        private const val PREF_KEY_IP = "server_ip"
        private const val PREF_KEY_PORT = "server_port"
    }
}
