# Enteup - SPDX-License-Identifier: GPL-3.0-or-later
"""What the QML side calls (through PyOtherSide).

init() and the small setters answer at once. Everything that talks to the server is put in
a queue for one worker thread and reports with events:
    state(dict)             the whole state for the UI (see state())
    login(step, message)    "code" | "totp" | "password" | "done" | "error"
    albums(list, message)   [{id, name, type}] after albums() / create_album()
    upload(dict)            progress of the upload queue (see _progress())
    result(dict)            one finished file: {name, status, detail}
                            status: "done" | "skipped" | "failed"
"""
import json
import os
import queue
import sqlite3
import threading
import time

import cloud
import ente
import sodium
from ente import EnteError

try:
    import pyotherside
except ImportError:          # tests on a PC
    pyotherside = None

APP = "harbour/enteup"
events = []                  # filled instead of sending when there is no PyOtherSide
data_dir = ""
cache_dir = ""

_db = None
_db_lock = threading.Lock()
_jobs = queue.Queue()
_worker = None
_cancel = threading.Event()

session = {}                 # server, email, userId, token, masterKey (base64)
album = {}                   # id, name - where uploads go
_album_keys = {}             # album id -> key
_pending = {}                # sign-in in progress: api, email, answer, totp
_up = {"active": False, "total": 0, "done": 0, "skipped": 0, "failed": 0,
       "name": "", "stage": "", "fraction": 0.0}
last_error = ""
_batch = [0, 0]              # number of the current upload batch, upload jobs still queued
_cloud = None                # the account's photos, for browsing (cloud.Cloud)
batch_error = [""]           # why the last upload batch stopped early ("" = it did not)


def send(name, *args):
    if pyotherside:
        pyotherside.send(name, *args)
    else:
        events.append((name,) + args)


# ---------------------------------------------------------------- storage

def _paths():
    home = os.path.expanduser("~")
    data = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local/share"),
                        APP)
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"), APP)
    return data, cache


def _session_file():
    return os.path.join(data_dir, "session.json")


def _save_session():
    path = _session_file()
    if not session:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    handle = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as out:
        json.dump({"session": session, "album": album}, out)
    os.replace(path + ".new", path)


def _sql(query, args=(), fetch=False):
    with _db_lock:
        cursor = _db.execute(query, args)
        rows = cursor.fetchall() if fetch else None
        _db.commit()
        return rows


def init(folder=None):
    """Open the storage; returns state()."""
    global data_dir, cache_dir, _db, session, album
    data_dir, cache_dir = (folder, os.path.join(folder, "cache")) if folder else _paths()
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    for name in os.listdir(cache_dir):          # left over from an interrupted upload
        if name.endswith(".tmp"):
            try:
                os.remove(os.path.join(cache_dir, name))
            except OSError:
                pass
    # the automatic backup (another process) uses the same database: wait for its writes
    _db = sqlite3.connect(os.path.join(data_dir, "uploads.db"), check_same_thread=False,
                          timeout=60)
    _db.execute("CREATE TABLE IF NOT EXISTS uploaded (path TEXT, size INTEGER, mtime INTEGER,"
                " hash TEXT, album INTEGER, file INTEGER, at INTEGER)")
    _db.execute("CREATE INDEX IF NOT EXISTS uploaded_path ON uploaded (path)")
    _db.execute("CREATE INDEX IF NOT EXISTS uploaded_hash ON uploaded (hash)")
    if "taken" not in [row[1] for row in _db.execute("PRAGMA table_info(uploaded)")]:
        _db.execute("ALTER TABLE uploaded ADD COLUMN taken INTEGER")   # from 0.2.0
    # files found empty or damaged: left alone until they change (size or time)
    _db.execute("CREATE TABLE IF NOT EXISTS broken (path TEXT PRIMARY KEY, size INTEGER,"
                " mtime INTEGER, reason TEXT)")
    # copies saved from the account: never uploaded back (unless they change)
    _db.execute("CREATE TABLE IF NOT EXISTS saved (path TEXT PRIMARY KEY, size INTEGER,"
                " mtime INTEGER, file INTEGER)")
    # content hashes of files on the phone (freeing up space compares them with Ente's)
    _db.execute("CREATE TABLE IF NOT EXISTS hashes (path TEXT PRIMARY KEY, size INTEGER,"
                " mtime INTEGER, hash TEXT)")
    fresh = not _db.execute("SELECT 1 FROM sqlite_master WHERE name='sources'").fetchall()
    _db.execute("CREATE TABLE IF NOT EXISTS sources (path TEXT PRIMARY KEY, enabled INTEGER,"
                " album INTEGER, album_name TEXT, added INTEGER)")
    if fresh:   # what "Upload new camera photos" covered before 0.3.0
        for index, folder in enumerate(_default_sources()):
            _db.execute("INSERT OR IGNORE INTO sources VALUES (?,1,0,'',?)", (folder, index))
    _db.commit()
    session, album = {}, {}
    try:
        with open(_session_file()) as source:
            kept = json.load(source)
        session, album = kept.get("session") or {}, kept.get("album") or {}
    except (OSError, ValueError):
        pass
    global _cloud
    _cloud = cloud.Cloud(data_dir, cache_dir, send)
    _cloud.api = _api
    threading.Thread(target=_cloud.trim_thumbs, daemon=True).start()
    return state()


