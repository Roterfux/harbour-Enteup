# Enteup - SPDX-License-Identifier: GPL-3.0-or-later
"""Ente Photos: sign in, list and create albums, encrypt and upload files.

Follows what Ente's own clients send (github.com/ente-io/ente, server "museum"):
  sign in   POST /users/ott, /users/verify-email (+ /users/two-factor/verify), then the
            password unlocks the account's keys on the phone
  albums    GET /collections/v2, POST /collections
  upload    POST /files/upload-url (length + MD5), PUT to the storage address, POST /files
Everything is encrypted here with libsodium before it leaves the phone.
"""
import base64
import hashlib
import http.client
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import sodium
from sodium import SodiumError

DEFAULT_SERVER = "https://api.ente.io"
USER_AGENT = "Enteup/0.1 (uploader for Sailfish OS)"
TIMEOUT = 40
CHUNK = 4 * 1024 * 1024        # Ente's stream encryption chunk; must not change
MAX_FILE = 10 * 1024 ** 3
THUMB_SIDE = 720
THUMB_BYTES = 100 * 1024
IMAGE, VIDEO = 0, 1
IMAGE_TYPES = ("jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff",
               "dng", "avif")
VIDEO_TYPES = ("mp4", "mov", "m4v", "mkv", "webm", "3gp", "avi")


class EnteError(Exception):
    pass


class Unauthorized(EnteError):
    """The server no longer accepts the session."""


class Cancelled(EnteError):
    pass


class Unusable(EnteError):
    """The file is empty or damaged: it is skipped, not uploaded."""


# ---------------------------------------------------------------- is the file usable?

ISO_MEDIA = ("mp4", "mov", "m4v", "3gp")          # files made of ISO base media boxes
ISO_BOXES = (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"uuid", b"meta",
             b"pnot", b"moof", b"mfra", b"styp", b"sidx", b"pdin", b"udta", b"junk")
# Pictures the phone's Pillow cannot read, but Ente takes: only their header is checked
OTHER_IMAGES = {"heic": (b"ftyp",), "heif": (b"ftyp",), "avif": (b"ftyp",),
                "dng": (b"II*\x00", b"MM\x00*"), "tif": (b"II*\x00", b"MM\x00*"),
                "tiff": (b"II*\x00", b"MM\x00*")}


def check_media(path):
    """Raise Unusable when the file is empty or damaged; return quietly when it is fine."""
    size = os.path.getsize(path)
    if size == 0:
        raise Unusable("Empty file")
    with open(path, "rb") as source:
        head = source.read(4096)
    if not head.strip(b"\x00"):
        raise Unusable("Broken file: only zeros at the start")
    ext = os.path.splitext(path)[1][1:].lower()
    if file_type(path) == IMAGE:
        _check_image(path, ext, head)
    else:
        _check_video(path, ext, head, size)


