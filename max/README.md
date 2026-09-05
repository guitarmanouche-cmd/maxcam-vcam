# 3ds Max side

Requires 3ds Max 2022+ (built-in Python 3 / `pymxs`); developed against
2024.

## Run

1. `Scripting > Run Script...` → select `maxcam_server.py`.
   (Or from the MAXScript listener: `python.ExecuteFile @"<full path>\maxcam_server.py"`)
2. It creates a `PhoneCam` Free Camera if one doesn't already exist, and
   starts listening on UDP port `40111`.
3. On the phone, enter this PC's LAN IP and port `40111`, hit Connect.
   Look through `PhoneCam` in the viewport for the live feed.
4. Re-running the script (after an edit) automatically stops the previous
   instance first — no need to restart Max.

To stop without closing Max: in the listener, `python.ExecuteFile` again
having edited it to call `stop()` instead, or simply:

```python
import maxcam_server
maxcam_server.stop()
```

(only works if it was `import`-ed rather than run via Run Script; if you
ran it via Run Script, just close/reopen Max or re-run the script to
replace the running instance).

## Recording

Hold the Record button in the Android app to bake real keyframes onto a
**separate** camera, `PhoneCam_Take` (auto-created on first use) — **not**
`PhoneCam` itself. To review a take, switch the viewport to look through
`PhoneCam_Take` and scrub/play the timeline; `PhoneCam` keeps showing the
live feed the whole time, unaffected by the timeline position.

Why two cameras: confirmed live on 2026-09-05 that assigning a value to an
already-keyed property in Max *outside* animate mode doesn't just preview
it at the current time — it shifts every existing key by the delta needed
to match the new value. `PhoneCam` gets a plain (non-animate) `.transform`
set on every packet for the live view; if it ever picked up keys, each of
those live updates would silently re-shift the *entire* recorded curve,
scale included, which is what was actually causing the squashing/judder
during recording. Keeping the live camera permanently key-free and baking
the take onto a different object sidesteps this rather than fighting it.

While recording, the server tracks wall-clock time since Record was
pressed, converts it to a frame number using the scene's frame rate
(`rt.frameRate`), and sets a key on `PhoneCam_Take` there each time a new
frame is reached — extending `animationRange` if needed. Releasing Record
just stops adding keys. Pressing Record again starts a fresh take from
whatever frame the time slider is currently on (it'll overwrite keys in
that range).

Rotation uses a TCB controller (forced on both cameras in `_ensure_camera`)
rather than the default Euler XYZ — Euler decomposition of a rotation
matrix isn't unique, so two keys with nearly-identical real orientation
can land on very different Euler triples, which showed up as the camera
flipping look-up/look-down every frame on playback.

## Files

- `maxcam_server.py` — the UDP server, camera-transform apply loop, and
  recording (keyframe baking).
- `protocol.py` — packet decode, shared spec with the Android sender (see
  [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md)).

## Known gaps (skeleton status)

- No on-screen/listener feedback loop for tracking quality — check the
  MAXScript listener for `[MaxCamServer]` log lines.
- No firewall setup included — Windows will likely prompt to allow 3ds Max
  through the firewall the first time the socket binds; allow it for
  Private networks.
- No outlier/jump filtering on incoming poses — an ARCore tracking glitch
  (lost features, fast motion) can snap the camera to a bad pose that then
  holds until a good sample arrives. Add a max-delta-per-frame sanity check
  in `_apply_latest` if this shows up often in practice.
- Recording interpolation is whatever the default controller gives you
  (see "Recording" above) — not verified against a real recorded take yet.
