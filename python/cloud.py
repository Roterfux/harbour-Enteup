# Enteup - SPDX-License-Identifier: GPL-3.0-or-later
"""Browse the photos in the Ente account.

sync()      fetches the albums and, per album, the files changed since the last sync
            (GET /collections/v2/diff) into a local index (cloud.db, readable by the user
            only). Hidden albums and hidden photos are left out everywhere; archived photos
            are left out of the timeline but shown in their albums, as in Ente's apps.
thumbnails  GET /files/preview/<id>, decrypted, and kept smaller than Ente's (THUMB_SHORT
            pixels on the short side, JPEG quality THUMB_QUALITY).
full files  GET /files/download/<id>, decrypted chunk by chunk; photos, videos and the two
            parts of live photos; the newest FULL_KEEP kept, within FULL_BYTES.
Ente's server answers both downloads with a redirect to its storage; the session token is
sent to Ente's server only, never to the storage.
"""
import collections
import http.client
import io
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import zipfile

import ente
import sodium
from ente import EnteError, VISIBLE, ARCHIVED, HIDDEN, b64, unb64
from sodium import SodiumError

THUMB_SHORT = 240            # stored thumbnails: short side (a grid cell is 270 px wide)
THUMB_QUALITY = 35
THUMB_VERSION = "240q35"     # change with the two above: kept thumbnails are shrunk to match
THUMB_BIG_SHORT = 480        # the few big tiles (2 or 3 cells): Ente's thumbnail, less shrunk
THUMB_CACHE = 200 * 1024 * 1024
THUMB_WORKERS = 4
THUMB_QUEUE = 300            # waiting thumbnails; the oldest are dropped when scrolling fast
FULL_KEEP = 8                # full photos and videos kept, newest first ...
FULL_BYTES = 1024 ** 3       # ... within this much space
SHOWABLE = ("jpg", "jpeg", "png", "gif", "bmp", "webp")
LIVE = 2                     # fileType of a live photo: a zip with a picture and a video
REDIRECTS = (301, 302, 303, 307, 308)


class _Links(threading.local):
    """Kept-open connections per thread and host: two requests per thumbnail would otherwise
    cost two TLS handshakes each."""

    def __init__(self):
        self.open = {}

    def get(self, url, headers):
        """(status, Location, body) of a GET; reconnects once when a kept link broke."""
        parts = urllib.parse.urlsplit(url)
        where = (parts.scheme, parts.netloc)
        target = parts.path + ("?" + parts.query if parts.query else "")
        for attempt in (0, 1):
            link = self.open.get(where)
            if link is None:
                kind = (http.client.HTTPSConnection if parts.scheme == "https"
                        else http.client.HTTPConnection)
                link = self.open[where] = kind(parts.netloc, timeout=ente.TIMEOUT)
            try:
                link.request("GET", target, headers=headers)
                reply = link.getresponse()
                body = reply.read()
                if reply.getheader("Connection", "").lower() == "close":
                    link.close()
                    self.open.pop(where, None)
                return reply.status, reply.getheader("Location"), body
            except (OSError, http.client.HTTPException) as err:
                link.close()
                self.open.pop(where, None)
                if attempt:
                    raise EnteError("Download interrupted (%s)" % (err or type(err).__name__))


_links = _Links()


def fetch(api, path):
    """The bytes behind one of Ente's download addresses."""
    status, where, body = _links.get(api.server + path, {
        "X-Auth-Token": api.token, "User-Agent": ente.USER_AGENT})
    if status in REDIRECTS and where:
        status, where, body = _links.get(urllib.parse.urljoin(api.server + path, where),
                                         {"User-Agent": ente.USER_AGENT})
    if status == 401:
        raise ente.Unauthorized("The session has ended; sign in again")
    if status != 200:
        raise EnteError("Download failed (%d)" % status)
    return body


def stream(api, path):
    """An open reply for a large download (read it in parts, then close it)."""
    status, where, body = _links.get(api.server + path, {
        "X-Auth-Token": api.token, "User-Agent": ente.USER_AGENT})
    if status == 401:
        raise ente.Unauthorized("The session has ended; sign in again")
    if status == 200:
        return io.BytesIO(body), len(body)
    if status not in REDIRECTS or not where:
        raise EnteError("Download failed (%d)" % status)
    url = urllib.parse.urljoin(api.server + path, where)
    parts = urllib.parse.urlsplit(url)
    kind = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    link = kind(parts.netloc, timeout=ente.TIMEOUT)
    try:
        link.request("GET", parts.path + ("?" + parts.query if parts.query else ""),
                     headers={"User-Agent": ente.USER_AGENT})
        reply = link.getresponse()
    except (OSError, http.client.HTTPException) as err:
        link.close()
        raise EnteError("Download failed (%s)" % (err or type(err).__name__))
    if reply.status != 200:
        link.close()
        raise EnteError("Download failed (%d)" % reply.status)
    return reply, int(reply.getheader("Content-Length") or 0)


def _day(seconds):
    """Local midnight of the day a moment falls on (epoch seconds)."""
    t = time.localtime(seconds)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def _same_date(seconds, today):
    """Taken on today's month and day, in an earlier year (local time)."""
    t = time.localtime(seconds)
    return t.tm_mon == today.tm_mon and t.tm_mday == today.tm_mday and t.tm_year < today.tm_year


BIG_MIN = 4                  # a day needs this many photos before one is shown big ...
BIG_EVERY = 8                # ... and gets one big tile per this many