def _check_image(path, ext, head):
    from PIL import Image
    try:
        opened = Image.open(path)
    except Exception as err:      # UnidentifiedImageError on newer Pillow, OSError before
        marks = OTHER_IMAGES.get(ext)
        if marks and any(head[:4] == m or head[4:8] == m for m in marks):
            return
        if "cannot identify" in str(err):
            raise Unusable("Broken file: not a picture")
        raise Unusable("Broken file: %s" % (err or type(err).__name__))
    try:
        with opened:
            if opened.format == "JPEG":   # decoding at 1/8 size still reads every byte
                opened.draft("RGB", (max(1, opened.size[0] // 8),
                                     max(1, opened.size[1] // 8)))
            opened.load()
    except Exception as err:
        text = str(err)
        if "truncated" in text:
            raise Unusable("Broken file: picture cut off")
        raise Unusable("Broken file: damaged picture (%s)" % (text or type(err).__name__))


def _check_video(path, ext, head, size):
    if ext in ISO_MEDIA:
        if head[4:8] not in ISO_BOXES:
            raise Unusable("Broken file: not a video")
        _check_boxes(path, size)
    elif ext in ("mkv", "webm"):
        if head[:4] != b"\x1a\x45\xdf\xa3":
            raise Unusable("Broken file: not a video")
    elif ext == "avi":
        if head[:4] != b"RIFF" or head[8:12] != b"AVI ":
            raise Unusable("Broken file: not a video")


def _check_boxes(path, size):
    """The top-level boxes must fill the file exactly, and there must be a "moov" (the
    index): a recording that stopped in the middle has neither."""
    found, offset = set(), 0
    with open(path, "rb") as source:
        while offset < size:
            source.seek(offset)
            header = source.read(16)
            if len(header) < 8:
                raise Unusable("Broken file: video cut off")
            length = int.from_bytes(header[:4], "big")
            kind = header[4:8]
            if length == 1:
                if len(header) < 16:
                    raise Unusable("Broken file: video cut off")
                length = int.from_bytes(header[8:16], "big")
            elif length == 0:                 # the last box runs to the end of the file
                length = size - offset
            if length < 8 or not kind.isascii():
                raise Unusable("Broken file: damaged video")
            if offset + length > size:
                raise Unusable("Broken file: video cut off")
            found.add(kind)
            offset += length
    if b"moov" not in found:
        raise Unusable("Broken file: video without index (recording not finished)")


def b64(data):
    return base64.b64encode(data).decode("ascii")


def unb64(text):
    try:
        return base64.b64decode(text or "")
    except (ValueError, TypeError):
        raise EnteError("The server sent data that cannot be read")


def file_type(path):
    """IMAGE, VIDEO or None for files Ente's gallery would not show."""
    ext = os.path.splitext(path)[1][1:].lower()
    if ext in IMAGE_TYPES:
        return IMAGE
    if ext in VIDEO_TYPES:
        return VIDEO
    return None


# ---------------------------------------------------------------- server calls

class Api:
    def __init__(self, server=DEFAULT_SERVER, token=""):
        self.server = normalise_server(server)
        self.token = token

    def call(self, method, path, body=None, query=None):
        url = self.server + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["X-Auth-Token"] = self.token
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
                raw = reply.read()
        except urllib.error.HTTPError as err:
            raw = err.read()
            err.close()
            raise _http_error(err.code, raw, bool(self.token))
        except urllib.error.URLError as err:
            raise EnteError("No connection (%s)" % err.reason)
        except (OSError, http.client.HTTPException) as err:
            raise EnteError("Connection failed (%s)" % (err or type(err).__name__))
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise EnteError("The server's answer cannot be read")


def normalise_server(text):
    url = (text or "").strip().rstrip("/") or DEFAULT_SERVER
    if "://" not in url:
        url = "https://" + url
    if not url.lower().startswith(("https://", "http://")) or " " in url:
        raise EnteError("Not a server address: %s" % text)
    return url


def _http_error(code, raw, signed_in):
    detail = ""
    try:
        answer = json.loads(raw.decode("utf-8"))
        detail = answer.get("message") or answer.get("error") or answer.get("code") or ""
    except (ValueError, AttributeError):
        pass
    if code == 401 and signed_in:
        return Unauthorized("The session has ended; sign in again")
    known = {401: "Wrong or expired code", 402: "The subscription has ended",
             404: "Not found on the server", 410: "The code has expired; ask for a new one",
             426: "The storage is full", 429: "Too many attempts; try again later"}
    text = known.get(code, "The server answered %d" % code)
    return EnteError("%s (%s)" % (text, detail) if detail else text)


# ---------------------------------------------------------------- signing in

def send_code(api, email):
    api.call("POST", "/users/ott", {"email": email, "purpose": "login"})


def verify_code(api, email, code):
    """The answer carries either the keys or a second-factor session."""
    return api.call("POST", "/users/verify-email", {"email": email, "ott": code.strip()})


def verify_totp(api, session_id, code):
    return api.call("POST", "/users/two-factor/verify",
                    {"sessionID": session_id, "code": code.strip()})


def second_factor(answer):
    """("totp", session id), ("passkey", "") or None when the keys are there."""
    if answer.get("keyAttributes"):
        return None
    totp = answer.get("twoFactorSessionIDV2") or answer.get("twoFactorSessionID")
    if totp:
        return "totp", totp
    if answer.get("passkeySessionID"):
        return "passkey", ""
    raise EnteError("The server sent no keys for this account")


def unlock(answer, password):
    """(master key, token, (public key, secret key)) from a sign-in answer and the account's
    password. The key pair opens albums others share with the account."""
    keys = answer.get("keyAttributes") or {}
    try:
        kek = sodium.pwhash(password, unb64(keys["kekSalt"]), keys["opsLimit"],
                            keys["memLimit"])
        try:
            master = sodium.secretbox_open(unb64(keys["encryptedKey"]),
                                           unb64(keys["keyDecryptionNonce"]), kek)
        except SodiumError:
            raise EnteError("Wrong password")
        secret = sodium.secretbox_open(unb64(keys["encryptedSecretKey"]),
                                       unb64(keys["secretKeyDecryptionNonce"]), master)
        keypair = (unb64(keys["publicKey"]), secret)
        if answer.get("encryptedToken"):
            raw = sodium.seal_open(unb64(answer["encryptedToken"]), keypair[0], secret)
            token = base64.urlsafe_b64encode(raw).decode("ascii")
        elif answer.get("token"):
            token = answer["token"]
        else:
            raise EnteError("The server sent no session")
    except KeyError as err:
        raise EnteError("The account's keys are incomplete (%s)" % err)
    except SodiumError as err:
        raise EnteError(str(err))
    return master, token, keypair


# ---------------------------------------------------------------- storage

def storage(api):
    """The account's storage: {used, total, free, files, expires, family} in bytes (expires:
    epoch seconds of the plan's end, 0 when unknown). In a family plan the family's storage
    and the members' usage together count, as in Ente's apps."""
    details = api.call("GET", "/users/details/v2")
    plan = details.get("subscription") or {}
    bonus = int(details.get("storageBonus") or 0)
    used = int(details.get("usage") or 0)
    total = int(plan.get("storage") or 0) + bonus
    family = details.get("familyData") or {}
    members = family.get("members") or []
    if members and family.get("storage"):
        used = sum(int(m.get("usage") or 0) for m in members)
        total = int(family["storage"]) + bonus
    return {"used": used, "total": total, "free": max(0, total - used),
            "files": int(details.get("fileCount") or 0),
            "expires": int(plan.get("expiryTime") or 0) // 1000000,
            "family": bool(members)}


# ---------------------------------------------------------------- albums

VISIBLE, ARCHIVED, HIDDEN = 0, 1, 2          # "visibility" in Ente's private magic metadata
SUBTYPE_HIDDEN = 1                           # the album that holds hidden photos


def magic(blob, key):
    """Ente's magic metadata ({data, header}) decrypted with the key it belongs to; {} when
    there is none or it cannot be read."""
    if not blob or not blob.get("data"):
        return {}
    try:
        value = json.loads(sodium.blob_decrypt(unb64(blob["data"]), unb64(blob.get("header")),
                                               key))
        return value if isinstance(value, dict) else {}
    except (SodiumError, EnteError, ValueError):
        return {}


def all_collections(api, master, user_id, keypair=None):
    """(albums, locked): the user's own albums and the albums others share with the user,
    each {id, name, type, key, updated, visibility, owner}; owner is "" for the user's own,
    else the sharer's address. visibility is HIDDEN for Ente's hidden album and albums hidden
    in Ente, ARCHIVED for archived ones. A shared album's key is sealed to the user's public
    key: without keypair (public, secret) it cannot be opened and only counts in locked."""
    answer = api.call("GET", "/collections/v2", query={"sinceTime": 0})
    found, locked = [], 0
    for item in answer.get("collections") or []:
        owner = item.get("owner") or {}
        if item.get("isDeleted"):
            continue
        mine = owner.get("id") == user_id
        try:
            if mine:
                key = sodium.secretbox_open(unb64(item.get("encryptedKey")),
                                            unb64(item.get("keyDecryptionNonce")), master)
            elif keypair:
                key = sodium.seal_open(unb64(item.get("encryptedKey")), keypair[0], keypair[1])
            else:
                locked += 1
                continue
            name = item.get("name") or ""
            if item.get("encryptedName"):
                name = sodium.secretbox_open(unb64(item["encryptedName"]),
                                             unb64(item.get("nameDecryptionNonce")),
                                             key).decode("utf-8", "replace")
        except (SodiumError, EnteError):
            continue
        # the owner's own settings; a sharee's (archived, hidden) are kept apart
        private = magic(item.get("magicMetadata" if mine else "sharedMagicMetadata"), key)
        visibility = private.get("visibility") or VISIBLE
        if private.get("subType") == SUBTYPE_HIDDEN:
            visibility = HIDDEN
        kind = item.get("type") or "album"
        if kind == "uncategorized":
            name = name or "Uncategorized"
        found.append({"id": item["id"], "name": name or "?", "type": kind, "key": key,
                      "updated": item.get("updationTime") or 0, "visibility": visibility,
                      "owner": "" if mine else (owner.get("email") or "someone")})
    found.sort(key=lambda a: -a["updated"])
    return found, locked


def own_collections(api, master, user_id):
    """The user's own albums (see all_collections)."""
    return [a for a in all_collections(api, master, user_id)[0] if not a["owner"]]


def albums(api, master, user_id):
    """The albums to upload into: the user's own, not hidden. Newest change first."""
    return [a for a in own_collections(api, master, user_id) if a["visibility"] != HIDDEN]


def create_album(api, master, name, kind="album"):
    """A new collection: an album, or Ente's "favorites" / "uncategorized" one."""
    key = sodium.random(sodium.KEY_BYTES)
    enc_key, key_nonce = sodium.secretbox(key, master)
    enc_name, name_nonce = sodium.secretbox(name.encode("utf-8"), key)
    answer = api.call("POST", "/collections", {
        "encryptedKey": b64(enc_key), "keyDecryptionNonce": b64(key_nonce),
        "encryptedName": b64(enc_name), "nameDecryptionNonce": b64(name_nonce),
        "type": kind, "attributes": {"version": 1}})
    made = answer.get("collection") or {}
    if not made.get("id"):
        raise EnteError("The server did not create the album")
    return {"id": made["id"], "name": name, "type": kind, "key": key,
            "updated": made.get("updationTime") or 0, "visibility": VISIBLE, "owner": ""}


# ---------------------------------------------------------------- one file

def describe(path, kind):
    """(thumbnail JPEG, static?, details) - details: taken (epoch s or None), lat, lon, w, h.
    Without a readable picture the thumbnail is plain black, as Ente's clients do."""
    from PIL import Image
    details = {"taken": None, "lat": None, "lon": None, "w": 0, "h": 0}
    picture = None
    if kind == IMAGE:
        try:
            from PIL import ImageOps
            with Image.open(path) as opened:
                details["w"], details["h"] = opened.size
                try:
                    _read_exif(opened.getexif(), details)
                except Exception:
                    pass
                opened.draft("RGB", (THUMB_SIDE * 2, THUMB_SIDE * 2))
                picture = ImageOps.exif_transpose(opened)
                picture.thumbnail((THUMB_SIDE, THUMB_SIDE))
                picture = picture.convert("RGB")
        except Exception:
            picture = None
    static = picture is None
    if static:
        picture = Image.new("RGB", (THUMB_SIDE // 2, THUMB_SIDE // 2))
    quality = 70
    while True:
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=quality)
        if out.tell() <= THUMB_BYTES or quality <= 30:
            return out.getvalue(), static, details
        quality -= 10


def taken_time(path):
    """When a photo was taken (epoch seconds) by its EXIF; None when it does not say."""
    if file_type(path) != IMAGE:
        return None
    details = {"taken": None, "lat": None, "lon": None}
    try:
        from PIL import Image
        with Image.open(path) as opened:
            _read_exif(opened.getexif(), details)
    except Exception:
        pass
    return details["taken"]


def _read_exif(exif, details):
    sub = exif.get_ifd(0x8769)
    stamp = sub.get(0x9003) or sub.get(0x9004) or exif.get(0x0132)
    if stamp:
        parts = time.strptime(str(stamp).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")
        offset = str(sub.get(0x9011) or "").strip("\x00 ")
        if len(offset) == 6 and offset[0] in "+-":
            import calendar
            shift = int(offset[1:3]) * 3600 + int(offset[4:6]) * 60
            details["taken"] = calendar.timegm(parts) - (shift if offset[0] == "+" else -shift)
        else:
            details["taken"] = time.mktime(parts[:8] + (-1,))
    gps = exif.get_ifd(0x8825)
    if gps.get(2) and gps.get(4):
        def degrees(value, ref, negative):
            d, m, s = (float(v) for v in value)
            result = d + m / 60.0 + s / 3600.0
            return -result if str(ref).strip("\x00 ").upper() == negative else result
        lat = degrees(gps[2], gps.get(1, "N"), "S")
        lon = degrees(gps[4], gps.get(3, "E"), "W")
        if (lat or lon) and abs(lat) <= 90 and abs(lon) <= 180:
            details["lat"], details["lon"] = lat, lon


def file_hash(path, check=None):
    """The content hash Ente's apps keep in a file's metadata: BLAKE2b-512, base64."""
    plain = hashlib.blake2b(digest_size=64)
    with open(path, "rb") as source:
        while True:
            if check:
                check()
            block = source.read(CHUNK)
            if not block:
                break
            plain.update(block)
    return b64(plain.digest())


def encrypt_file(path, key, target, check=None):
    """Encrypt path into the file target. (header, size, MD5 base64, plaintext hash base64)"""
    stream = sodium.StreamEncryptor(key)
    plain = hashlib.blake2b(digest_size=64)
    md5 = hashlib.md5()
    size = 0
    with open(path, "rb") as source, open(target, "wb") as out:
        chunk = source.read(CHUNK)
        while True:
            if check:
                check()
            following = source.read(CHUNK)
            plain.update(chunk)
            sealed = stream.push(chunk, not following)
            md5.update(sealed)
            out.write(sealed)
            size += len(sealed)
            if not following:
                break
            chunk = following
    return stream.header, size, b64(md5.digest()), b64(plain.digest())


def put(url, source, size, md5, progress=None, check=None):
    """PUT a file object (or bytes) to the storage address the server handed out."""
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        link = http.client.HTTPSConnection(parts.netloc, timeout=TIMEOUT)
    elif parts.scheme == "http":
        link = http.client.HTTPConnection(parts.netloc, timeout=TIMEOUT)
    else:
        raise EnteError("Unexpected upload address")
    try:
        link.putrequest("PUT", parts.path + ("?" + parts.query if parts.query else ""))
        link.putheader("Content-Length", str(size))
        link.putheader("Content-MD5", md5)
        link.putheader("User-Agent", USER_AGENT)
        link.endheaders()
        sent = 0
        while True:
            if check:
                check()
            block = source.read(256 * 1024)
            if not block:
                break
            link.send(block)
            sent += len(block)
            if progress:
                progress(sent, size)
        reply = link.getresponse()
        body = reply.read(2000)
        if reply.status not in (200, 201, 204):
            raise EnteError("The storage refused the upload (%d %s)"
                            % (reply.status, body[:200].decode("utf-8", "replace").strip()))
    except (OSError, http.client.HTTPException) as err:
        raise EnteError("Upload interrupted (%s)" % (err or type(err).__name__))
    finally:
        link.close()


def _upload_object(api, source, size, md5, progress=None, check=None):
    answer = api.call("POST", "/files/upload-url", {"contentLength": size, "contentMD5": md5})
    if not answer.get("url") or not answer.get("objectKey"):
        raise EnteError("The server gave no upload address")
    put(answer["url"], source, size, md5, progress, check)
    return answer["objectKey"]


def upload(api, album, path, work_dir, progress=None, check=None, known_hash=None):
    """Encrypt and upload one file into an album ({id, key}).
    progress(stage, done, total); check() may raise Cancelled.
    known_hash(hash) -> a reason (true) when that content is in Ente already: then nothing
    is encrypted or sent and the result is {"duplicate": True, "hash", "taken", "reason"}.
    Result: {"id", "hash", "size", "taken"} - taken: epoch seconds as told to Ente."""
    kind = file_type(path)
    if kind is None:
        raise EnteError("Not a photo or video")
    info = os.stat(path)
    check_media(path)
    if info.st_size > MAX_FILE:
        raise EnteError("The file is too large for Ente")
    key = sodium.random(sodium.KEY_BYTES)
    temp = os.path.join(work_dir, "upload-%d.tmp" % os.getpid())
    os.makedirs(work_dir, exist_ok=True)
    try:
        if progress:
            progress("encrypt", 0, info.st_size)
        if known_hash:                      # before encrypting: a duplicate costs one read
            digest = file_hash(path, check)
            reason = known_hash(digest)
            if reason:
                return {"duplicate": True, "hash": digest,
                        "taken": taken_time(path) or info.st_mtime,
                        "reason": reason if isinstance(reason, str) else ""}
        header, size, md5, digest = encrypt_file(path, key, temp, check)

        thumb, static, details = describe(path, kind)
        thumb_enc, thumb_header = sodium.blob_encrypt(thumb, key)
        taken = details["taken"] if details["taken"] else info.st_mtime
        metadata = {"title": os.path.basename(path), "fileType": kind,
                    "creationTime": int(taken * 1000000),
                    "modificationTime": int(info.st_mtime * 1000000), "hash": digest}
        if details["lat"] is not None:
            metadata["latitude"] = details["lat"]
            metadata["longitude"] = details["lon"]
        if static:
            metadata["hasStaticThumbnail"] = True
        meta_enc, meta_header = sodium.blob_encrypt(
            json.dumps(metadata, ensure_ascii=False).encode("utf-8"), key)

        thumb_key = _upload_object(api, thumb_enc, len(thumb_enc),
                                   b64(hashlib.md5(thumb_enc).digest()), None, check)
        with open(temp, "rb") as source:
            file_key = _upload_object(
                api, source, size, md5,
                (lambda done, total: progress("upload", done, total)) if progress else None,
                check)

        enc_key, key_nonce = sodium.secretbox(key, album["key"])
        body = {"collectionID": album["id"], "encryptedKey": b64(enc_key),
                "keyDecryptionNonce": b64(key_nonce),
                "file": {"objectKey": file_key, "decryptionHeader": b64(header),
                         "size": size},
                "thumbnail": {"objectKey": thumb_key,
                              "decryptionHeader": b64(thumb_header), "size": len(thumb_enc)},
                "metadata": {"encryptedData": b64(meta_enc),
                             "decryptionHeader": b64(meta_header)}}
        if details["w"] and details["h"]:
            public = {"w": details["w"], "h": details["h"]}
            pub_enc, pub_header = sodium.blob_encrypt(json.dumps(public).encode("utf-8"), key)
            body["pubMagicMetadata"] = {"version": 1, "count": len(public),
                                        "data": b64(pub_enc), "header": b64(pub_header)}
        answer = api.call("POST", "/files", body)
        if not answer.get("id"):
            raise EnteError("The server did not confirm the file")
        return {"id": answer["id"], "hash": digest, "size": size, "taken": taken}
    except SodiumError as err:
        raise EnteError(str(err))
    finally:
        try:
            os.remove(temp)
        except OSError:
            pass