def state():
    count = _sql("SELECT COUNT(*) FROM uploaded", fetch=True)[0][0] if _db else 0
    last = (_sql("SELECT MAX(at) FROM uploaded WHERE file != 0", fetch=True)[0][0] or 0
            if _db else 0)
    on = _sql("SELECT COUNT(*) FROM sources WHERE enabled=1", fetch=True)[0][0] if _db else 0
    return {"signedIn": bool(session.get("token")), "email": session.get("email", ""),
            "server": session.get("server", ente.DEFAULT_SERVER),
            "albumId": album.get("id", 0), "albumName": album.get("name", ""),
            "uploaded": count, "lastUpload": last, "sources": on,
            "sodium": sodium.available(),
            "error": last_error, "auto": auto_settings() if data_dir else dict(AUTO_DEFAULTS),
            "autoLast": auto_status() if data_dir else {}}


# ---------------------------------------------------------------- upload sources

def _home():
    return os.path.expanduser("~")


def _default_sources():
    return [os.path.join(_home(), "Pictures", "Camera"), os.path.join(_home(), "Videos", "Camera")]


def _card_roots():
    """Mounted memory cards (Sailfish mounts them under /run/media/<user>/<label>)."""
    try:
        import getpass
        base = os.path.join("/run/media", getpass.getuser())
        return sorted(os.path.join(base, n) for n in os.listdir(base)
                      if os.path.isdir(os.path.join(base, n)))
    except (OSError, KeyError, ImportError):
        return []


def label(path):
    """A short name for a folder: home and memory cards spelled the way people know them."""
    home = _home()
    android = os.path.join(home, "android_storage")
    for card in _card_roots():
        if path == card or path.startswith(card + "/"):
            rest = path[len(card) + 1:]
            return "SD card" + (": " + rest if rest else "")
    if path == android or path.startswith(android + "/"):
        rest = path[len(android) + 1:]
        return "Android" + (": " + rest if rest else "")
    if path == home:
        return "Home"
    if path.startswith(home + "/"):
        return path[len(home) + 1:]
    return path


