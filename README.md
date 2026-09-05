# MaxCam VCam

Use an Android phone as a handheld 6DoF camera operator for a Free Camera
in 3ds Max, over your local network — walk around with the phone, and a
camera in your Max scene follows it live. Hold Record to bake the take
into real keyframes you can scrub and render.

Phone (ARCore world tracking) → UDP → 3ds Max (Python/`pymxs`) → Free
Camera transform, live.

## Status

Working and calibrated against a real device: live preview tracks
smoothly, recording bakes a clean take onto a separate camera, and the
coordinate-system remap between ARCore and Max has been verified (not
just assumed) against an actual phone. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md)
for exactly what was checked and how, and the "Known gaps" list in
[`max/README.md`](max/README.md) for what's still rough (no outlier
filtering on tracking glitches, no camera background preview on the
phone, IP has to be typed in by hand).

## Requirements

- An Android phone that supports [ARCore](https://developers.google.com/ar/devices)
  (most phones from the last several years; Google Play Services for AR
  gets installed automatically on first launch if missing).
- 3ds Max 2022 or newer (needs the built-in Python 3 / `pymxs` — this is
  what runs `max/maxcam_server.py`, no separate Python install needed).
- Phone and PC on the **same local network** (same Wi-Fi/router) — this
  doesn't work over mobile data or across different networks.
- Android Studio/SDK if you want to build the app yourself (see below);
  or just install a prebuilt APK from a GitHub Release, if one is attached.

## Quick start

**1. Get the app on your phone.** Either grab an APK from
[Releases](../../releases) and install it (you'll need to allow
"install from unknown sources" once), or build it yourself:

```bash
cd android
./gradlew.bat assembleDebug     # or ./gradlew on Mac/Linux
./gradlew.bat installDebug      # installs on a USB-connected, debugging-enabled phone
```

Launch "MaxCam VCam", grant the camera permission it asks for.

**2. Run the Max side.** In 3ds Max: `Scripting > Run Script...` →
select `max/maxcam_server.py`. It creates a `PhoneCam` Free Camera if one
doesn't exist yet and starts listening on UDP port `40111`. See
[`max/README.md`](max/README.md) for what it does and how recording
works.

**3. Connect.** See "Connecting" below — you need the Max PC's IP address
on your local network, which is different for every network/computer.

## Connecting

The phone needs the Max PC's **local network IP address** (not a public/
internet IP) — this is different every time, on every network, so there's
no default that works for everyone.

**Find the PC's IP (Windows):**

```powershell
ipconfig
```

Look for the network adapter you're actually using (usually "Wireless LAN
adapter Wi-Fi" or "Ethernet adapter Ethernet") and read its **IPv4
Address** — typically something like `192.168.1.70` or `10.0.0.15`. Ignore
adapters with no cable/Wi-Fi connected, and ignore `169.254.x.x` addresses
(those mean "not actually connected to anything").

**Then, on the phone:** enter that IP address and port `40111` in the app,
hit Connect.

**If it won't connect:**
- Confirm the phone and PC are on the *same* Wi-Fi network (not phone on
  mobile data, not PC on a different Wi-Fi/guest network/VPN).
- The first time `maxcam_server.py` binds its socket, Windows Firewall
  usually prompts to allow 3ds Max through — allow it for **Private**
  networks.
- The PC's IP can change (DHCP) if the router restarts or after a while —
  re-check with `ipconfig` if a previously-working connection stops
  working.
- Port `40111` is just this project's default; if it's already in use on
  your PC for something else, change `UDP_PORT` in `max/maxcam_server.py`
  and enter the matching port on the phone.

## Layout

- [`android/`](android/) — Kotlin/ARCore phone app. Reads 6DoF pose,
  streams it over UDP.
- [`max/`](max/) — Python UDP server for 3ds Max, drives a Free Camera and
  handles recording.
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — wire format, coordinate
  systems, and the debugging history behind the trickier fixes.

## License

No license file yet — add one (MIT is a common choice for a project like
this) if you want to make explicit what others are allowed to do with the
code; without one, default copyright applies and reuse isn't formally
granted even though the repo is public.
