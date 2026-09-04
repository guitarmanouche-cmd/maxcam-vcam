# 3ds Max side

Requires 3ds Max 2022+ (built-in Python 3 / `pymxs`); developed against
2024.

## Run

1. `Scripting > Run Script...` → select `maxcam_server.py`.
   (Or from the MAXScript listener: `python.ExecuteFile @"<full path>\maxcam_server.py"`)
2. It creates a `PhoneCam` Free Camera if one doesn't already exist, and
   starts listening on UDP port `40111`.
3. On the phone, enter this PC's LAN IP and port `40111`, hit Connect.
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

## Files

- `maxcam_server.py` — the UDP server + camera-transform apply loop.
- `protocol.py` — packet decode, shared spec with the Android sender (see
  [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md)).

## Known gaps (skeleton status)

- Axis remap in `_remap_vec()` is a first guess, not calibrated against the
  real phone yet — see the coordinate-systems section in
  [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md).
- No on-screen/listener feedback loop for tracking quality — check the
  MAXScript listener for `[MaxCamServer]` log lines.
- No firewall setup included — Windows will likely prompt to allow 3ds Max
  through the firewall the first time the socket binds; allow it for
  Private networks.