def _media(folder, videos=True):
    """Photos (and videos) in a folder and its subfolders; hidden ones are left out."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            kind = ente.file_type(name)
            if name.startswith(".") or kind is None or (kind == ente.VIDEO and not videos):
                continue
            yield os.path.join(root, name)


def _source_rows():
    return [{"path": p, "on": bool(e), "albumId": a or 0, "albumName": n or ""}
            for p, e, a, n in _sql("SELECT path, enabled, album, album_name FROM sources"
                                   " ORDER BY added, path", fetch=True)]


def _target(source):
    """(album id, album name) a source uploads to: its own, else the main album."""
    if source["albumId"]:
        return source["albumId"], source["albumName"]
    return album.get("id", 0), album.get("name", "")


def sources(videos=True):
    """The upload sources: [{path, label, on, albumId, albumName, own, exists, total, fresh}]
    own = it has an album of its own; fresh = files not yet uploaded to its album."""
    listed = []
    for source in _source_rows():
        target, name = _target(source)
        total = fresh = broken = 0
        exists = os.path.isdir(source["path"])
        if exists:
            for path in _media(source["path"], videos):
                total += 1
                try:
                    info = os.stat(path)
                except OSError:
                    continue
                if _broken(path, info):
                    broken += 1
                elif not target or not _known(path, info, target):
                    fresh += 1
        listed.append(dict(source, albumId=target, albumName=name,
                           own=bool(source["albumId"]), label=label(source["path"]),
                           exists=exists, total=total, fresh=fresh, broken=broken))
    return listed


def _candidates():
    home = _home()
    android = os.path.join(home, "android_storage")
    found = _default_sources() + [
        os.path.join(home, "Pictures", "Screenshots"), os.path.join(home, "Pictures"),
        os.path.join(home, "Downloads"), os.path.join(android, "DCIM", "Camera"),
        os.path.join(android, "Pictures"), os.path.join(android, "Download"),
        os.path.join(android, "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images"),
        os.path.join(android, "Android/media/org.telegram.messenger/Telegram/Telegram Images")]
    for card in _card_roots():
        found += [os.path.join(card, "DCIM"), os.path.join(card, "Pictures")]
    return found


def suggestions(videos=True):
    """Folders with photos that are not sources yet: [{path, label, total}]."""
    have = set(s["path"] for s in _source_rows())
    listed = []
    for path in _candidates():
        if path in have or not os.path.isdir(path):
            continue
        total = 0
        for _ in _media(path, videos):
            total += 1
            if total >= 9999:
                break
        if total:
            listed.append({"path": path, "label": label(path), "total": total})
    return listed


def folders(path=""):
    """One step of the folder browser. "" = the starting points (home, cards, Android).
    {path, label, parent, here (photos directly in it), dirs: [{path, label, name}], error}"""
    if not path:
        roots = [_home()] + _card_roots()
        android = os.path.join(_home(), "android_storage")
        if os.path.isdir(android):
            roots.append(android)
        return {"path": "", "label": "", "parent": "", "here": 0, "error": "",
                "dirs": [{"path": r, "label": label(r), "name": label(r)} for r in roots]}
    path = os.path.normpath(os.path.expanduser(path))
    roots = [_home(), os.path.join(_home(), "android_storage")] + _card_roots()
    parent = "" if path in roots else os.path.dirname(path)
    dirs, here = [], 0
    try:
        for name in sorted(os.listdir(path), key=str.lower):
            full = os.path.join(path, name)
            if name.startswith("."):
                continue
            if os.path.isdir(full):
                dirs.append({"path": full, "label": label(full), "name": name})
            elif ente.file_type(name) is not None:
                here += 1
    except OSError as err:
        # most likely the app's sandbox keeps it out (it sees only some of the phone)
        return {"path": path, "label": label(path), "parent": parent, "here": 0, "dirs": [],
                "error": "Cannot open this folder (%s)" % (err.strerror or err)}
    return {"path": path, "label": label(path), "parent": parent, "here": here, "dirs": dirs,
            "error": ""}


def add_source(path):
    path = os.path.normpath(os.path.expanduser(str(path)))
    if not os.path.isdir(path):
        return state()
    added = _sql("SELECT COALESCE(MAX(added), 0) + 1 FROM sources", fetch=True)[0][0]
    _sql("INSERT OR IGNORE INTO sources VALUES (?,1,0,'',?)", (path, added))
    _sql("UPDATE sources SET enabled=1 WHERE path=?", (path,))
    return state()


def remove_source(path):
    _sql("DELETE FROM sources WHERE path=?", (str(path),))
    return state()


def set_source(path, on):
    _sql("UPDATE sources SET enabled=? WHERE path=?", (1 if on else 0, str(path)))
    return state()


def set_source_album(path, album_id, name):
    """album_id 0 = the main album."""
    _sql("UPDATE sources SET album=?, album_name=? WHERE path=?",
         (int(album_id or 0), name if album_id else "", str(path)))
    return state()


def upload_sources(videos=True, only=""):
    """Queue everything new from the switched-on sources (or the one source `only`), each
    into its album. {queued, noAlbum: [labels of sources without an album]}"""
    queued, no_album, seen = 0, [], set()
    for source in _source_rows():
        if (only and source["path"] != only) or (not only and not source["on"]):
            continue
        target, name = _target(source)
        if not target:
            no_album.append(label(source["path"]))
            continue
        found = []
        for path in _media(source["path"], videos):
            if (path, target) in seen:
                continue
            seen.add((path, target))
            if _wanted(path, target):
                found.append(path)
        if found:
            upload(found, target, name)
            queued += len(found)
    return {"queued": queued, "noAlbum": no_album}


def _api():
    return ente.Api(session.get("server"), session.get("token", ""))


def _master():
    return ente.unb64(session.get("masterKey"))


def _keypair():
    """(public, secret) key of the account; None for sessions from before 0.6.0, which did
    not keep it (albums shared with the account need it)."""
    if not session.get("secretKey"):
        return None
    return ente.unb64(session["publicKey"]), ente.unb64(session["secretKey"])


# ---------------------------------------------------------------- worker

def _run():
    global last_error
    while True:
        job, on_error = _jobs.get()
        try:
            job()
        except ente.Unauthorized as err:
            last_error = str(err)
            _sign_out()
            on_error(str(err))
        except EnteError as err:
            last_error = str(err)
            on_error(str(err))
        except Exception as err:               # never let the worker die
            last_error = "%s: %s" % (type(err).__name__, err)
            on_error(last_error)
        if _jobs.empty():
            send("state", state())
        _jobs.task_done()


def _post(job, on_error):
    global _worker
    if _worker is None:
        _worker = threading.Thread(target=_run, daemon=True)
        _worker.start()
    _jobs.put((job, on_error))
    return True


def wait():
    """Tests: until the queue is empty."""
    _jobs.join()


# ---------------------------------------------------------------- signing in

def _login_error(message):
    send("login", "error", message)


def send_code(server, email):
    """Step 1: the server mails a code to the address."""
    # Ente keeps addresses in lower case; the keyboard likes to capitalise the first letter
    email = (email or "").strip().lower()

    def job():
        if "@" not in email:
            raise EnteError("Enter the account's e-mail address")
        api = ente.Api(server)
        try:
            ente.send_code(api, email)
        except EnteError as err:
            if "not registered" in str(err).lower():
                raise EnteError("%s has no account for \"%s\". Check the address for typing "
                                "mistakes; it must be the one you sign in to Ente with."
                                % (api.server.split("://")[-1], email))
            raise
        _pending.clear()
        _pending.update(api=api, email=email)
        send("login", "code", "")
    return _post(job, _login_error)


def _after_factor(answer):
    factor = ente.second_factor(answer)
    if factor is None:
        _pending["answer"] = answer
        send("login", "password", "")
    elif factor[0] == "totp":
        _pending["totp"] = factor[1]
        send("login", "totp", "")
    else:
        raise EnteError("This account signs in with a passkey, which this app cannot do. "
                        "Add an authenticator app as second factor, or switch the passkey off.")


def verify_code(code):
    """Step 2: the code from the mail."""
    def job():
        if "api" not in _pending:
            raise EnteError("Ask for a code first")
        _after_factor(ente.verify_code(_pending["api"], _pending["email"], code))
    return _post(job, _login_error)


def verify_totp(code):
    """Step 2b: the authenticator app's code, for accounts with two-factor sign-in."""
    def job():
        if "totp" not in _pending:
            raise EnteError("Ask for a code first")
        _after_factor(ente.verify_totp(_pending["api"], _pending["totp"], code))
    return _post(job, _login_error)


