# Enteup

A native [Ente Photos](https://ente.io) client for Sailfish OS (written for 5.2): backs up the
phone's photos and videos to Ente and browses the account. Silica QML pages and a Python engine
through PyOtherSide, no web view. Everything is encrypted on the phone with libsodium, in the
formats Ente's own apps use.

**An independent app, not made or supported by Ente.**

## Install

Download the `.noarch.rpm` from the [releases](../../releases) and install it on the phone
(file manager, or `pkcon install-local harbour-enteup-*.noarch.rpm`). It pulls in `libsodium`,
`python3-imaging` and PyOtherSide.

## What it does

**Backup**
- Sign in with the code Ente mails (plus the authenticator code with two-factor sign-in), then
  the password, which unlocks the keys on the phone.
- Any number of upload sources (camera folders, memory card, Android storage), each into the
  main album or an album of its own.
- Photos already in Ente are skipped by content, also ones uploaded from elsewhere; empty and
  broken files are skipped.
- Automatic backup every 15 minutes through a systemd user timer (Wi-Fi only and battery
  limits optional).
- Free up space on the phone: deletes local files whose exact content is safely in Ente, after
  checking each again.
- The plan's used and free space, and the phone's.

**Browsing**
- The timeline by day, albums, albums shared with you; hidden and archived photos left out as in
  Ente's apps.
- Memories as title pictures on top: 1 month, 3 months and half a year ago, this day in earlier
  years, the best of this month in earlier years.
- A few photos and videos per day shown big; the topmost big video plays silently (Wi-Fi, small
  videos).
- Full screen with swipe and pinch zoom, videos, live photos, captions.
- Save, share, favorite, add to / move between albums, trash; Ente's trash with restore;
  duplicate finder.

Not done: passkey and SRP sign-in, multipart upload, video thumbnails.

The session (server token and keys) is kept in `~/.local/share/harbour/enteup/`, readable only
by the user; the password is not kept. "Sign out" removes it with the cached thumbnails.

## Layout

    python/sodium.py      libsodium through ctypes
    python/ente.py        Ente's API and file encryption
    python/backend.py     what the QML calls: session, sources, uploads, free-up
    python/cloud.py       the account index, grid layout, memories, duplicates, trash
    python/autobackup.py  the timer's backup run
    qml/                  the pages
    tests/                engine tests against a stand-in server
    tools/                icon renderer, RPM build, on-phone QML checks

## Build and test (Linux / WSL, plain rpmbuild)

    python3 -m unittest discover -s tests      # needs libsodium and python3-pillow
    python3 tools/make_icons.py
    sh tools/build-rpm.sh                      # -> RPMS/noarch/

## License

Copyright (C) 2026 the Enteup contributors.

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version (`GPL-3.0-or-later`). It is distributed WITHOUT
ANY WARRANTY; see [LICENSE](LICENSE) for the full text.

Ente is a trademark of its owners; this project is not affiliated with Ente.
