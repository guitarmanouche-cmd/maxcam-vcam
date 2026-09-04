package com.maxcam.vcam.ar

/**
 * Minimal quaternion/vector math used to rebase ARCore's absolute world pose
 * onto the pose captured at the last "Recenter" press, so the operator can
 * start the shot from wherever they're standing instead of ARCore's
 * arbitrary session origin. All quaternions are (x, y, z, w).
 */
object Quat {
    fun conjugate(q: FloatArray): FloatArray = floatArrayOf(-q[0], -q[1], -q[2], q[3])

    fun multiply(a: FloatArray, b: FloatArray): FloatArray {
        val (ax, ay, az, aw) = a
        val (bx, by, bz, bw) = b
        return floatArrayOf(
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )
    }

    /** Rotates vector [v] (x,y,z) by unit quaternion [q]. */
    fun rotateVector(q: FloatArray, v: FloatArray): FloatArray {
        val qv = floatArrayOf(v[0], v[1], v[2], 0f)
        val result = multiply(multiply(q, qv), conjugate(q))
        return floatArrayOf(result[0], result[1], result[2])
    }
}