def unlock(password):
    """Step 3: the password opens the account's keys; it is not kept."""
    def job():
        global last_error
        answer = _pending.get("answer")
        if not answer:
            raise EnteError("Ask for a code first")
        master, token, keypair = ente.unlock(answer, password)
        api = _pending["api"]
        api.token = token
        try:
            ente.albums(api, master, answer.get("id"))
        except ente.Unauthorized:
            # the token spelt without its padding, in case the server wants it so
            api.token = token.rstrip("=")
            ente.albums(api, master, answer.get("id"))
        session.clear()
        session.update(server=api.server, email=_pending["email"], userId=answer.get("id"),
                       token=api.token, masterKey=ente.b64(master),
                       publicKey=ente.b64(keypair[0]), secretKey=ente.b64(keypair[1]))
        _cloud.reset()               # nothing of an account signed in before stays
        album.clear()
        _album_keys.clear()
        _pending.clear()
        last_error = ""
        _save_session()
        send("login", "done", "")
    return _post(job, _login_error)


def _sign_out():
    session.clear()
    album.clear()
    _album_keys.clear()
    _pending.clear()
    _save_session()
    if _cloud:
        _cloud.reset()


def sign_out():
    """Forget the session on the phone and end it on the server."""
    api = _api() if session.get("token") else None
    cancel()
    _sign_out()
    if api:
        def job():
            api.call("POST", "/users/logout")
        _post(job, lambda message: None)
    return state()


# ---------------------------------------------------------------- albums

def _load_albums():
    found = ente.albums(_api(), _master(), session.get("userId"))
    _album_keys.clear()
    for item in found:
        _album_keys[item["id"]] = item["key"]
    return [{"id": a["id"], "name": a["name"], "type": a["type"]} for a in found]


def albums():
    def job():
        send("albums", _load_albums(), "")
    return _post(job, lambda message: send("albums", [], message))


def create_album(name, make_main=True):
    """albums(list, "", new album's id) when done; make_main: upload there from now on."""
    name = (name or "").strip()

    def job():
        if not name:
            raise EnteError("The album needs a name")
        made = ente.create_album(_api(), _master(), name)
        _album_keys[made["id"]] = made["key"]
        if make_main:
            set_album(made["id"], name)
        send("albums", _load_albums(), "", made["id"])
    return _post(job, lambda message: send("albums", [], message))


def set_album(album_id, name):
    album.clear()
    album.update(id=int(album_id), name=name)
    _save_session()
    return state()


# ---------------------------------------------------------------- uploading

def _progress():
    send("upload", dict(_up))


def _known(path, info, album_id):
    return bool(_sql("SELECT 1 FROM uploaded WHERE path=? AND size=? AND mtime=? AND album=?",
                     (path, info.st_size, int(info.st_mtime), album_id), fetch=True))


def _broken(path, info):
    """The reason, when this very file (same size and time) was found unusable before."""
    rows = _sql("SELECT reason FROM broken WHERE path=? AND size=? AND mtime=?",
                (path, info.st_size, int(info.st_mtime)), fetch=True)
    return rows[0][0] if rows else ""


def _mark_broken(path, info, reason):
    _sql("INSERT OR REPLACE INTO broken VALUES (?,?,?,?)",
         (path, info.st_size, int(info.st_mtime), reason))


def _wanted(path, album_id):
    """True for a file to upload: not in the album yet, and not known to be unusable."""
    try:
        info = os.stat(path)
    except OSError:
        return False
    return (not _known(path, info, album_id) and not _broken(path, info)
            and not _saved_copy(path, info))


def _saved_copy(path, info):
    return bool(_sql("SELECT 1 FROM saved WHERE path=? AND size=? AND mtime=?",
                     (path, info.st_size, int(info.st_mtime)), fetch=True))


def _remember(path, info, digest, file_id, taken, album_id):
    _sql("INSERT INTO uploaded (path, size, mtime, hash, album, file, at, taken)"
         " VALUES (?,?,?,?,?,?,?,?)",
         (path, info.st_size, int(info.st_mtime), digest, album_id, file_id,
          int(time.time()), int(taken)))


def _taken(path, stored=None, mtime=None):
    """Capture time in epoch seconds: kept, from EXIF, else the file's time; 0 = unknown."""
    if stored:
        return int(stored)
    found = ente.taken_time(path)
    if found:
        return int(found)
    if mtime:
        return int(mtime)
    try:
        return int(os.stat(path).st_mtime)
    except OSError:
        return 0