def _pick_big(found, marks):
    """The few files of a day shown as big tiles: favorite videos, videos, favorite
    photos, then other photos, about one in BIG_EVERY. The same ones every time (a fixed
    shuffle by id), so the grid does not jump around."""
    if len(found) < BIG_MIN:
        return set()

    def rank(item):
        video, liked = item[2] == 1, bool(marks) and item[0] in marks
        order = 0 if video and liked else 1 if video else 2 if liked else 3
        return order, (item[0] * 2654435761) % 4294967296
    budget = max(1, len(found) // BIG_EVERY)
    return set(i[0] for i in sorted(found, key=rank)[:budget])


def _span(item, columns, big):
    """Cells a file takes (wide, high): a big one 3 x 2 when it is wider than high, 2 x 3
    when upright; when its size is not known, a video 3 x 2 and a photo 2 x 2. 1 x 1 for
    the rest."""
    if item[0] not in big or columns < 3:
        return 1, 1
    w, h = (item[3], item[4]) if len(item) >= 5 else (0, 0)
    if w > 0 and h > 0:
        return (2, 3) if h > w else (3, 2)
    return (3, 2) if item[2] == 1 else (2, 2)


def _cell_rows(found, columns, marks=None, big=False, chosen=None):
    """Grid rows for (id, taken, kind[, w, h]) under one header: files placed in order at
    the first free spot (a few as big tiles when big; their ids go into chosen), cut into
    bands no tile crosses. Each band {t: "r", h: its height in cells,
    cells: "id:kind:fav:x:y:w:h,..."}."""
    columns = max(1, int(columns))
    grid, placed, start = [], [], 0
    bigs = _pick_big(found, marks) if big and columns >= 3 else set()
    if chosen is not None:
        chosen.update(bigs)

    def fits(x, y, w, h):
        return all(yy >= len(grid) or not any(grid[yy][x:x + w]) for yy in range(y, y + h))
    for item in found:
        w, h = _span(item, columns, bigs)
        y = start
        while True:
            spot = next((x for x in range(columns - w + 1) if fits(x, y, w, h)), None)
            if spot is not None:
                break
            y += 1
        while len(grid) < y + h:
            grid.append([False] * columns)
        for yy in range(y, y + h):
            for xx in range(spot, spot + w):
                grid[yy][xx] = True
        placed.append((item, spot, y, w, h))
        while start < len(grid) and all(grid[start]):
            start += 1
    crossed = set()
    for _item, _x, y, _w, h in placed:
        crossed.update(range(y + 1, y + h))
    cuts = [r for r in range(1, len(grid)) if r not in crossed] + [len(grid)]
    bands, top = [], 0
    for cut in cuts:
        bands.append((top, cut))
        top = cut
    rows = []
    for top, bottom in bands:
        cells = ["%d:%d:%d:%d:%d:%d:%d" % (item[0], item[2],
                                          1 if marks and item[0] in marks else 0,
                                          x, y - top, w, h)
                 for item, x, y, w, h in placed if top <= y < bottom]
        rows.append({"t": "r", "h": bottom - top, "cells": ",".join(cells)})
    return rows


def _grid(found, columns, marks=None, big=False, chosen=None):
    """Grid rows from (id, taken, kind[, w, h]), newest first: {t: "d", day, n} for a
    day's header, then its photos as bands (see _cell_rows). marks: favorite ids."""
    rows, day, part = [], None, []

    def flush():
        if part:
            rows.append({"t": "d", "day": day, "n": len(part)})
            rows.extend(_cell_rows(part, columns, marks, big, chosen))
    for item in found:
        this = _day(item[1])
        if this != day:
            flush()
            day, part = this, []
        part.append(item)
    flush()
    return rows


def _months_back(today, months):
    """Local midnight of the same day of the month, months earlier (the month's last day
    when it is shorter)."""
    import calendar
    year, month = today.tm_year, today.tm_mon - months
    while month < 1:
        month += 12
        year -= 1
    day = min(today.tm_mday, calendar.monthrange(year, month)[1])
    return int(time.mktime((year, month, day, 0, 0, 0, 0, 0, -1)))


def _spread(items, count):
    """count items spread evenly over a list (all of it when it is short)."""
    if len(items) <= count:
        return list(items)
    step = len(items) / float(count)
    return [items[int(i * step)] for i in range(count)]


MEMORY_MONTHS = (1, 3, 6)    # "1 month ago", "3 months ago", "half a year ago"
MEMORY_REACH = 3             # ... the nearest day with photos, at most this many days off
BEST_COUNT = 12              # photos in "best of <month> <year>"


class Cloud:
    def __init__(self, folder, cache, send):
        self.send = send
        self.thumb_dir = os.path.join(cache, "thumbs")
        self.full_dir = os.path.join(cache, "full")
        os.makedirs(self.thumb_dir, exist_ok=True)
        os.makedirs(self.full_dir, exist_ok=True)
        self.path = os.path.join(folder, "cloud.db")
        self.lock = threading.RLock()
        # timeout: the app and the automatic backup may both write
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=60)
        os.chmod(self.path, 0o600)          # it holds the photos' keys
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS albums (id INTEGER PRIMARY KEY, name TEXT, type TEXT,
                key TEXT, visibility INTEGER, updated INTEGER, synced INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, key TEXT, title TEXT,
                taken INTEGER, kind INTEGER, w INTEGER, h INTEGER, thumb_header TEXT,
                file_header TEXT, size INTEGER, visibility INTEGER);
            CREATE TABLE IF NOT EXISTS members (file INTEGER, album INTEGER,
                PRIMARY KEY (file, album));
            CREATE INDEX IF NOT EXISTS members_album ON members (album);
            CREATE INDEX IF NOT EXISTS files_taken ON files (taken);
            CREATE TABLE IF NOT EXISTS info (name TEXT PRIMARY KEY, value TEXT);
        """)
        # 0.6.0: captions, and the sharer of albums shared with the user. Indexes from 0.5.0
        # are read again once, to fill them in.
        if "caption" not in [r[1] for r in self.db.execute("PRAGMA table_info(files)")]:
            self.db.execute("ALTER TABLE files ADD COLUMN caption TEXT DEFAULT ''")
            self.db.execute("UPDATE albums SET synced=0")
        if "owner" not in [r[1] for r in self.db.execute("PRAGMA table_info(albums)")]:
            self.db.execute("ALTER TABLE albums ADD COLUMN owner TEXT DEFAULT ''")
        # 0.8.0: each file's content hash (as Ente's apps write it), so uploads can skip
        # photos that are in the account already; read again once to fill it in
        if "hash" not in [r[1] for r in self.db.execute("PRAGMA table_info(files)")]:
            self.db.execute("ALTER TABLE files ADD COLUMN hash TEXT DEFAULT ''")
            self.db.execute("UPDATE albums SET synced=0")
        self.db.execute("CREATE INDEX IF NOT EXISTS files_hash ON files (hash)")
        # Ente's trash: read whole each time it is shown (it is small); album = the album
        # the file was trashed from, whose key opens its file key
        self.db.execute("CREATE TABLE IF NOT EXISTS trash (id INTEGER PRIMARY KEY, key TEXT,"
                        " title TEXT, taken INTEGER, kind INTEGER, thumb_header TEXT,"
                        " file_header TEXT, size INTEGER, album INTEGER, delete_by INTEGER)")
        # 0.11.0: on the phone most files had lost their hash (an older copy of the app,
        # still running during an update, wrote the rows again without it). Read all once
        # more, so duplicates, freeing up space and skipping photos already in Ente see them.
        if not self.db.execute("SELECT 1 FROM info WHERE name='hash_refill'").fetchall():
            if self.db.execute("SELECT 1 FROM files WHERE hash='' LIMIT 1").fetchall():
                self.db.execute("UPDATE albums SET synced=0")
            self.db.execute("INSERT OR REPLACE INTO info VALUES ('hash_refill', '1')")
        self.db.commit()
        self.api = None                     # set by the backend: a function giving an Api
        self._sync_lock = threading.Lock()
        self._wanted = collections.OrderedDict()
        self._failed = set()
        self._ready = threading.Condition()
        self._workers = []
        self._full_job = threading.Lock()
        self._big = set()                   # files laid out as big tiles: sharper thumbnails
        self.status = {"syncing": False, "albums": 0, "albumsDone": 0, "fetched": 0,
                       "files": self._sql("SELECT COUNT(*) FROM files")[0][0],
                       "synced": int(self._info("synced") or 0), "error": ""}

    # ------------------------------------------------------------ storage helpers

    def _sql(self, query, args=(), many=False):
        with self.lock:
            if many:
                self.db.executemany(query, args)
                self.db.commit()
                return []
            rows = self.db.execute(query, args).fetchall()
            self.db.commit()
            return rows

    def _info(self, name):
        rows = self._sql("SELECT value FROM info WHERE name=?", (name,))
        return rows[0][0] if rows else None

    def reset(self):
        """Forget everything (signing out): the index, thumbnails and full files."""
        with self.lock:
            self.db.executescript("DELETE FROM albums; DELETE FROM files; DELETE FROM members;"
                                  " DELETE FROM info; DELETE FROM trash;")
            # thumbnails made from now on are of the present kind: nothing to shrink later
            self.db.execute("INSERT OR REPLACE INTO info VALUES ('thumb_version', ?)",
                            (THUMB_VERSION,))
            self.db.commit()
        with self._ready:
            self._wanted.clear()
        for folder in (self.thumb_dir, self.full_dir):
            for name in os.listdir(folder):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
        self.status.update(synced=0, files=0, error="")

    # ------------------------------------------------------------ sync

    def _report(self, **changes):
        self.status.update(changes)
        self.send("cloud", dict(self.status))

    def sync(self, master, user_id, keypair=None):
        """Bring the index up to date. Returns False when a sync is running already.
        keypair (public, secret) opens albums shared with the user; without it they are only
        counted (status "locked")."""
        if not self._sync_lock.acquire(blocking=False):
            return False
        try:
            self._report(syncing=True, error="", albumsDone=0, fetched=0)
            api = self.api()
            found, locked = ente.all_collections(api, master, user_id, keypair)
            self.status["locked"] = locked
            have = set(a["id"] for a in found)
            stale = [(row[0],) for row in self._sql("SELECT id FROM albums")
                     if row[0] not in have]
            self._sql("DELETE FROM albums WHERE id=?", stale, many=True)
            self._sql("DELETE FROM members WHERE album=?", stale, many=True)
            for album in found:
                self._sql("UPDATE albums SET name=?, type=?, key=?, visibility=?, updated=?,"
                          " owner=? WHERE id=?",
                          (album["name"], album["type"], b64(album["key"]), album["visibility"],
                           album["updated"], album["owner"], album["id"]))
                self._sql("INSERT OR IGNORE INTO albums (id, name, type, key, visibility,"
                          " updated, synced, owner) VALUES (?,?,?,?,?,?,0,?)",
                          (album["id"], album["name"], album["type"], b64(album["key"]),
                           album["visibility"], album["updated"], album["owner"]))
            todo = self._sql("SELECT id, key, synced, updated FROM albums"
                             " WHERE updated > synced")
            self._report(albums=len(todo))
            for done, (album_id, key, synced, updated) in enumerate(todo):
                self._sync_album(api, album_id, unb64(key), synced)
                # everything up to now is in: an album whose own change (a new name, say)
                # is newer than its newest file would otherwise be asked again every time
                self._sql("UPDATE albums SET synced=MAX(synced, ?) WHERE id=?",
                          (updated, album_id))
                self._report(albumsDone=done + 1)
            self._sql("DELETE FROM files WHERE id NOT IN (SELECT file FROM members)")
            self._drop_orphan_thumbs()
            now = int(time.time())
            self._sql("INSERT OR REPLACE INTO info VALUES ('synced', ?)", (str(now),))
            count = self._sql("SELECT COUNT(*) FROM files")[0][0]
            self._report(syncing=False, synced=now, files=count)
            return True
        except ente.Unauthorized:
            self._report(syncing=False)
            raise
        except (EnteError, OSError) as err:
            self._report(syncing=False, error=str(err))
            return True
        finally:
            self._sync_lock.release()

    def _sync_album(self, api, album_id, key, since):
        while True:
            answer = api.call("GET", "/collections/v2/diff",
                              query={"collectionID": album_id, "sinceTime": since})
            part = answer.get("diff") or []
            # in the order of the changes, the last one per file wins: a file can leave the
            # album and come back in one answer (moved to the trash, then restored)
            files, present = [], {}
            for item in sorted(part, key=lambda i: i.get("updationTime") or 0):
                since = max(since, item.get("updationTime") or 0)
                if item.get("isDeleted"):
                    present[item["id"]] = False
                    continue
                row = self._file_row(item, key)
                if row:
                    files.append(row)
                    present[item["id"]] = True
            joins = [(f, album_id) for f, here in present.items() if here]
            leaves = [(f, album_id) for f, here in present.items() if not here]
            self._sql("INSERT OR REPLACE INTO files (id, key, title, taken, kind, w, h,"
                      " thumb_header, file_header, size, visibility, caption, hash)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", files, many=True)
            self._sql("INSERT OR IGNORE INTO members VALUES (?,?)", joins, many=True)
            self._sql("DELETE FROM members WHERE file=? AND album=?", leaves, many=True)
            self._sql("UPDATE albums SET synced=? WHERE id=?", (since, album_id))
            self._report(fetched=self.status.get("fetched", 0) + len(files))
            if not answer.get("hasMore") or not part:
                return

    @staticmethod
    def _file_row(item, album_key):
        """The index row for one file of Ente's diff; None when it cannot be read."""
        try:
            key = sodium.secretbox_open(unb64(item["encryptedKey"]),
                                        unb64(item["keyDecryptionNonce"]), album_key)
            meta = json.loads(sodium.blob_decrypt(unb64(item["metadata"]["encryptedData"]),
                                                  unb64(item["metadata"]["decryptionHeader"]),
                                                  key))
        except (SodiumError, EnteError, KeyError, ValueError, TypeError):
            return None
        public = ente.magic(item.get("pubMagicMetadata"), key)
        private = ente.magic(item.get("magicMetadata"), key)
        taken = public.get("editedTime") or meta.get("creationTime") or 0
        info = item.get("info") or {}
        return (item["id"], b64(key), public.get("editedName") or meta.get("title") or "",
                int(taken // 1000000), int(meta.get("fileType") or 0),
                int(public.get("w") or 0), int(public.get("h") or 0),
                (item.get("thumbnail") or {}).get("decryptionHeader") or "",
                (item.get("file") or {}).get("decryptionHeader") or "",
                int(info.get("fileSize") or (item.get("file") or {}).get("size") or 0),
                int(private.get("visibility") or VISIBLE),
                str(public.get("caption") or ""), str(meta.get("hash") or ""))

    # ------------------------------------------------------------ what to show

    # the timeline: photos in the user's own albums that are neither hidden nor archived
    # (as in Ente's apps, albums shared with the user stay out of it)
    _SHOWN = ("EXISTS (SELECT 1 FROM members m JOIN albums a ON a.id = m.album"
              " WHERE m.file = f.id AND a.visibility = %d AND a.owner = '')" % VISIBLE)

    def rows(self, album_id=0, columns=4):
        """The grid as list rows (see _grid). album_id 0 = the timeline."""
        if album_id:
            found = self._sql("SELECT f.id, f.taken, f.kind, f.w, f.h FROM files f JOIN members m"
                              " ON m.file = f.id WHERE m.album = ? AND f.visibility != ?"
                              " ORDER BY f.taken DESC, f.id DESC", (int(album_id), HIDDEN))
        else:
            found = self._timeline()
        return _grid(found, columns, self.favorite_set(), True, self._big)

    def _timeline(self):
        return self._sql("SELECT f.id, f.taken, f.kind, f.w, f.h FROM files f"
                         " WHERE f.visibility = ? AND " + self._SHOWN
                         + " ORDER BY f.taken DESC, f.id DESC", (VISIBLE,))

    def favorite_set(self):
        """The ids of the files in Ente's favorites album."""
        favorites = self.favorites_id()
        if not favorites:
            return set()
        return set(r[0] for r in self._sql("SELECT file FROM members WHERE album=?",
                                           (favorites,)))

    def on_this_day(self, columns=4, now=None):
        """The timeline's photos taken on today's date in earlier years, newest year first;
        each year's header also carries "ago" (years)."""
        today = time.localtime(now if now is not None else time.time())
        found = [i for i in self._timeline() if i[1] and _same_date(i[1], today)]
        rows = _grid(found, columns, self.favorite_set(), True, self._big)
        for row in rows:
            if row["t"] == "d":
                row["ago"] = today.tm_year - time.localtime(row["day"]).tm_year
        return rows

    def memories(self, columns=4, now=None, only=-1):
        """Looking back, as grid rows; each header says what it is:
          {t: "d", kind: "months", ago: 1|3|6, day, n}  the day 1 month, 3 months, half a
              year ago (or the nearest day with photos, at most MEMORY_REACH days off)
          {t: "d", kind: "years", ago, day, n}  today's date in an earlier year
          {t: "d", kind: "best", ago, day (the month's first), n, of}  the best of this month
              in an earlier year: favorites first, the rest spread over the month
        only >= 0: that one memory alone (its place in the list, as memory_covers has it).
        """
        marks = self.favorite_set()
        rows = []
        for place, (header, found) in enumerate(self._memory_sets(now, marks)):
            if only >= 0 and place != only:
                continue
            header.update(t="d", n=len(found))
            rows.append(header)
            rows.extend(_cell_rows(found, columns, marks, True, self._big))
        return rows

    def memory_covers(self, now=None):
        """The memories as title pictures: [{kind, ago, day, n, [of], cover}] in the order of
        memories(); cover = the file shown for it (a favorite photo when there is one, else
        a photo, else a video; the same one every time). Covers get the sharper thumbnail."""
        marks = self.favorite_set()
        covers = []
        for header, found in self._memory_sets(now, marks):
            def rank(item):
                return (0 if item[0] in marks else 1, 1 if item[2] == 1 else 0,
                        (item[0] * 2654435761) % 4294967296)
            cover = min(found, key=rank)[0]
            self._big.add(cover)
            header.update(n=len(found), cover=cover)
            covers.append(header)
        return covers

    def _memory_sets(self, now, marks):
        """[(header without t/n, [(id, taken, kind, w, h)])] for memories()."""
        now = now if now is not None else time.time()
        today = time.localtime(now)
        by_day, by_month, by_year = {}, {}, {}
        # only the days the month memories can land on are kept by day
        wanted = set()
        for months in MEMORY_MONTHS:
            target = _months_back(today, months)
            for off in range(-MEMORY_REACH, MEMORY_REACH + 1):
                wanted.add(_day(target + off * 86400 + 43200))
        first_wanted = min(wanted)
        for item in self._timeline():
            if not item[1]:
                continue
            t = time.localtime(item[1])
            if item[1] >= first_wanted:
                day = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))
                if day in wanted:
                    by_day.setdefault(day, []).append(item)
            if t.tm_mon == today.tm_mon and t.tm_year < today.tm_year:
                by_month.setdefault(t.tm_year, []).append(item)
                if t.tm_mday == today.tm_mday:
                    by_year.setdefault(t.tm_year, []).append(item)
        sets = []

        def section(header, found):
            sets.append((header, found))

        for months in MEMORY_MONTHS:
            target = _months_back(today, months)
            for off in [0] + [d * s for d in range(1, MEMORY_REACH + 1) for s in (-1, 1)]:
                day = _day(target + off * 86400 + 43200)    # noon: safe across DST
                if day in by_day:
                    section({"kind": "months", "ago": months, "day": day}, by_day[day])
                    break
        for year in sorted(by_year, reverse=True):
            found = sorted(by_year[year], key=lambda i: (-i[1], -i[0]))
            section({"kind": "years", "ago": today.tm_year - year,
                     "day": _day(found[0][1])}, found)
        for year in sorted(by_month, reverse=True):
            items = sorted(by_month[year], key=lambda i: (i[1], i[0]))
            liked = [i for i in items if i[0] in marks]
            rest = [i for i in items if i[0] not in marks]
            picked = liked[:BEST_COUNT] + _spread(rest, max(0, BEST_COUNT - len(liked)))
            picked.sort(key=lambda i: (i[1], i[0]))
            first = int(time.mktime((year, today.tm_mon, 1, 0, 0, 0, 0, 0, -1)))
            section({"kind": "best", "ago": today.tm_year - year, "day": first,
                     "of": len(items)}, picked)
        return sets

    # ------------------------------------------------------------ duplicates

    def duplicate_groups(self):
        """Files stored more than once in the user's own albums (same content hash), as
        lists of ids; in each, the one to keep first (in the most albums, then the oldest
        upload). Newest photos first."""
        found = self._sql(
            "SELECT f.hash, f.id, f.taken, (SELECT COUNT(*) FROM members m JOIN albums a"
            "  ON a.id = m.album WHERE m.file = f.id AND a.owner = '') AS n"
            " FROM files f WHERE f.hash != '' AND f.visibility != ? AND EXISTS"
            " (SELECT 1 FROM members m JOIN albums a ON a.id = m.album WHERE m.file = f.id"
            "  AND a.owner = '' AND a.visibility != ?)", (HIDDEN, HIDDEN))
        groups = {}
        for digest, file_id, taken, n in found:
            groups.setdefault(digest, []).append((-n, file_id, taken))
        result = []
        for items in groups.values():
            if len(items) > 1:
                items.sort()
                result.append((max(t for _n, _i, t in items), [i for _n, i, _t in items]))
        result.sort(key=lambda g: (-g[0], g[1][0]))
        return [ids for _t, ids in result]

    def duplicates(self, columns=4):
        """Grid rows of the duplicates: per group a header {t: "d", kind: "dup", day, n,
        title, size} and its copies, the one to keep first."""
        marks = self.favorite_set()
        rows = []
        for ids in self.duplicate_groups():
            info = self._sql("SELECT id, taken, kind, title, size FROM files WHERE id IN (%s)"
                             % ",".join("?" * len(ids)), ids)
            by_id = dict((r[0], r) for r in info)
            first = by_id[ids[0]]
            rows.append({"t": "d", "kind": "dup", "day": first[1] or 0, "n": len(ids),
                         "title": first[3] or "", "size": first[4] or 0})
            rows.extend(_cell_rows([by_id[i][:3] for i in ids], columns, marks))
        return rows

    def trash_duplicates(self, ids):
        """Move the chosen extra copies to Ente's trash. The copy that stays (one not
        chosen; when all of a group are chosen, its first) is put into every album of the
        user's the extras were in first, so no album loses the photo. Chosen files that are
        no duplicates are left alone. {done, skipped}"""
        chosen = set(int(i) for i in ids)
        extras, keepers_for, skipped, grouped = [], {}, 0, set()
        for group in self.duplicate_groups():
            grouped.update(group)
            gone = [i for i in group if i in chosen]
            if not gone:
                continue
            stay = [i for i in group if i not in chosen]
            if not stay:
                stay = [gone.pop(0)]
                skipped += 1
            keeper = stay[0]
            have = set(r[0] for r in self._sql("SELECT album FROM members WHERE file=?",
                                               (keeper,)))
            for file_id in gone:
                for (album_id,) in self._sql(
                        "SELECT m.album FROM members m JOIN albums a ON a.id = m.album"
                        " WHERE m.file = ? AND a.owner = ''", (file_id,)):
                    if album_id not in have:
                        have.add(album_id)
                        keepers_for.setdefault(album_id, []).append(keeper)
            extras.extend(gone)
        skipped += len(chosen - grouped)
        for album_id, keepers in keepers_for.items():
            self.add_to_album(keepers, album_id)
        result = self.trash_many(extras)
        return {"done": result["done"], "skipped": skipped + result["skipped"]}

    def albums(self):
        """Albums to browse: [{id, name, type, archived, count, cover, newest, owner,
        section}]: the user's own first, then those shared with the user (owner = the
        sharer), each newest photo first. Hidden albums are left out."""
        found = self._sql(
            "SELECT a.id, a.name, a.type, a.visibility, COUNT(f.id), MAX(f.taken),"
            " (SELECT f2.id FROM files f2 JOIN members m2 ON m2.file = f2.id"
            "  WHERE m2.album = a.id AND f2.visibility != ? ORDER BY f2.taken DESC LIMIT 1),"
            " a.owner"
            " FROM albums a LEFT JOIN members m ON m.album = a.id"
            " LEFT JOIN files f ON f.id = m.file AND f.visibility != ?"
            " WHERE a.visibility != ? GROUP BY a.id"
            " ORDER BY a.owner != '', MAX(f.taken) DESC",
            (HIDDEN, HIDDEN, HIDDEN))
        return [{"id": i, "name": n, "type": t, "archived": v == ARCHIVED, "count": c,
                 "newest": w or 0, "cover": cover or 0, "owner": owner or "",
                 "section": "shared" if owner else "own"}
                for i, n, t, v, c, w, cover, owner in found]

    def details(self, file_id):
        rows = self._sql("SELECT title, taken, kind, w, h, size, caption FROM files WHERE id=?",
                         (int(file_id),))
        if not rows:
            return {}
        title, taken, kind, w, h, size, caption = rows[0]
        favorites = self.favorites_id()
        return {"id": int(file_id), "title": title, "taken": taken, "video": kind == 1,
                "live": kind == LIVE, "w": w, "h": h, "size": size, "caption": caption or "",
                "own": bool(self.owned_album(file_id)),
                "favorite": bool(favorites and self._sql(
                    "SELECT 1 FROM members WHERE file=? AND album=?",
                    (int(file_id), favorites)))}

    def counts(self):
        timeline = self._sql("SELECT COUNT(*) FROM files f WHERE f.visibility = ? AND "
                             + self._SHOWN, (VISIBLE,))[0][0]
        return {"timeline": timeline, "files": self._sql("SELECT COUNT(*) FROM files")[0][0],
                "synced": self.status["synced"], "locked": self.status.get("locked", 0)}

    # ------------------------------------------------------------ thumbnails

    def thumb_path(self, file_id):
        return os.path.join(self.thumb_dir, "%d.jpg" % int(file_id))

    def thumbs(self, ids):
        """{id: path} for the thumbnails already here; the others are fetched in the
        background and announced with the event thumb(id, path) ("" when it failed)."""
        ready = {}
        with self._ready:
            for file_id in ids:
                file_id = int(file_id)
                path = self._thumb_for(file_id)
                if os.path.exists(path):
                    ready[str(file_id)] = path
                elif file_id not in self._failed:
                    self._wanted.pop(file_id, None)
                    self._wanted[file_id] = True        # newest wish last: fetched first
            while len(self._wanted) > THUMB_QUEUE:
                self._wanted.popitem(last=False)
            if self._wanted:
                self._ready.notify_all()
        while len(self._workers) < THUMB_WORKERS:
            worker = threading.Thread(target=self._thumb_worker, daemon=True)
            worker.start()
            self._workers.append(worker)
        return ready

    def retry_thumbs(self):
        with self._ready:
            self._failed.clear()

    def _thumb_worker(self):
        while True:
            with self._ready:
                while not self._wanted:
                    self._ready.wait()
                file_id, _ = self._wanted.popitem(last=True)
            path = self._thumb_for(file_id)
            if os.path.exists(path):
                self.send("thumb", file_id, path)
                continue
            try:
                self._make_thumb(file_id, path, path != self.thumb_path(file_id))
                self.send("thumb", file_id, path)
            except ente.Unauthorized as err:
                self._report(error=str(err))
                self.send("thumb", file_id, "")
            except Exception as err:            # one bad thumbnail must not stop the others
                with self._ready:
                    self._failed.add(file_id)
                self.status["error"] = "Thumbnail %d: %s" % (file_id, err)
                self.send("thumb", file_id, "")

    def _key_and_header(self, file_id, column):
        rows = (self._sql("SELECT key, %s FROM files WHERE id=?" % column, (int(file_id),))
                or self._sql("SELECT key, %s FROM trash WHERE id=?" % column, (int(file_id),)))
        if not rows:
            raise EnteError("This photo is no longer in the account")
        return unb64(rows[0][0]), unb64(rows[0][1])

    def ente_thumbnail(self, file_id):
        """Ente's own thumbnail of a file, decrypted (JPEG bytes)."""
        key, header = self._key_and_header(file_id, "thumb_header")
        return sodium.blob_decrypt(fetch(self.api(), "/files/preview/%d" % file_id), header, key)

    def _thumb_for(self, file_id):
        """The thumbnail the grid shows: the sharper one for a big tile."""
        if file_id in self._big:
            return os.path.join(self.thumb_dir, "%d_big.jpg" % int(file_id))
        return self.thumb_path(file_id)

    def _make_thumb(self, file_id, path, big=False):
        from PIL import Image
        data = self.ente_thumbnail(file_id)
        limit = THUMB_BIG_SHORT if big else THUMB_SHORT
        with Image.open(io.BytesIO(data)) as picture:
            picture = picture.convert("RGB")
            short = min(picture.size)
            if short > limit:
                scale = limit / float(short)
                picture = picture.resize((max(1, round(picture.size[0] * scale)),
                                          max(1, round(picture.size[1] * scale))),
                                         Image.BILINEAR)
            temp = path + ".part"
            picture.save(temp, "JPEG", quality=THUMB_QUALITY, optimize=True)
        os.replace(temp, path)

    def shrink_thumbs(self):
        """Bring thumbnails kept from an earlier version (larger, better quality) down to
        the present size and quality, from the files on the phone: nothing is fetched.
        Returns how many were rewritten; 0 when they are current."""
        if self._info("thumb_version") == THUMB_VERSION:
            return 0
        from PIL import Image
        done = 0
        for name in os.listdir(self.thumb_dir):
            path = os.path.join(self.thumb_dir, name)
            if not name.endswith(".jpg") or "_big" in name:
                continue
            try:
                with Image.open(path) as picture:
                    picture = picture.convert("RGB")
                    short = min(picture.size)
                    if short > THUMB_SHORT:
                        scale = THUMB_SHORT / float(short)
                        picture = picture.resize((max(1, round(picture.size[0] * scale)),
                                                  max(1, round(picture.size[1] * scale))),
                                                 Image.BILINEAR)
                    picture.save(path + ".part", "JPEG", quality=THUMB_QUALITY, optimize=True)
                if os.path.getsize(path + ".part") < os.path.getsize(path):
                    os.replace(path + ".part", path)
                    done += 1
                else:
                    os.remove(path + ".part")
            except Exception:
                try:                        # unreadable: fetched anew when it is next shown
                    os.remove(path)
                except OSError:
                    pass
        self._sql("INSERT OR REPLACE INTO info VALUES ('thumb_version', ?)", (THUMB_VERSION,))
        return done

    def trim_thumbs(self, limit=THUMB_CACHE):
        """Drop the least recently made thumbnails above the cache limit."""
        try:
            self.shrink_thumbs()
        except Exception:
            pass
        entries = []
        for name in os.listdir(self.thumb_dir):
            full = os.path.join(self.thumb_dir, name)
            try:
                info = os.stat(full)
            except OSError:
                continue
            entries.append((info.st_mtime, info.st_size, full))
        total = sum(e[1] for e in entries)
        for _, size, full in sorted(entries):
            if total <= limit:
                break
            try:
                os.remove(full)
                total -= size
            except OSError:
                pass
        return total

    def _drop_orphan_thumbs(self):
        have = set(str(r[0]) for r in self._sql("SELECT id FROM files UNION SELECT id FROM trash"))
        for folder in (self.thumb_dir, self.full_dir):
            for name in os.listdir(folder):
                if name.split(".")[0].split("_")[0] not in have:
                    try:
                        os.remove(os.path.join(folder, name))
                    except OSError:
                        pass

    # ------------------------------------------------------------ full photos

    def open_full(self, file_id):
        """Fetch and decrypt a file in the background. Events: full(id, {progress}) while
        it loads, then full(id, {path, note, error})."""
        file_id = int(file_id)
        worker = threading.Thread(target=self._full, args=(file_id,), daemon=True)
        worker.start()
        return True

    def _full(self, file_id):
        with self._full_job:            # one at a time: they are large
            try:
                self.send("full", file_id, self._make_full(file_id))
            except ente.Unauthorized as err:
                self._report(error=str(err))
                self.send("full", file_id, {"error": str(err)})
            except Exception as err:
                self.send("full", file_id, {"error": str(err) or type(err).__name__})

    def _kept(self, file_id):
        """What is here of a file already: {picture, preview, video, motion} paths."""
        found = {}
        prefix = str(file_id)
        for name in os.listdir(self.full_dir):
            stem, _, ext = name.partition(".")
            if ext.endswith("part"):
                continue
            full = os.path.join(self.full_dir, name)
            if stem == prefix:
                found["video" if ente.file_type(name) == ente.VIDEO else "picture"] = full
            elif stem == prefix + "_preview":
                found["preview"] = full
            elif stem == prefix + "_motion":
                found["motion"] = full
        return found

    @staticmethod
    def _cannot_show(ext):
        return ("This phone cannot show %s pictures; this is Ente's preview."
                % (ext.upper() or "these"))

    def _make_full(self, file_id):
        """{path: picture ("" for a video), video: video or a live photo's motion ("" for
        none), motion: is it a live photo's, note: why a preview stands in}."""
        info = self.details(file_id) or self.trash_details(file_id)
        if not info:
            raise EnteError("This photo is no longer in the account")
        ext = os.path.splitext(info["title"])[1][1:].lower()
        kept = self._kept(file_id)
        result = None
        if info["video"] and kept.get("video"):
            result = {"path": "", "video": kept["video"], "motion": False, "note": ""}
        elif info["live"] and kept.get("motion") and (kept.get("picture") or kept.get("preview")):
            result = {"path": kept.get("picture") or kept["preview"], "video": kept["motion"],
                      "motion": True, "note": "" if kept.get("picture") else
                      self._cannot_show("")}
        elif not info["video"] and not info["live"]:
            if kept.get("picture"):
                result = {"path": kept["picture"], "video": "", "motion": False, "note": ""}
            elif kept.get("preview") and ext not in SHOWABLE:
                result = {"path": kept["preview"], "video": "", "motion": False,
                          "note": self._cannot_show(ext)}
        if result:
            for path in (result["path"], result["video"]):
                if path:
                    os.utime(path)
            return result

        self._room_for(info["size"] * (2 if info["live"] else 1))
        temp = self._download(file_id)
        if info["video"]:
            if ext not in ente.VIDEO_TYPES:
                ext = "mp4"
            path = os.path.join(self.full_dir, "%d.%s" % (file_id, ext))
            os.replace(temp, path)
            self._trim_full(keep=(path,))
            return {"path": "", "video": path, "motion": False, "note": ""}
        motion = ""
        if info["live"]:
            temp, ext, motion = self._live_parts(temp, file_id)
        if ext not in SHOWABLE:
            os.remove(temp)
            result = self._preview_instead(file_id, self._cannot_show(ext))
        else:
            path = os.path.join(self.full_dir, "%d.%s" % (file_id, ext))
            os.replace(temp, path)
            result = {"path": path, "note": ""}
        result.update(video=motion, motion=bool(motion))
        self._trim_full(keep=(result["path"], motion))
        return result

    def _room_for(self, size):
        """Fail before a download that would fill the phone (some room is left over)."""
        try:
            info = os.statvfs(self.full_dir)
            free = info.f_bavail * info.f_frsize
        except (OSError, AttributeError):
            return
        if size + 300 * 1024 * 1024 > free:
            raise EnteError("Not enough free space on the phone (%d MB needed, %d MB free)"
                            % (size // (1024 * 1024) + 300, free // (1024 * 1024)))

    def _download(self, file_id):
        """Download and decrypt a file into a temporary file; its path."""
        key, header = self._key_and_header(file_id, "file_header")
        temp = os.path.join(self.full_dir, "%d.part" % file_id)
        reply, length = stream(self.api(), "/files/download/%d" % file_id)
        try:
            decrypt = sodium.StreamDecryptor(header, key)
            step = ente.CHUNK + sodium.STREAM_OVERHEAD
            got, final, shown = 0, False, 0.0
            with open(temp, "wb") as out:
                while not final:
                    block = reply.read(step)
                    while 0 < len(block) < step:
                        more = reply.read(step - len(block))
                        if not more:
                            break
                        block += more
                    if not block:
                        raise EnteError("Download cut off")
                    plain, final = decrypt.pull(block)
                    out.write(plain)
                    got += len(block)
                    if length and time.time() - shown > 0.3:
                        shown = time.time()
                        self.send("full", file_id, {"progress": got / float(length)})
        except SodiumError:
            os.remove(temp)
            raise EnteError("The download does not decrypt")
        except Exception:
            try:
                os.remove(temp)
            except OSError:
                pass
            raise
        finally:
            reply.close()
        return temp

    def _live_parts(self, temp, file_id):
        """A live photo is a zip of a picture and a short video: (picture as a temporary
        file, the picture's extension, the video's path or "")."""
        picture, ext, motion = None, "", ""
        try:
            with zipfile.ZipFile(temp) as packed:
                for name in packed.namelist():
                    kind = ente.file_type(name)
                    part_ext = os.path.splitext(name)[1][1:].lower()
                    if kind == ente.IMAGE and picture is None:
                        picture = os.path.join(self.full_dir, "%d.picture.part" % file_id)
                        ext = part_ext
                    elif kind == ente.VIDEO and not motion:
                        motion = os.path.join(self.full_dir, "%d_motion.%s" % (file_id, part_ext))
                    else:
                        continue
                    target = picture if kind == ente.IMAGE else motion
                    with packed.open(name) as source, open(target, "wb") as out:
                        while True:
                            block = source.read(1024 * 1024)
                            if not block:
                                break
                            out.write(block)
        except zipfile.BadZipFile:
            pass
        finally:
            os.remove(temp)
        if picture is None:
            if motion:
                os.remove(motion)
            raise EnteError("This live photo holds no picture")
        return picture, ext, motion

    def album_with_hash(self, digest):
        """The name of one of the user's own albums holding a file with this content
        hash (in the trash does not count); "" when there is none."""
        if not digest:
            return ""
        rows = self._sql("SELECT a.name FROM files f JOIN members m ON m.file = f.id"
                         " JOIN albums a ON a.id = m.album"
                         " WHERE f.hash = ? AND a.owner = '' LIMIT 1", (digest,))
        return rows[0][0] if rows else ""

    def synced_once(self):
        return bool(self._info("synced"))

    # ------------------------------------------------------------ save, trash

    def owned_album(self, file_id):
        """One of the user's own albums the file is in; 0 when there is none (a photo seen
        only in an album shared with the user cannot be moved to the trash)."""
        rows = self._sql("SELECT m.album FROM members m JOIN albums a ON a.id = m.album"
                         " WHERE m.file = ? AND a.owner = '' LIMIT 1", (int(file_id),))
        return rows[0][0] if rows else 0

    def save(self, file_id, pictures, videos):
        """Put the original of a file into the folder pictures (or videos), named as in
        Ente and dated when it was taken; a live photo puts its picture and its movement.
        Returns the new paths."""
        info = self.details(file_id) or self.trash_details(file_id)
        if not info:
            raise EnteError("This photo is no longer in the account")
        title = os.path.basename(info["title"].replace("\\", "/")) or "%d.jpg" % file_id
        base, ext = os.path.splitext(title)
        kept = self._kept(file_id)
        made = []
        if info["live"]:
            if not (kept.get("picture") and kept.get("motion")):
                self._room_for(info["size"] * 2)
                temp, pic_ext, motion = self._live_parts(self._download(file_id), file_id)
                kept = {"picture": temp, "motion": motion, "temporary": True}
                ext = "." + pic_ext
            else:
                ext = os.path.splitext(kept["picture"])[1]
            made.append(self._place(kept["picture"], pictures, base + ext, info["taken"],
                                    move=kept.get("temporary")))
            if kept.get("motion"):
                made.append(self._place(kept["motion"], videos,
                                        base + os.path.splitext(kept["motion"])[1],
                                        info["taken"], move=kept.get("temporary")))
            return made
        source = kept.get("video") if info["video"] else kept.get("picture")
        temporary = False
        if not source:
            self._room_for(info["size"])
            source, temporary = self._download(file_id), True
        made.append(self._place(source, videos if info["video"] else pictures, title,
                                info["taken"], move=temporary))
        return made

    @staticmethod
    def _place(source, folder, name, taken, move=False):
        """Copy (or move) a file into folder under name, without overwriting anything."""
        import shutil
        os.makedirs(folder, exist_ok=True)
        base, ext = os.path.splitext(name)
        target = os.path.join(folder, name)
        number = 2
        while os.path.exists(target):
            target = os.path.join(folder, "%s (%d)%s" % (base, number, ext))
            number += 1
        temp = target + ".part"
        if move:
            shutil.move(source, temp)
        else:
            shutil.copyfile(source, temp)
        if taken:
            os.utime(temp, (taken, taken))
        os.replace(temp, target)
        return target

    def trash(self, file_id):
        """Move a file to Ente's trash (out of all its albums; Ente keeps it 30 days)."""
        if not self.trash_many([file_id])["done"]:
            raise EnteError("Only photos in your own albums can be moved to the trash")
        return True

    TRASH_BATCH = 1000       # Ente's server takes at most this many items per request

    def trash_many(self, ids):
        """Move files to Ente's trash: one request item per file (Ente takes each file once
        and removes it from all its albums), at most TRASH_BATCH per request. Files only in
        albums shared with the user are left alone. {done, skipped}"""
        items, skipped, seen = [], 0, set()
        for file_id in ids:
            file_id = int(file_id)
            if file_id in seen:
                continue
            seen.add(file_id)
            album_id = self.owned_album(file_id)
            if album_id:
                items.append({"fileID": file_id, "collectionID": album_id})
            else:
                skipped += 1
        done = 0
        for start in range(0, len(items), self.TRASH_BATCH):
            part = items[start:start + self.TRASH_BATCH]
            self.api().call("POST", "/files/trash", {"items": part})
            gone = [(i["fileID"],) for i in part]
            self._sql("DELETE FROM members WHERE file=?", gone, many=True)
            self._sql("DELETE FROM files WHERE id=?", gone, many=True)
            done += len(part)
        if done:
            self._drop_orphan_thumbs()
        return {"done": done, "skipped": skipped}

    # ------------------------------------------------------------ albums and favorites

    BATCH = 1000             # Ente's server takes at most this many files per request

    def _album_key(self, album_id):
        """The key of one of the user's own albums."""
        rows = self._sql("SELECT key FROM albums WHERE id=? AND owner=''", (int(album_id),))
        if not rows:
            raise EnteError("That album is not one of yours")
        return unb64(rows[0][0])

    def _items(self, ids, album_key, table="files"):
        """[{id, encryptedKey, keyDecryptionNonce}]: each file's key sealed for an album, as
        Ente's apps send it when they put files into an album."""
        items = []
        for file_id in ids:
            key = unb64(self._sql("SELECT key FROM %s WHERE id=?" % table, (file_id,))[0][0])
            sealed, nonce = sodium.secretbox(key, album_key)
            items.append({"id": file_id, "encryptedKey": b64(sealed),
                          "keyDecryptionNonce": b64(nonce)})
        return items

    def _mine(self, ids):
        """The given files that are in one of the user's own albums, each once, in order."""
        seen, mine = set(), []
        for file_id in ids:
            file_id = int(file_id)
            if file_id not in seen and self.owned_album(file_id):
                seen.add(file_id)
                mine.append(file_id)
        return mine

    def add_to_album(self, ids, album_id):
        """Put files into one of the user's albums too (they stay where they are).
        {done, skipped}: skipped = not the user's, or in that album already."""
        album_id = int(album_id)
        key = self._album_key(album_id)
        there = set(r[0] for r in self._sql("SELECT file FROM members WHERE album=?",
                                            (album_id,)))
        todo = [i for i in self._mine(ids) if i not in there]
        for start in range(0, len(todo), self.BATCH):
            part = todo[start:start + self.BATCH]
            self.api().call("POST", "/collections/add-files",
                            {"collectionID": album_id, "files": self._items(part, key)})
            self._sql("INSERT OR IGNORE INTO members VALUES (?,?)",
                      [(i, album_id) for i in part], many=True)
        return {"done": len(todo), "skipped": len(set(int(i) for i in ids)) - len(todo)}

    def move_to_album(self, ids, from_id, to_id):
        """Move files from one of the user's albums to another. {done, skipped}"""
        from_id, to_id = int(from_id), int(to_id)
        if from_id == to_id:
            return {"done": 0, "skipped": len(ids)}
        self._album_key(from_id)
        key = self._album_key(to_id)
        inside = set(r[0] for r in self._sql("SELECT file FROM members WHERE album=?",
                                             (from_id,)))
        todo = [i for i in self._mine(ids) if i in inside]
        for start in range(0, len(todo), self.BATCH):
            part = todo[start:start + self.BATCH]
            self.api().call("POST", "/collections/move-files",
                            {"fromCollectionID": from_id, "toCollectionID": to_id,
                             "files": self._items(part, key)})
            self._sql("DELETE FROM members WHERE file=? AND album=?",
                      [(i, from_id) for i in part], many=True)
            self._sql("INSERT OR IGNORE INTO members VALUES (?,?)",
                      [(i, to_id) for i in part], many=True)
        return {"done": len(todo), "skipped": len(set(int(i) for i in ids)) - len(todo)}

    def favorites_id(self):
        rows = self._sql("SELECT id FROM albums WHERE type='favorites' AND owner=''"
                         " ORDER BY id LIMIT 1")
        return rows[0][0] if rows else 0

    def _special_album(self, kind, name, master):
        """Ente's favorites or uncategorized album; made, as Ente's apps do, when missing."""
        rows = self._sql("SELECT id FROM albums WHERE type=? AND owner='' ORDER BY id LIMIT 1",
                         (kind,))
        if rows:
            return rows[0][0]
        made = ente.create_album(self.api(), master, name, kind)
        self._sql("INSERT OR IGNORE INTO albums (id, name, type, key, visibility, updated,"
                  " synced, owner) VALUES (?,?,?,?,?,?,?, '')",
                  (made["id"], name, kind, b64(made["key"]), VISIBLE, made["updated"],
                   made["updated"]))
        return made["id"]

    def set_favorite(self, ids, on, master):
        """Favorite files (into Ente's favorites album) or not any more. Leaving favorites
        works as in Ente's apps: a file moves to another of the user's albums that has it
        already, or to "Uncategorized" when it is in no other. {done, skipped}"""
        if on:
            return self.add_to_album(ids, self._special_album("favorites", "Favorites", master))
        favorites = self.favorites_id()
        if not favorites:
            return {"done": 0, "skipped": len(ids)}
        inside = set(r[0] for r in self._sql("SELECT file FROM members WHERE album=?",
                                             (favorites,)))
        todo = [i for i in self._mine(ids) if i in inside]
        places = {}
        for file_id in todo:
            other = self._sql("SELECT a.id FROM members m JOIN albums a ON a.id = m.album"
                              " WHERE m.file=? AND a.owner='' AND a.id != ?"
                              " AND a.type != 'favorites' ORDER BY a.type = 'uncategorized'"
                              " LIMIT 1", (file_id, favorites))
            places.setdefault(other[0][0] if other else None, []).append(file_id)
        if None in places:
            places.setdefault(self._special_album("uncategorized", "Uncategorized", master),
                              []).extend(places.pop(None))
        for target, part in places.items():
            self.move_to_album(part, favorites, target)
        return {"done": len(todo), "skipped": len(set(int(i) for i in ids)) - len(todo)}

    # ------------------------------------------------------------ Ente's trash

    def sync_trash(self):
        """Read Ente's trash anew. Files whose album is gone cannot be opened and are
        counted as unreadable. {count, unreadable}"""
        api = self.api()
        since, found = 0, {}
        while True:
            answer = api.call("GET", "/trash/v2/diff", query={"sinceTime": since})
            part = answer.get("diff") or []
            for item in part:
                since = max(since, item.get("updatedAt") or 0)
                entry = item.get("file") or {}
                if item.get("isDeleted") or item.get("isRestored"):
                    found.pop(entry.get("id"), None)
                else:
                    found[entry.get("id")] = (entry, item.get("deleteBy") or 0)
            if not answer.get("hasMore") or not part:
                break
        rows, unreadable = [], 0
        keys = dict((r[0], unb64(r[1])) for r in self._sql("SELECT id, key FROM albums"))
        for entry, delete_by in found.values():
            key = keys.get(entry.get("collectionID"))
            row = self._file_row(entry, key) if key else None
            if not row:
                unreadable += 1
                continue
            # id, key, title, taken, kind, thumb_header, file_header, size, album, delete_by
            rows.append((row[0], row[1], row[2], row[3], row[4], row[7], row[8], row[9],
                         entry.get("collectionID"), int(delete_by // 1000000)))
        with self.lock:
            self.db.execute("DELETE FROM trash")
            self.db.executemany("INSERT OR REPLACE INTO trash VALUES (?,?,?,?,?,?,?,?,?,?)",
                                rows)
            self.db.execute("INSERT OR REPLACE INTO info VALUES ('trash_updated', ?)",
                            (str(since),))
            self.db.commit()
        return {"count": len(rows), "unreadable": unreadable}

    def trash_rows(self, columns=4):
        return _grid(self._sql("SELECT id, taken, kind FROM trash ORDER BY taken DESC, id DESC"),
                     columns)

    def trash_details(self, file_id):
        rows = self._sql("SELECT title, taken, kind, size, delete_by FROM trash WHERE id=?",
                         (int(file_id),))
        if not rows:
            return {}
        title, taken, kind, size, delete_by = rows[0]
        return {"id": int(file_id), "title": title, "taken": taken, "video": kind == 1,
                "live": kind == LIVE, "size": size, "deleteBy": delete_by, "trash": True}

    def restore(self, ids, master):
        """Bring files back from Ente's trash: into the album they left when it is still one
        of the user's (and not hidden), else into "Uncategorized". {done, skipped}"""
        wanted = set(int(i) for i in ids)
        rows = [r for r in self._sql("SELECT id, album FROM trash") if r[0] in wanted]
        mine = dict(self._sql("SELECT id, visibility FROM albums WHERE owner=''"))
        places = {}
        for file_id, album_id in rows:
            target = album_id if album_id in mine and mine[album_id] != HIDDEN else None
            places.setdefault(target, []).append(file_id)
        if None in places:
            places.setdefault(self._special_album("uncategorized", "Uncategorized", master),
                              []).extend(places.pop(None))
        done = 0
        for target, part in places.items():
            key = self._album_key(target)
            for start in range(0, len(part), self.BATCH):
                chunk = part[start:start + self.BATCH]
                self.api().call("POST", "/collections/restore-files",
                                {"collectionID": target,
                                 "files": self._items(chunk, key, table="trash")})
                self._sql("DELETE FROM trash WHERE id=?", [(i,) for i in chunk], many=True)
                done += len(chunk)
        return {"done": done, "skipped": len(wanted) - done}

    def delete_forever(self, ids):
        """Delete files from Ente's trash for good. {done, skipped}"""
        wanted = set(int(i) for i in ids)
        todo = [r[0] for r in self._sql("SELECT id FROM trash") if r[0] in wanted]
        for start in range(0, len(todo), self.BATCH):
            part = todo[start:start + self.BATCH]
            self.api().call("POST", "/trash/delete", {"fileIDs": part})
            self._sql("DELETE FROM trash WHERE id=?", [(i,) for i in part], many=True)
        if todo:
            self._drop_orphan_thumbs()
        return {"done": len(todo), "skipped": len(wanted) - len(todo)}

    def empty_trash(self):
        """Empty Ente's trash up to what was last read (newer arrivals stay, as Ente's apps
        do it). {done}"""
        since = int(self._info("trash_updated") or 0)
        count = self._sql("SELECT COUNT(*) FROM trash")[0][0]
        self.api().call("POST", "/trash/empty", {"lastUpdatedAt": since})
        self._sql("DELETE FROM trash")
        self._drop_orphan_thumbs()
        return {"done": count, "skipped": 0}

    def _preview_instead(self, file_id, note):
        path = os.path.join(self.full_dir, "%d_preview.jpg" % file_id)
        if not os.path.exists(path):
            with open(path + ".part", "wb") as out:
                out.write(self.ente_thumbnail(file_id))
            os.replace(path + ".part", path)
        return {"path": path, "note": note}

    def _trim_full(self, keep=()):
        """Keep the newest FULL_KEEP files within FULL_BYTES; keep (the files just made)
        always stay."""
        entries = []
        for name in os.listdir(self.full_dir):
            if name.endswith("part"):
                continue
            full = os.path.join(self.full_dir, name)
            try:
                info = os.stat(full)
            except OSError:
                continue
            entries.append((full in keep, info.st_mtime, info.st_size, full))
        total = 0
        for index, (kept, _, size, full) in enumerate(sorted(entries, reverse=True)):
            if kept or (index < FULL_KEEP and total + size <= FULL_BYTES):
                total += size
                continue
            try:
                os.remove(full)
            except OSError:
                pass
