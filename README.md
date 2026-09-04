# MaxCam VCam

Use an Android phone as a handheld 6DoF camera operator for a Free Camera
in 3ds Max, over the local network.

Phone (ARCore world tracking) → UDP → 3ds Max (Python/`pymxs`) → Free
Camera transform, live.

## Status: skeleton

Builds and runs, but the coordinate-system remap between ARCore and Max is
an unverified first guess — see the "Known gaps" note in
[`max/README.md`](max/README.md) and the coordinate-systems section of
[`docs/PROTOCOL.md`](docs/PROTOCOL.md). Everything else (permissions,
ARCore session lifecycle, UDP transport, recenter, the Max-side server
lifecycle) is implemented and has been built/tested end-to-end for
compilation; live on-device pose accuracy hasn't been calibrated yet.

## Layout

- [`android/`](android/) — Kotlin/ARCore phone app. Reads 6DoF pose,
  streams it over UDP.
- [`max/`](max/) — Python UDP server for 3ds Max, drives a Free Camera.
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — wire format and coordinate
  systems, the part most likely to need iteration.

## Build & run — Android

Toolchain already verified in this environment: ADB sees the phone,
Android SDK platforms 34/35 + build-tools installed, Gradle 8.11.1 cached
locally (`android/gradlew` wrapper generated from it, no network needed).

```bash
cd android
./gradlew.bat assembleDebug     # build
./gradlew.bat installDebug      # install on the USB-connected phone
```

Then launch "MaxCam VCam" on the phone, grant the camera permission, enter
the Max PC's LAN IP + port `40111`, and hit Connect.

## Run — 3ds Max

See [`max/README.md`](max/README.md): `Scripting > Run Script...` →
`max/maxcam_server.py`. It auto-creates a `PhoneCam` Free Camera and starts
listening on port `40111`.

## Next steps

1. Calibrate the axis remap in `max/maxcam_server.py` (`_remap_vec`)
   against the real device — walk/rotate the phone a known way, confirm
   the Max camera moves the way you'd expect, fix signs.
2. Decide on a scene-unit scale (`SCENE_SCALE` in `maxcam_server.py`) if
   the scene isn't in meters.
3. Optional: camera background preview in `ArCameraRenderer` (currently
   just clears to black — the texture is bound but never drawn), FOV/zoom
   control, recording tied to Max's timeline/autokey, mDNS discovery
   instead of typing the PC's IP by hand.