def _entry(path, status, detail="", taken=0):
    return {"name": os.path.basename(path), "path": path, "status": status,
            "detail": detail, "taken": taken,
            "video": ente.file_type(path) == ente.VIDEO}


def history():
    """Every file uploaded from this phone, newest capture first:
    [{name, path, taken, at, video, status: "done"}]. Capture times missing from older
    versions are read once and kept."""
    rows = _sql("SELECT rowid, path, taken, mtime, at FROM uploaded", fetch=True)
    found, fixed = [], []
    for rowid, path, taken, mtime, at in rows:
        if not taken:
            taken = _taken(path, None, mtime)
            fixed.append((taken, rowid))
        entry = _entry(path, "done", "", taken)
        entry["at"] = at or 0
        found.append(entry)
    if fixed:
        with _db_lock:
            _db.executemany("UPDATE uploaded SET taken=? WHERE rowid=?", fixed)
            _db.commit()
    found.sort(key=lambda e: (-e["taken"], e["name"]))
    return found


def _check():
    if _cancel.is_set():
        raise ente.Cancelled("Stopped")


def _upload_one(path, where):
    """where: {id, name} of the album, or {} for the main album as it is now."""
    name = os.path.basename(path)
    _up.update(name=name, stage="encrypt", fraction=0.0)
    _progress()
    try:
        album_id = where.get("id") or album.get("id")
        if not album_id:
            raise EnteError("Choose an album first")
        info = os.stat(path)
        if _known(path, info, album_id):
            _up["skipped"] += 1
            kept = _sql("SELECT taken FROM uploaded WHERE path=? AND taken IS NOT NULL",
                        (path,), fetch=True)
            send("result", _entry(path, "skipped", "Already uploaded",
                                  _taken(path, kept[0][0] if kept else None, info.st_mtime)))
            return
        reason = _broken(path, info)
        if reason:
            raise ente.Unusable(reason)
        if album_id not in _album_keys:
            _load_albums()
        if album_id not in _album_keys:
            raise EnteError("The album \"%s\" no longer exists; choose another"
                            % (where.get("name") or album.get("name") or album_id))
        target = {"id": album_id, "key": _album_keys[album_id]}
        shown = [0.0]

        def progress(stage, done, total):
            now = time.time()
            if stage != _up["stage"] or now - shown[0] > 0.3 or done == total:
                shown[0] = now
                _up.update(stage=stage, fraction=(done / total if total else 0.0))
                _progress()

        def known_hash(digest):
            if _sql("SELECT 1 FROM uploaded WHERE hash=? AND album=?",
                    (digest, target["id"]), fetch=True):
                return "Same photo already uploaded"
            there = _cloud.album_with_hash(digest)
            return "Already in Ente (album \"%s\")" % there if there else ""

        result = ente.upload(_api(), target, path, cache_dir, progress, _check, known_hash)
        taken = int(result.get("taken") or info.st_mtime)
        _remember(path, info, result["hash"], result.get("id", 0), taken, album_id)
        if result.get("duplicate"):
            _up["skipped"] += 1
            send("result", _entry(path, "skipped",
                                  result.get("reason") or "Same photo already uploaded", taken))
        else:
            _up["done"] += 1
            send("result", _entry(path, "done", "", taken))
    except ente.Cancelled:
        raise
    except ente.Unauthorized:
        raise
    except ente.Unusable as err:
        _up["skipped"] += 1
        _mark_broken(path, info, str(err))
        send("result", _entry(path, "skipped", str(err), int(info.st_mtime)))
    except (EnteError, OSError) as err:
        _up["failed"] += 1
        send("result", _entry(path, "failed", str(err), _taken(path)))


def upload(paths, album_id=0, album_name=""):
    """Queue files for an album (0 = the main album). Files already uploaded there from this
    phone are skipped."""
    paths = [str(p) for p in (paths or [])]
    where = {"id": int(album_id), "name": album_name} if album_id else {}
    if not paths:
        return False
    if not _up["active"]:
        _cancel.clear()
        _up.update(active=True, total=0, done=0, skipped=0, failed=0, name="", stage="",
                   fraction=0.0)
        _batch[0] += 1
        batch_error[0] = ""
    _up["total"] += len(paths)
    _progress()
    batch = _batch[0]
    _batch[1] += 1

    def finish(message=""):
        _batch[1] -= 1
        if message:
            batch_error[0] = message
        if _batch[1] <= 0 or message:
            _up.update(active=False, name="", stage="", fraction=0.0)
            if message:
                _up["error"] = message
            _progress()
            _up.pop("error", None)

    def job():
        # a batch stopped by cancel() stays stopped, also when a new one has begun since
        if batch == _batch[0] and not _cancel.is_set():
            lock = _upload_lock()
            try:
                _refresh_index()
                for path in paths:
                    _upload_one(path, where)
            except ente.Cancelled:
                pass
            finally:
                lock.close()                        # releases the lock
        finish()
    return _post(job, finish)


# Uploads from the app and from the automatic backup (another process) take turns.
# The app waits for a backup run; the backup run (UPLOAD_LOCK_WAIT False) gives way.
UPLOAD_LOCK_WAIT = True


