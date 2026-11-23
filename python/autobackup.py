# Enteup - SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic backup, run by the systemd user timer harbour-enteup-backup.timer.

It does nothing unless automatic backup is switched on in the app. Then it uploads what is
new in the switched-on upload sources, as "Upload new from sources" does, when:
  - there is a connection (on Wi-Fi only, if the app says so),
  - the battery is not low (unless charging),
  - the app itself is not uploading at that moment.
What it did goes to auto-status.json next to the session, which the app shows.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backend  # noqa: E402

LOW_BATTERY = 20             # percent; below it, runs wait for the charger


def on_wifi(route="/proc/net/route"):
    """True on Wi-Fi (or a cable), False on mobile data, None without a connection: the
    interface of the default route with the lowest metric decides."""
    try:
        with open(route) as source:
            lines = source.read().splitlines()[1:]
    except OSError:
        return None
    best = None
    for line in lines:
        fields = line.split()
        if len(fields) < 7 or fields[1] != "00000000":
            continue
        try:
            if not int(fields[3], 16) & 1:          # RTF_UP
                continue
            metric = int(fields[6])
        except ValueError:
            continue
        if best is None or metric < best[0]:
            best = (metric, fields[0])
    if best is None:
        return None
    return best[1].startswith(("wlan", "wl", "eth", "en"))


def battery_low(base="/sys/class/power_supply"):
    """True when the battery is below LOW_BATTERY percent and not charging."""
    try:
        names = os.listdir(base)
    except OSError:
        return False
    for name in names:
        folder = os.path.join(base, name)
        try:
            with open(os.path.join(folder, "type")) as source:
                if source.read().strip() != "Battery":
                    continue
            with open(os.path.join(folder, "capacity")) as source:
                capacity = int(source.read().strip())
            with open(os.path.join(folder, "status")) as source:
                status = source.read().strip()
        except (OSError, ValueError):
            continue
        return capacity < LOW_BATTERY and status not in ("Charging", "Full")
    return False


def _log(name, *args):
    """Events of the engine, written to the journal (stdout of the service)."""
    if name == "result":
        result = args[0]
        print("%s: %s %s" % (result["status"], result["name"], result.get("detail", "")),
              flush=True)


def run(folder=None, route="/proc/net/route", power="/sys/class/power_supply"):
    """One run; returns the status it wrote (None when automatic backup is off)."""
    backend.send = _log
    backend.UPLOAD_LOCK_WAIT = False
    backend.init(folder)
    settings = backend.auto_settings()
    if not settings["on"]:
        return None
    status = {"at": int(time.time()), "ran": False, "reason": "", "uploaded": 0,
              "skipped": 0, "failed": 0, "error": ""}
    network = on_wifi(route)
    if not backend.session.get("token"):
        status["reason"] = "Not signed in"
    elif backend.sodium.available():
        status["reason"] = backend.sodium.available()
    elif network is None:
        status["reason"] = "No connection"
    elif settings["wifiOnly"] and not network:
        status["reason"] = "Waiting for Wi-Fi"
    elif battery_low(power):
        status["reason"] = "Battery low"
    if not status["reason"]:
        queued = backend.upload_sources(settings["videos"], "")
        backend.wait()
        up = backend._up
        status.update(ran=True, uploaded=up["done"] if queued["queued"] else 0,
                      skipped=up["skipped"] if queued["queued"] else 0,
                      failed=up["failed"] if queued["queued"] else 0,
                      error=backend.batch_error[0] if queued["queued"] else "")
        if queued["noAlbum"]:
            status["reason"] = "No album chosen for: " + ", ".join(queued["noAlbum"])
        if status["error"] == "The app is uploading right now":
            status.update(ran=False, reason=status["error"], error="")
    backend._write_json(backend._auto_file("auto-status.json"), status)
    print("automatic backup: %s" % status, flush=True)
    return status


if __name__ == "__main__":
    run()