def _upload_lock():
    import fcntl
    handle = open(os.path.join(data_dir, "upload.lock"), "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        if not UPLOAD_LOCK_WAIT:
            handle.close()
            raise EnteError("The app is uploading right now")
    _up.update(stage="wait", name="", fraction=0.0)
    _progress()
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


# ---------------------------------------------------------------- automatic backup

AUTO_DEFAULTS = {"on": False, "wifiOnly": True, "videos": True}


def _auto_file(name):
    return os.path.join(data_dir, name)


def _read_json(path, default):
    try:
        with open(path) as source:
            value = json.load(source)
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    with open(path + ".new", "w") as out:
        json.dump(value, out)
    os.replace(path + ".new", path)


def auto_settings():
    """{on, wifiOnly, videos} of the automatic backup (the timer reads them)."""
    return dict(AUTO_DEFAULTS, **_read_json(_auto_file("auto.json"), {}))


def set_auto(changes):
    """Change some of the automatic backup's settings; returns state()."""
    settings = auto_settings()
    for name in AUTO_DEFAULTS:
        if name in (changes or {}):
            settings[name] = bool(changes[name])
    _write_json(_auto_file("auto.json"), settings)
    return state()


def auto_status():
    """What the last automatic backup run did: {at, ran, reason, uploaded, skipped,
    failed, error}; {} before the first run."""
    return _read_json(_auto_file("auto-status.json"), {})


INDEX_FRESH = 60             # seconds an index sync counts as current for uploads
_index_checked = [0.0]


def _refresh_index():
    """Before uploading: bring the index of the account up to date, so photos already in
    Ente (from any device) are skipped. Only changes are read, except the very first time.
    A failure here does not stop the uploads; they are then checked against this phone's
    own record only."""
    if not session.get("token") or time.time() - _index_checked[0] < INDEX_FRESH:
        return
    _up.update(stage="index", name="", fraction=0.0)
    _progress()
    try:
        _cloud.sync(_master(), session.get("userId"), _keypair())
        _index_checked[0] = time.time()
    except ente.Unauthorized:
        raise
    except Exception:
        pass


def scan(folders, videos=True):
    """Queue every photo (and video) in the folders not yet in the main album.
    Returns how many were queued."""
    found = []
    for folder in folders or []:
        for path in _media(os.path.expanduser(str(folder)), videos):
            if _wanted(path, album.get("id")):
                found.append(path)
    if found:
        upload(found)
    return len(found)


def cancel():
    """Stop after the current block; files not yet finished stay for the next time."""
    if _up["active"]:
        _cancel.set()
    return True


# ---------------------------------------------------------------- browsing the account

def cloud_sync():
    """Bring the index of the account's photos up to date, in the background. Progress and
    the end come as cloud(status) events. False when not signed in."""
    if not session.get("token"):
        return False

    def run():
        global last_error
        try:
            _cloud.sync(_master(), session.get("userId"), _keypair())
        except ente.Unauthorized as err:
            last_error = str(err)
            _sign_out()
            send("state", state())
        except Exception as err:
            _cloud._report(syncing=False, error="%s: %s" % (type(err).__name__, err))
    threading.Thread(target=run, daemon=True).start()
    return True


def cloud_status():
    return dict(_cloud.status, **_cloud.counts())


def cloud_rows(album_id=0, columns=4):
    return _cloud.rows(album_id, columns)


def cloud_albums():
    return _cloud.albums()


def cloud_thumbs(ids):
    return _cloud.thumbs(ids)


def cloud_retry_thumbs():
    _cloud.retry_thumbs()
    return True


def cloud_details(file_id):
    return _cloud.details(file_id) or _cloud.trash_details(file_id)


def cloud_on_this_day(columns=4):
    return _cloud.on_this_day(columns)


def cloud_trash_rows(columns=4):
    return _cloud.trash_rows(columns)


def cloud_memories(columns=4, only=-1):
    """1 month, 3 months, half a year ago; this day in earlier years; the best of this
    month in earlier years (see Cloud.memories). only >= 0: that one memory."""
    return _cloud.memories(columns, only=int(only))


def cloud_memory_covers():
    """The memories as title pictures for the top of the timeline (Cloud.memory_covers)."""
    return _cloud.memory_covers()


def cloud_duplicates(columns=4):
    """Photos stored more than once in the account, grouped (the copy to keep first)."""
    return _cloud.duplicates(columns)


def cloud_trash_duplicates(ids):
    """Move chosen extra copies to Ente's trash; the copy that stays joins their albums."""
    ids = [int(i) for i in ids or []]
    return _act("dedupe", lambda: _cloud.trash_duplicates(ids))


def cloud_favorites():
    """{id} of Ente's favorites album; 0 when there is none yet."""
    return {"id": _cloud.favorites_id()}


CLIP_MAX = 60 * 1024 * 1024  # videos larger than this do not play by themselves in the grid


def cloud_clip(file_id):
    """A big video tile wants to play by itself: fetched (event full) only on Wi-Fi and up
    to CLIP_MAX. {ok, reason}"""
    import autobackup
    info = _cloud.details(file_id)
    if not info or not info.get("video"):
        return {"ok": False, "reason": "no video"}
    if info.get("size", 0) > CLIP_MAX:
        return {"ok": False, "reason": "large"}
    if autobackup.on_wifi() is not True:
        return {"ok": False, "reason": "wifi"}
    _cloud.open_full(file_id)
    return {"ok": True, "reason": ""}


# ---------------------------------------------------------------- freeing up the phone

def _local_hash(path, info, check=None):
    """A local file's content hash, as Ente keeps it; remembered by path, size and time."""
    key = (path, info.st_size, int(info.st_mtime))
    rows = (_sql("SELECT hash FROM hashes WHERE path=? AND size=? AND mtime=?", key, fetch=True)
            or _sql("SELECT hash FROM uploaded WHERE path=? AND size=? AND mtime=?"
                    " AND hash != ''", key, fetch=True))
    if rows and rows[0][0]:
        return rows[0][0]
    digest = ente.file_hash(path, check)
    _sql("INSERT OR REPLACE INTO hashes VALUES (?,?,?,?)", key + (digest,))
    return digest


def _local_media():
    """Every photo and video in the upload sources (each once)."""
    seen = set()
    for source in _source_rows():
        if not os.path.isdir(source["path"]):
            continue
        for path in _media(source["path"], True):
            if path not in seen:
                seen.add(path)
                yield path


def cleanup_scan(days=30):
    """Find photos and videos in the upload sources, older than days, that are safely in
    Ente: a file with exactly the same content is in one of the user's own albums (not
    only in the trash or in an album someone shares). The index is brought up to date
    first. In the background: events cleanup(0, {progress, total}) while files are
    checked, then cleanup(0, {files: [{path, name, size, taken, album, video}], bytes,
    checked, notInEnte, error})."""
    _cancel_cleanup.clear()

    def work():
        _cloud.sync(_master(), session.get("userId"), _keypair())
        if _cloud.status.get("error"):
            raise EnteError(_cloud.status["error"])
        limit = time.time() - max(0, int(days)) * 86400
        paths = []
        for path in _local_media():
            try:
                info = os.stat(path)
            except OSError:
                continue
            if info.st_size > 0 and info.st_mtime <= limit:
                paths.append((path, info))
        found, missing, shown = [], 0, [0.0]

        def check():
            if _cancel_cleanup.is_set():
                raise ente.Cancelled("Stopped")
        for done, (path, info) in enumerate(paths):
            check()
            if time.time() - shown[0] > 0.3:
                shown[0] = time.time()
                send("cleanup", 0, {"progress": done, "total": len(paths)})
            try:
                there = _cloud.album_with_hash(_local_hash(path, info, check))
            except OSError:
                continue
            if not there:
                missing += 1
                continue
            found.append({"path": path, "name": os.path.basename(path), "size": info.st_size,
                          "taken": int(info.st_mtime), "album": there,
                          "video": ente.file_type(path) == ente.VIDEO})
        found.sort(key=lambda f: f["taken"])
        return {"files": found, "bytes": sum(f["size"] for f in found),
                "checked": len(paths), "notInEnte": missing}

    def safe():
        try:
            return work()
        except ente.Cancelled:
            return {"files": [], "bytes": 0, "checked": 0, "notInEnte": 0, "stopped": True}
    return _in_background("cleanup", 0, safe)


_cancel_cleanup = threading.Event()


def cleanup_cancel():
    _cancel_cleanup.set()
    return True


def cleanup_delete(paths):
    """Delete files from the phone that cleanup_scan found in Ente. Each one is checked
    again just before: unchanged since (size and time) and its content still in one of
    the user's own albums after a fresh update of the index; otherwise it stays. In the
    background: event cleanup(1, {done, freed, kept, error})."""
    paths = [str(p) for p in paths or []]

    def work():
        _cloud.sync(_master(), session.get("userId"), _keypair())
        if _cloud.status.get("error"):
            raise EnteError(_cloud.status["error"])
        done = freed = kept = 0
        for path in paths:
            try:
                info = os.stat(path)
                rows = _sql("SELECT hash FROM hashes WHERE path=? AND size=? AND mtime=?",
                            (path, info.st_size, int(info.st_mtime)), fetch=True)
                digest = rows[0][0] if rows else _local_hash(path, info)
                if not _cloud.album_with_hash(digest):
                    kept += 1
                    continue
                os.remove(path)
            except OSError:
                kept += 1
                continue
            _sql("DELETE FROM hashes WHERE path=?", (path,))
            done += 1
            freed += info.st_size
        return {"done": done, "freed": freed, "kept": kept}
    return _in_background("cleanup", 1, work)


def _act(what, work, sync_after=True):
    """An action on the account, in the background. Event acted(0, {what, done, skipped,
    error, ...}). The index is brought up to date afterwards (only changes are read)."""
    def run():
        result = dict(work() or {}, what=what)
        if sync_after:
            try:
                _cloud.sync(_master(), session.get("userId"), _keypair())
            except ente.Unauthorized:
                raise
            except Exception:
                pass
        return result
    return _in_background("acted", 0, run)


def cloud_add(ids, album_id):
    """Put photos into one of the user's albums too."""
    return _act("add", lambda: _cloud.add_to_album(ids, album_id))


def cloud_move(ids, from_album, to_album):
    """Move photos from one of the user's albums to another."""
    return _act("move", lambda: _cloud.move_to_album(ids, from_album, to_album))


def cloud_favorite(ids, on):
    """Favorite photos in Ente, or not any more."""
    return _act("favorite" if on else "unfavorite",
                lambda: _cloud.set_favorite(ids, bool(on), _master()))


def cloud_save_many(ids):
    """Save the originals of several photos to the phone (see cloud_save)."""
    def work():
        home = os.path.expanduser("~")
        done, folder = 0, ""
        for file_id in ids:
            paths = _cloud.save(int(file_id), os.path.join(home, SAVE_PICTURES),
                                os.path.join(home, SAVE_VIDEOS))
            for path in paths:
                info = os.stat(path)
                _sql("INSERT OR REPLACE INTO saved VALUES (?,?,?,?)",
                     (path, info.st_size, int(info.st_mtime), int(file_id)))
            folder = folder or label(os.path.dirname(paths[0]))
            done += 1
        return {"done": done, "skipped": 0, "folder": folder}
    return _act("save", work, sync_after=False)


def cloud_trash_sync():
    """Read Ente's trash anew. Event acted(0, {what: "trashsync", count, unreadable})."""
    return _act("trashsync", _cloud.sync_trash, sync_after=False)


def cloud_restore(ids):
    """Bring photos back from Ente's trash."""
    return _act("restore", lambda: _cloud.restore(ids, _master()))


def cloud_delete_forever(ids):
    """Delete photos from Ente's trash for good."""
    return _act("delete", lambda: _cloud.delete_forever(ids), sync_after=False)


def cloud_empty_trash():
    """Empty Ente's trash (up to what the app last read of it)."""
    return _act("empty", _cloud.empty_trash, sync_after=False)


def space():
    """Space left: {ente: {used, total, free, files, expires, family} or {error},
    phone: {free, total}} in bytes. The phone's is that of the home folder (photos and the
    app's files live there)."""
    result = {"ente": {}, "phone": {}}
    try:
        info = os.statvfs(_home())
        result["phone"] = {"free": info.f_bavail * info.f_frsize,
                           "total": info.f_blocks * info.f_frsize}
    except (OSError, AttributeError):
        pass
    if session.get("token"):
        try:
            result["ente"] = ente.storage(_api())
        except ente.Unauthorized as err:
            result["ente"] = {"error": str(err)}
        except EnteError as err:
            result["ente"] = {"error": str(err)}
    return result


def pending(videos=True):
    """How many files the switched-on sources have that are not backed up yet."""
    return sum(s["fresh"] for s in sources(videos) if s["on"] and s["exists"])


def cloud_open(file_id):
    return _cloud.open_full(file_id)


SAVE_PICTURES = os.path.join("Pictures", "Ente")
SAVE_VIDEOS = os.path.join("Videos", "Ente")


def _in_background(event, file_id, work):
    """Run work() in a thread; event(file_id, {...result, error})."""
    def run():
        global last_error
        try:
            result = work() or {}
            result.setdefault("error", "")
        except ente.Unauthorized as err:
            last_error = str(err)
            _sign_out()
            send("state", state())
            result = {"error": str(err)}
        except EnteError as err:
            result = {"error": str(err)}
        except Exception as err:
            result = {"error": "%s: %s" % (type(err).__name__, err)}
        send(event, file_id, result)
    threading.Thread(target=run, daemon=True).start()
    return True


def cloud_save(file_id, share=False):
    """Save the original into Pictures/Ente (videos: Videos/Ente). Event saved(id, {paths,
    folder, share, error}); share: the page shares the first path next."""
    def work():
        home = os.path.expanduser("~")
        paths = _cloud.save(file_id, os.path.join(home, SAVE_PICTURES),
                            os.path.join(home, SAVE_VIDEOS))
        for path in paths:
            info = os.stat(path)
            _sql("INSERT OR REPLACE INTO saved VALUES (?,?,?,?)",
                 (path, info.st_size, int(info.st_mtime), int(file_id)))
        return {"paths": paths, "folder": label(os.path.dirname(paths[0])),
                "share": bool(share)}
    return _in_background("saved", file_id, work)


def cloud_trash(file_id):
    """Move to Ente's trash. Event trashed(id, {error})."""
    def work():
        _cloud.trash(file_id)
        return {}
    return _in_background("trashed", file_id, work)


def cloud_trash_many(ids):
    """Move several to Ente's trash. Event trashed(0, {done, skipped, error}); when it
    failed part way, done counts the ones that went."""
    ids = [int(i) for i in ids or []]

    def work():
        return _cloud.trash_many(ids)
    return _in_background("trashed", 0, work)


def forget_uploads():
    """Forget which files were uploaded (they would be sent again) and which were broken."""
    _sql("DELETE FROM uploaded")
    _sql("DELETE FROM broken")      # saved copies stay known: they are in Ente already
    return state()
