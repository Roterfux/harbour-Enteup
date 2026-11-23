"""Engine tests against a small stand-in for Ente's server (needs libsodium and Pillow).

    python3 -m unittest discover -s tests -v
"""
import base64
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

import backend   # noqa: E402
import cloud     # noqa: E402
import ente      # noqa: E402
import sodium    # noqa: E402
from ente import b64, unb64   # noqa: E402

PASSWORD = "correct horse"
OPS, MEM = 2, 64 * 1024 * 1024


class Account:
    """What the server keeps for one user, built the way Ente's clients build it."""

    def __init__(self):
        self.master = sodium.random(32)
        self.salt = sodium.random(16)
        kek = sodium.pwhash(PASSWORD, self.salt, OPS, MEM)
        enc_key, key_nonce = sodium.secretbox(self.master, kek)
        self.public, self.secret = sodium.box_keypair()
        enc_secret, secret_nonce = sodium.secretbox(self.secret, self.master)
        self.token_raw = sodium.random(32)
        self.token = base64.urlsafe_b64encode(self.token_raw).decode()
        self.attributes = {
            "kekSalt": b64(self.salt), "encryptedKey": b64(enc_key),
            "keyDecryptionNonce": b64(key_nonce), "publicKey": b64(self.public),
            "encryptedSecretKey": b64(enc_secret),
            "secretKeyDecryptionNonce": b64(secret_nonce), "memLimit": MEM, "opsLimit": OPS}
        self.totp = False
        self.collections = []
        self.objects = {}           # object key -> bytes
        self.expected = {}          # object key -> (length, md5)
        self.files = []
        self.calls = []
        self.clock = 1000           # Ente's updationTime: goes up with every change
        self.page = 1000            # diff entries per answer
        self.storage_headers = []   # the headers of every download from the storage
        self.shared_keys = {}       # albums shared with the user: id -> key
        self.trash_requests = []
        self.trash = {}             # file id -> Ente's trash record
        # as /users/details/v2 answers (the numbers of a 200 GB plan)
        self.details = {"usage": 47079753190, "storageBonus": 0, "fileCount": 10550,
                        "subscription": {"storage": 214748364800,
                                         "expiryTime": 1797856708000000},
                        "bonusData": {"storageBonuses": []}}
        self.album_requests = []
        self.empty_requests = []

    def tick(self, collection_id):
        self.clock += 1
        for stored in self.collections:
            if stored["id"] == collection_id:
                stored["updationTime"] = self.clock
        return self.clock

    def collection_key(self, collection_id):
        if collection_id in self.shared_keys:
            return self.shared_keys[collection_id]
        stored = [c for c in self.collections if c["id"] == collection_id][0]
        return sodium.secretbox_open(unb64(stored["encryptedKey"]),
                                     unb64(stored["keyDecryptionNonce"]), self.master)

    def add_shared(self, name, owner="friend@example.org", shared_magic=None):
        """An album another user shares with this one: its key sealed to our public key."""
        key = sodium.random(32)
        enc_name, nonce = sodium.secretbox(name.encode(), key)
        collection_id = 900 + len(self.shared_keys)
        self.shared_keys[collection_id] = key
        stored = {"id": collection_id, "owner": {"id": 9, "email": owner},
                  "encryptedKey": b64(sodium.seal(key, self.public)),
                  "encryptedName": b64(enc_name), "nameDecryptionNonce": b64(nonce),
                  "type": "album", "attributes": {}, "updationTime": 0}
        if shared_magic:
            blob, header = sodium.blob_encrypt(json.dumps(shared_magic).encode(), key)
            stored["sharedMagicMetadata"] = {"version": 1, "count": len(shared_magic),
                                             "data": b64(blob), "header": b64(header)}
        self.collections.append(stored)
        self.tick(collection_id)
        return collection_id

    def set_magic(self, collection_id, value):
        stored = [c for c in self.collections if c["id"] == collection_id][0]
        data, header = sodium.blob_encrypt(json.dumps(value).encode(),
                                           self.collection_key(collection_id))
        stored["magicMetadata"] = {"version": 1, "count": len(value), "data": b64(data),
                                   "header": b64(header)}
        self.tick(collection_id)

    def add_file(self, collection_id, data, title, kind=0, taken=1600000000, private=None,
                 public=None):
        """A file put into the account the way Ente's own apps do it."""
        from PIL import Image
        import io
        key = sodium.random(32)
        stream = sodium.StreamEncryptor(key)
        body = b""
        for start in range(0, max(len(data), 1), ente.CHUNK):
            body += stream.push(data[start:start + ente.CHUNK], start + ente.CHUNK >= len(data))
        out = io.BytesIO()
        Image.effect_noise((720, 540), 50).convert("RGB").save(out, "JPEG", quality=85)
        thumb, thumb_header = sodium.blob_encrypt(out.getvalue(), key)
        import hashlib
        meta = {"title": title, "fileType": kind, "creationTime": taken * 1000000,
                "modificationTime": taken * 1000000,
                "hash": b64(hashlib.blake2b(data, digest_size=64).digest())}
        meta_data, meta_header = sodium.blob_encrypt(json.dumps(meta).encode(), key)
        number = len(self.objects)
        self.objects["7/direct-%d" % number] = body
        self.objects["7/direct-%d-t" % number] = thumb
        enc_key, nonce = sodium.secretbox(key, self.collection_key(collection_id))
        entry = {"id": 5000 + number, "collectionID": collection_id, "ownerID": 7,
                 "encryptedKey": b64(enc_key), "keyDecryptionNonce": b64(nonce),
                 "file": {"objectKey": "7/direct-%d" % number,
                          "decryptionHeader": b64(stream.header)},
                 "thumbnail": {"objectKey": "7/direct-%d-t" % number,
                               "decryptionHeader": b64(thumb_header)},
                 "metadata": {"encryptedData": b64(meta_data),
                              "decryptionHeader": b64(meta_header)},
                 "info": {"fileSize": len(body), "thumbSize": len(thumb)},
                 "isDeleted": False, "updationTime": self.tick(collection_id)}
        for field, value in (("magicMetadata", private), ("pubMagicMetadata", public)):
            if value:
                blob, header = sodium.blob_encrypt(json.dumps(value).encode(), key)
                entry[field] = {"version": 1, "count": len(value), "data": b64(blob),
                                "header": b64(header)}
        self.files.append(entry)
        return entry["id"]

    def delete_file(self, file_id, collection_id):
        for entry in self.files:
            if entry["id"] == file_id and entry["collectionID"] == collection_id:
                entry.update(isDeleted=True, updationTime=self.tick(collection_id))

    def authorization(self):
        return {"id": 7, "keyAttributes": self.attributes,
                "encryptedToken": b64(sodium.seal(self.token_raw, self.public))}


class Handler(http.server.BaseHTTPRequestHandler):
    account = None

    def log_message(self, *args):
        pass

    def reply(self, code, body=None):
        data = json.dumps(body if body is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def authed(self):
        if self.headers.get("X-Auth-Token") != self.account.token:
            self.reply(401, {"error": "invalid token"})
            return False
        return True

    def do_GET(self):
        acc = self.account
        path = urllib.parse.urlsplit(self.path).path
        acc.calls.append("GET " + path)
        if path == "/collections/v2":
            return self.authed() and self.reply(200, {"collections": acc.collections})
        if path == "/files/upload-urls":
            return self.reply(410, {"message": "This upload API is no longer supported."})
        if path == "/users/details/v2":
            if not self.authed():
                return
            return self.reply(200, acc.details)
        if path == "/trash/v2/diff":
            if not self.authed():
                return
            since = int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                        ["sinceTime"][0])
            items = sorted((r for r in acc.trash.values() if r["updatedAt"] > since),
                           key=lambda r: r["updatedAt"])
            return self.reply(200, {"diff": items[:acc.page], "hasMore": len(items) > acc.page})
        if path == "/collections/v2/diff":
            if not self.authed():
                return
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            wanted, since = int(query["collectionID"][0]), int(query["sinceTime"][0])
            items = sorted((f for f in acc.files if f["collectionID"] == wanted
                            and f["updationTime"] > since), key=lambda f: f["updationTime"])
            return self.reply(200, {"diff": items[:acc.page],
                                    "hasMore": len(items) > acc.page})
        for prefix, part in (("/files/preview/", "thumbnail"), ("/files/download/", "file")):
            if path.startswith(prefix):
                if not self.authed():
                    return
                wanted = int(path[len(prefix):])
                found = [f for f in acc.files if f["id"] == wanted and not f["isDeleted"]]
                record = acc.trash.get(wanted)
                if not found and record and not record["isDeleted"]:
                    found = [record["file"]]        # the owner can still open trashed files
                if not found:
                    return self.reply(404)
                self.send_response(307)
                self.send_header("Location", "/s3/" + found[0][part]["objectKey"] + "?sig=d")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        if path.startswith("/s3/"):
            acc.storage_headers.append(dict(self.headers))
            data = acc.objects.get(path[len("/s3/"):])
            if data is None:
                return self.reply(404)
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.reply(404)

    def do_POST(self):
        acc = self.account
        path = urllib.parse.urlsplit(self.path).path
        acc.calls.append("POST " + path)
        data = json.loads(self.body() or b"{}")
        if path == "/users/ott":
            if data.get("purpose") != "login" or data.get("email") != "me@example.org":
                return self.reply(404, {"code": "USER_NOT_REGISTERED",
                                        "message": "User is not registered"})
            return self.reply(200)
        if path == "/users/verify-email":
            if data.get("ott") != "123456":
                return self.reply(401, {"message": "invalid ott"})
            if acc.totp:
                return self.reply(200, {"id": 7, "twoFactorSessionID": "tf-1",
                                        "passkeySessionID": ""})
            return self.reply(200, acc.authorization())
        if path == "/users/two-factor/verify":
            if data.get("sessionID") != "tf-1" or data.get("code") != "654321":
                return self.reply(401, {"message": "invalid code"})
            return self.reply(200, acc.authorization())
        if not self.authed():
            return
        if path == "/users/logout":
            return self.reply(200)
        if path == "/collections":
            made = dict(data, id=100 + len(acc.collections), owner={"id": 7, "email": "me"},
                        updationTime=len(acc.collections) + 1)
            acc.collections.append(made)
            return self.reply(200, {"collection": made})
        if path == "/files/trash":
            # as Ente's server: each file once, the user's own files and albums only, and
            # the file leaves all of its albums
            items = data.get("items") or []
            ids = [i["fileID"] for i in items]
            if len(ids) != len(set(ids)):
                return self.reply(400, {"message": "duplicate fileIDs"})
            for item in items:
                owned = [c for c in acc.collections if c["id"] == item["collectionID"]
                         and c["owner"]["id"] == 7]
                if not owned:
                    return self.reply(403, {"message": "user doesn't own collection"})
            acc.trash_requests.append(items)
            for item in items:
                kept = [e for e in acc.files if e["id"] == item["fileID"]
                        and e["collectionID"] == item["collectionID"] and not e["isDeleted"]]
                if kept:
                    acc.clock += 1
                    acc.trash[item["fileID"]] = {"file": dict(kept[0]), "isDeleted": False,
                                                 "isRestored": False, "deleteBy": 9 * 10 ** 15,
                                                 "updatedAt": acc.clock}
            for entry in acc.files:
                if entry["id"] in ids and not entry["isDeleted"]:
                    entry.update(isDeleted=True, updationTime=acc.tick(entry["collectionID"]))
            return self.reply(200)
        if path in ("/collections/add-files", "/collections/restore-files",
                    "/collections/move-files"):
            target = data.get("collectionID") or data.get("toCollectionID")
            if not [c for c in acc.collections if c["id"] == target and c["owner"]["id"] == 7]:
                return self.reply(403, {"message": "not your collection"})
            acc.album_requests.append((path, data))
            for item in data["files"]:
                if path == "/collections/restore-files":
                    record = acc.trash.get(item["id"])
                    if not record or record["isDeleted"] or record["isRestored"]:
                        return self.reply(400, {"message": "not in the trash"})
                    acc.clock += 1
                    record.update(isRestored=True, updatedAt=acc.clock)
                    source = record["file"]
                else:
                    source = [e for e in acc.files if e["id"] == item["id"]
                              and not e["isDeleted"]][0]
                if path == "/collections/move-files":
                    for entry in acc.files:
                        if (entry["id"] == item["id"] and not entry["isDeleted"]
                                and entry["collectionID"] == data["fromCollectionID"]):
                            entry.update(isDeleted=True,
                                         updationTime=acc.tick(data["fromCollectionID"]))
                copy = dict(source, collectionID=target, isDeleted=False,
                            encryptedKey=item["encryptedKey"],
                            keyDecryptionNonce=item["keyDecryptionNonce"])
                copy["updationTime"] = acc.tick(target)
                acc.files.append(copy)
            return self.reply(200)
        if path == "/trash/delete":
            for file_id in data["fileIDs"]:
                acc.clock += 1
                acc.trash[file_id].update(isDeleted=True, updatedAt=acc.clock)
            return self.reply(200)
        if path == "/trash/empty":
            acc.empty_requests.append(data)
            for record in acc.trash.values():
                if record["updatedAt"] <= data["lastUpdatedAt"] and not record["isRestored"]:
                    record.update(isDeleted=True)
            return self.reply(200)
        if path == "/files/upload-url":
            key = "7/object-%d" % len(acc.expected)
            acc.expected[key] = (data["contentLength"], data["contentMD5"])
            host = "http://127.0.0.1:%d" % self.server.server_address[1]
            return self.reply(200, {"objectKey": key, "url": host + "/s3/" + key + "?sig=x"})
        if path == "/files":
            for part in ("file", "thumbnail"):
                key = data[part]["objectKey"]
                if key not in acc.objects or len(acc.objects[key]) != data[part]["size"]:
                    return self.reply(400, {"message": "mismatch in %s size" % part})
            data["id"] = 1000 + len(acc.files)
            data.update(ownerID=7, isDeleted=False, updationTime=acc.tick(data["collectionID"]),
                        info={"fileSize": data["file"]["size"],
                              "thumbSize": data["thumbnail"]["size"]})
            acc.files.append(data)
            return self.reply(200, data)
        self.reply(404)

    def do_PUT(self):
        acc = self.account
        parts = urllib.parse.urlsplit(self.path)
        key = parts.path[len("/s3/"):]
        data = self.body()
        length, md5 = acc.expected.get(key, (None, None))
        if (parts.query != "sig=x" or length != len(data)
                or self.headers.get("Content-MD5") != md5
                or b64(hashlib.md5(data).digest()) != md5):
            return self.reply(403, {"message": "SignatureDoesNotMatch"})
        acc.objects[key] = data
        self.reply(200)


def decrypt_stream(data, header, key):
    stream = sodium.StreamDecryptor(header, key)
    step = ente.CHUNK + sodium.STREAM_OVERHEAD
    out, final = b"", False
    for start in range(0, len(data), step):
        plain, final = stream.pull(data[start:start + step])
        out += plain
    assert final
    return out


def make_jpeg(path, size=(1600, 1200), exif=True):
    from PIL import Image
    picture = Image.effect_noise(size, 60).convert("RGB")
    data = Image.Exif()
    if exif:
        sub = {0x9003: "2024:05:06 07:08:09", 0x9011: "+02:00"}
        data[0x8769] = sub
        data[0x8825] = {1: "N", 2: (48.0, 12.0, 30.0), 3: "W", 4: (16.0, 30.0, 0.0)}
    picture.save(path, "JPEG", quality=90, exif=data)


def box(kind, payload):
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def make_mp4(path, cut=0, index=True):
    """A file with the box layout of an MP4 (not playable, but whole); cut = bytes left off."""
    data = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")
    if index:
        data += box(b"moov", box(b"mvhd", bytes(100)))
    data += box(b"mdat", os.urandom(5000))
    with open(path, "wb") as out:
        out.write(data[:len(data) - cut])


class Base(unittest.TestCase):
    def setUp(self):
        self.account = Account()
        handler = type("H", (Handler,), {"account": self.account})
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.tmp = tempfile.TemporaryDirectory()
        self.photos = os.path.join(self.tmp.name, "Pictures")
        os.makedirs(self.photos)
        backend.events.clear()
        backend._album_keys.clear()
        backend._pending.clear()
        backend.last_error = ""
        backend._index_checked[0] = 0.0
        backend.init(os.path.join(self.tmp.name, "data"))

    def tearDown(self):
        backend.wait()
        backend._db.close()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def last(self, name):
        found = [e for e in backend.events if e[0] == name]
        self.assertTrue(found, "no %s event in %r" % (name, backend.events))
        return found[-1]

    def sign_in(self):
        backend.send_code(self.url, "me@example.org")
        backend.verify_code("123456")
        backend.unlock(PASSWORD)
        backend.wait()
        self.assertEqual(self.last("login")[1:], ("done", ""))


class Crypto(unittest.TestCase):
    def test_secretbox_and_blob(self):
        key = sodium.random(32)
        cipher, nonce = sodium.secretbox(b"hello", key)
        self.assertEqual(sodium.secretbox_open(cipher, nonce, key), b"hello")
        with self.assertRaises(sodium.SodiumError):
            sodium.secretbox_open(cipher, nonce, sodium.random(32))
        blob, header = sodium.blob_encrypt(b"x" * 1000, key)
        self.assertEqual(len(blob), 1017)
        self.assertEqual(sodium.blob_decrypt(blob, header, key), b"x" * 1000)

    def test_known_vectors(self):
        # libsodium's own test vector for crypto_secretbox (NaCl's "tests/secretbox")
        key = bytes.fromhex("1b27556473e985d462cd51197a9a46c76009549eac6474f206c4ee0844f68389")
        nonce = bytes.fromhex("69696ee955b62b73cd62bda875fc73d68219e0036b7a0b37")
        plain = sodium.secretbox_open(
            bytes.fromhex("f3ffc7703f9400e52a7dfb4b3d3305d9"
                          "8e993b9f48681273c29650ba32fc76ce48332ea7164d96a4476fb8c531a1186a"
                          "c0dfc17c98dce87b4da7f011ec48c97271d2c20f9b928fe2270d6fb863d51738"
                          "b48eeee314a7cc8ab932164548e526ae90224368517acfeabd6bb3732bc0e9da"
                          "99832b61ca01b6de56244a9e88d5f9b37973f622a43d14a6599b1f654cb45a74"
                          "e355a5"), nonce, key)
        self.assertEqual(plain[:8].hex(), "be075fc53c81f2d5")
        self.assertEqual(len(plain), 131)

    def test_argon2id_vector(self):
        # RFC 9106 has no vector without secret/associated data; check against the value
        # libsodium documents behaviour for: same input, same output, and salt matters
        a = sodium.pwhash("pw", b"0123456789abcdef", 2, 8 * 1024 * 1024)
        self.assertEqual(a, sodium.pwhash("pw", b"0123456789abcdef", 2, 8 * 1024 * 1024))
        self.assertNotEqual(a, sodium.pwhash("pw", b"0123456789abcdeg", 2, 8 * 1024 * 1024))
        self.assertEqual(len(a), 32)

    def test_file_stream(self):
        key = sodium.random(32)
        with tempfile.TemporaryDirectory() as folder:
            for size in (1, ente.CHUNK - 1, ente.CHUNK, ente.CHUNK + 5, 2 * ente.CHUNK):
                source = os.path.join(folder, "in")
                data = os.urandom(size)
                with open(source, "wb") as out:
                    out.write(data)
                target = os.path.join(folder, "out")
                header, length, md5, digest = ente.encrypt_file(source, key, target)
                with open(target, "rb") as handle:
                    sealed = handle.read()
                chunks = -(-size // ente.CHUNK)
                self.assertEqual(length, size + 17 * chunks)
                self.assertEqual(len(sealed), length)
                self.assertEqual(md5, b64(hashlib.md5(sealed).digest()))
                self.assertEqual(digest, b64(hashlib.blake2b(data, digest_size=64).digest()))
                self.assertEqual(decrypt_stream(sealed, header, key), data)


class SignIn(Base):
    def test_sign_in_and_restart(self):
        self.sign_in()
        state = backend.state()
        self.assertTrue(state["signedIn"])
        self.assertEqual(state["email"], "me@example.org")
        self.assertEqual(backend.session["token"], self.account.token)
        self.assertEqual(unb64(backend.session["masterKey"]), self.account.master)
        mode = os.stat(os.path.join(backend.data_dir, "session.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        with open(os.path.join(backend.data_dir, "session.json")) as kept:
            self.assertNotIn(PASSWORD, kept.read())
        backend._db.close()
        self.assertTrue(backend.init(backend.data_dir)["signedIn"])

    def test_wrong_code_and_password(self):
        backend.send_code(self.url, "me@example.org")
        backend.verify_code("000000")
        backend.wait()
        self.assertEqual(self.last("login")[1], "error")
        self.assertIn("Wrong or expired code", self.last("login")[2])
        backend.verify_code("123456")
        backend.unlock("nope")
        backend.wait()
        self.assertEqual(self.last("login")[1:], ("error", "Wrong password"))
        self.assertFalse(backend.state()["signedIn"])
        backend.unlock(PASSWORD)
        backend.wait()
        self.assertEqual(self.last("login")[1], "done")

    def test_unknown_address(self):
        backend.send_code(self.url, "Other@example.org ")
        backend.wait()
        self.assertEqual(self.last("login")[1], "error")
        self.assertIn('no account for "other@example.org"', self.last("login")[2])
        backend.send_code(self.url, " Me@Example.org")
        backend.wait()
        self.assertEqual(self.last("login")[1], "code")

    def test_two_factor(self):
        self.account.totp = True
        backend.send_code(self.url, "me@example.org")
        backend.verify_code("123456")
        backend.wait()
        self.assertEqual(self.last("login")[1], "totp")
        backend.verify_totp("654321")
        backend.wait()
        self.assertEqual(self.last("login")[1], "password")
        backend.unlock(PASSWORD)
        backend.wait()
        self.assertEqual(self.last("login")[1], "done")

    def test_sign_out(self):
        self.sign_in()
        backend.sign_out()
        backend.wait()
        self.assertFalse(backend.state()["signedIn"])
        self.assertFalse(os.path.exists(os.path.join(backend.data_dir, "session.json")))
        self.assertIn("POST /users/logout", self.account.calls)


class Uploading(Base):
    def album(self, name="Phone", make_main=True):
        backend.create_album(name, make_main)
        backend.wait()
        listed, message = self.last("albums")[1:3]
        self.assertEqual(message, "")
        return listed

    def test_sources(self):
        self.addCleanup(os.environ.__setitem__, "HOME", os.environ.get("HOME", "/root"))
        os.environ["HOME"] = self.tmp.name
        backend._db.close()
        os.remove(os.path.join(backend.data_dir, "uploads.db"))
        backend.init(backend.data_dir)
        camera = os.path.join(self.tmp.name, "Pictures", "Camera")
        shots = os.path.join(self.tmp.name, "Pictures", "Screenshots")
        os.makedirs(camera)
        os.makedirs(os.path.join(shots, ".hidden"))
        os.makedirs(os.path.join(self.tmp.name, "Downloads"))
        make_jpeg(os.path.join(camera, "c1.jpg"), (320, 240), exif=False)
        make_jpeg(os.path.join(camera, "c2.jpg"), (320, 240), exif=False)
        make_jpeg(os.path.join(shots, "s1.png.jpg"), (320, 240), exif=False)
        make_jpeg(os.path.join(shots, ".hidden", "x.jpg"), (320, 240), exif=False)
        with open(os.path.join(shots, "readme.txt"), "w") as out:
            out.write("x")

        # the camera folders are there from the start; the video one does not exist here
        listed = backend.sources()
        self.assertEqual([(s["label"], s["on"], s["exists"]) for s in listed],
                         [("Pictures/Camera", True, True), ("Videos/Camera", True, False)])
        self.assertEqual((listed[0]["total"], listed[0]["fresh"], listed[0]["albumId"]),
                         (2, 2, 0))
        self.assertEqual([s["label"] for s in backend.suggestions()],
                         ["Pictures/Screenshots", "Pictures"])
        self.assertEqual(backend.state()["sources"], 2)

        # nowhere to upload yet
        self.sign_in()
        self.assertEqual(backend.upload_sources(),
                         {"queued": 0, "noAlbum": ["Pictures/Camera", "Videos/Camera"]})

        # the folder browser
        top = backend.folders("")
        self.assertEqual(top["dirs"][0]["label"], "Home")
        step = backend.folders(os.path.join(self.tmp.name, "Pictures"))
        self.assertEqual(([d["name"] for d in step["dirs"]], step["here"], step["parent"]),
                         (["Camera", "Screenshots"], 0, self.tmp.name))
        self.assertEqual(backend.folders(shots)["here"], 1)

        # camera -> main album, screenshots -> an album of their own
        self.album("Phone")
        main = backend.album["id"]
        backend.add_source(shots)
        self.album("Screens", make_main=False)
        self.assertEqual(backend.album["id"], main)
        screens = self.last("albums")[3]
        self.assertNotEqual(screens, main)
        backend.set_source_album(shots, screens, "Screens")
        backend.set_source(os.path.join(self.tmp.name, "Videos", "Camera"), False)
        self.assertEqual(backend.state()["sources"], 2)
        self.assertEqual(backend.upload_sources(), {"queued": 3, "noAlbum": []})
        backend.wait()
        self.assertEqual(sorted((f["collectionID"], len(f["file"]["objectKey"]) > 0)
                                for f in self.account.files),
                         sorted([(main, True), (main, True), (screens, True)]))
        listed = {s["label"]: s for s in backend.sources()}
        self.assertEqual((listed["Pictures/Screenshots"]["albumName"],
                          listed["Pictures/Screenshots"]["own"],
                          listed["Pictures/Screenshots"]["fresh"]), ("Screens", True, 0))
        self.assertEqual(listed["Pictures/Camera"]["albumName"], "Phone")
        self.assertEqual(backend.upload_sources()["queued"], 0)

        # a folder that is in two sources goes once per album; one source on its own
        backend.add_source(os.path.join(self.tmp.name, "Pictures"))
        self.assertEqual(backend.upload_sources()["queued"], 1)   # s1 into the main album
        backend.wait()
        make_jpeg(os.path.join(shots, "s2.jpg"), (320, 240), exif=False)
        make_jpeg(os.path.join(camera, "c3.jpg"), (320, 240), exif=False)
        self.assertEqual(backend.upload_sources(True, shots)["queued"], 1)
        backend.wait()
        backend.remove_source(os.path.join(self.tmp.name, "Pictures"))
        self.assertNotIn("Pictures", [s["label"] for s in backend.sources()])
        self.assertEqual(backend.upload_sources()["queued"], 1)   # c3
        backend.wait()
        self.assertEqual(len(self.account.files), 6)

    def test_albums(self):
        self.sign_in()
        listed = self.album("Ferien 🏖")
        self.assertEqual([a["name"] for a in listed], ["Ferien 🏖"])
        self.assertEqual(backend.state()["albumName"], "Ferien 🏖")
        stored = self.account.collections[0]
        key = sodium.secretbox_open(unb64(stored["encryptedKey"]),
                                    unb64(stored["keyDecryptionNonce"]), self.account.master)
        self.assertEqual(sodium.secretbox_open(unb64(stored["encryptedName"]),
                                               unb64(stored["nameDecryptionNonce"]), key),
                         "Ferien 🏖".encode())
        self.assertEqual(stored["type"], "album")
        # somebody else's album and a deleted one are not offered
        self.account.collections.append(dict(stored, id=5, owner={"id": 9}))
        self.account.collections.append(dict(stored, id=6, isDeleted=True))
        backend.albums()
        backend.wait()
        self.assertEqual(len(self.last("albums")[1]), 1)

    def test_upload_photo(self):
        self.sign_in()
        self.album()
        path = os.path.join(self.photos, "IMG_1.jpg")
        make_jpeg(path)
        with open(path, "rb") as handle:
            original = handle.read()
        backend.upload([path])
        backend.wait()
        result = self.last("result")[1]
        self.assertEqual(result["status"], "done", backend.events)
        self.assertEqual((result["path"], result["taken"], result["video"]),
                         (path, 1714972089, False))
        listed = backend.history()
        self.assertEqual([(e["path"], e["taken"]) for e in listed], [(path, 1714972089)])
        final = self.last("upload")[1]
        self.assertEqual((final["active"], final["done"], final["total"]), (False, 1, 1))
        self.assertNotIn("GET /files/upload-urls", self.account.calls)

        sent = self.account.files[0]
        stored = self.account.collections[0]
        self.assertEqual(sent["collectionID"], stored["id"])
        album_key = sodium.secretbox_open(unb64(stored["encryptedKey"]),
                                          unb64(stored["keyDecryptionNonce"]),
                                          self.account.master)
        key = sodium.secretbox_open(unb64(sent["encryptedKey"]),
                                    unb64(sent["keyDecryptionNonce"]), album_key)
        body = self.account.objects[sent["file"]["objectKey"]]
        self.assertEqual(decrypt_stream(body, unb64(sent["file"]["decryptionHeader"]), key),
                         original)
        thumb = sodium.blob_decrypt(self.account.objects[sent["thumbnail"]["objectKey"]],
                                    unb64(sent["thumbnail"]["decryptionHeader"]), key)
        from PIL import Image
        import io
        picture = Image.open(io.BytesIO(thumb))
        self.assertEqual(picture.format, "JPEG")
        self.assertEqual(max(picture.size), 720)
        self.assertLessEqual(len(thumb), 100 * 1024)
        meta = json.loads(sodium.blob_decrypt(unb64(sent["metadata"]["encryptedData"]),
                                              unb64(sent["metadata"]["decryptionHeader"]), key))
        self.assertEqual(meta["title"], "IMG_1.jpg")
        self.assertEqual(meta["fileType"], 0)
        self.assertEqual(meta["creationTime"], 1714972089 * 1000000)   # 05:08:09 UTC
        self.assertAlmostEqual(meta["latitude"], 48.2083333, places=5)
        self.assertAlmostEqual(meta["longitude"], -16.5, places=5)
        self.assertEqual(meta["hash"],
                         b64(hashlib.blake2b(original, digest_size=64).digest()))
        self.assertNotIn("hasStaticThumbnail", meta)
        magic = sent["pubMagicMetadata"]
        self.assertEqual((magic["version"], magic["count"]), (1, 2))
        self.assertEqual(json.loads(sodium.blob_decrypt(unb64(magic["data"]),
                                                        unb64(magic["header"]), key)),
                         {"w": 1600, "h": 1200})
        self.assertEqual([n for n in os.listdir(backend.cache_dir) if n.endswith(".tmp")], [])

    def test_skips_and_scan(self):
        self.sign_in()
        self.album()
        first = os.path.join(self.photos, "a.jpg")
        make_jpeg(first, (320, 240), exif=False)
        backend.upload([first])
        backend.wait()
        # the same file again, and a copy of it under another name
        copy = os.path.join(self.photos, "copy.jpg")
        with open(first, "rb") as source, open(copy, "wb") as out:
            out.write(source.read())
        backend.events.clear()
        backend.upload([first, copy])
        backend.wait()
        self.assertEqual([e[1]["status"] for e in backend.events if e[0] == "result"],
                         ["skipped", "skipped"])
        self.assertEqual(len(self.account.files), 1)
        # a scan finds only what is new: one photo, one video; notes are left alone
        make_jpeg(os.path.join(self.photos, "b.jpg"), (320, 240), exif=False)
        make_mp4(os.path.join(self.photos, "clip.mp4"))
        with open(os.path.join(self.photos, "notes.txt"), "w") as out:
            out.write("x")
        self.assertEqual(backend.scan([self.photos], False), 1)
        backend.wait()
        self.assertEqual(backend.scan([self.photos], True), 1)
        backend.wait()
        self.assertEqual(len(self.account.files), 3)
        self.assertEqual(backend.scan([self.photos], True), 0)
        video = self.account.files[2]
        stored = self.account.collections[0]
        album_key = sodium.secretbox_open(unb64(stored["encryptedKey"]),
                                          unb64(stored["keyDecryptionNonce"]),
                                          self.account.master)
        key = sodium.secretbox_open(unb64(video["encryptedKey"]),
                                    unb64(video["keyDecryptionNonce"]), album_key)
        meta = json.loads(sodium.blob_decrypt(unb64(video["metadata"]["encryptedData"]),
                                              unb64(video["metadata"]["decryptionHeader"]),
                                              key))
        self.assertEqual(meta["fileType"], 1)
        self.assertTrue(meta["hasStaticThumbnail"])
        self.assertNotIn("pubMagicMetadata", video)

    def test_history_and_old_record(self):
        # a record from 0.1.0 has no capture times: they are read once from the files
        backend._db.close()
        import sqlite3
        path = os.path.join(self.photos, "old.jpg")
        make_jpeg(path, (320, 240))
        plain = os.path.join(self.photos, "plain.jpg")
        make_jpeg(plain, (320, 240), exif=False)
        os.utime(plain, (1600000000, 1600000000))
        database = os.path.join(backend.data_dir, "uploads.db")
        os.remove(database)
        old = sqlite3.connect(database)
        old.execute("CREATE TABLE uploaded (path TEXT, size INTEGER, mtime INTEGER,"
                    " hash TEXT, album INTEGER, file INTEGER, at INTEGER)")
        old.execute("INSERT INTO uploaded VALUES (?,1,1600000000,'h',1,1,5)", (plain,))
        old.execute("INSERT INTO uploaded VALUES (?,1,1,'h2',1,2,6)", (path,))
        old.commit()
        old.close()
        backend.init(backend.data_dir)
        listed = backend.history()
        self.assertEqual([(e["name"], e["taken"], e["at"]) for e in listed],
                         [("old.jpg", 1714972089, 6), ("plain.jpg", 1600000000, 5)])
        self.assertEqual(backend._sql("SELECT COUNT(*) FROM uploaded WHERE taken IS NULL",
                                      fetch=True)[0][0], 0)
        # skipped files report their kept time
        self.sign_in()
        self.album()
        backend.events.clear()
        backend._sql("UPDATE uploaded SET album=?, size=?, mtime=? WHERE path=?",
                     (backend.album["id"], os.stat(path).st_size,
                      int(os.stat(path).st_mtime), path))
        backend.upload([path])
        backend.wait()
        result = self.last("result")[1]
        self.assertEqual((result["status"], result["taken"]), ("skipped", 1714972089))

    def test_check(self):
        folder = self.photos

        def write(name, data):
            path = os.path.join(folder, name)
            with open(path, "wb") as out:
                out.write(data)
            return path

        good = os.path.join(folder, "good.jpg")
        make_jpeg(good, (640, 480))
        with open(good, "rb") as source:
            jpeg = source.read()
        from PIL import Image
        png = os.path.join(folder, "good.png")
        Image.new("RGB", (50, 40), "red").save(png)
        with open(png, "rb") as source:
            png_data = source.read()
        video = os.path.join(folder, "good.mp4")
        make_mp4(video)
        large = os.path.join(folder, "large.mov")
        with open(large, "wb") as out:      # a box with a 64-bit length, and one to the end
            out.write(box(b"ftyp", b"qt  \x00\x00\x00\x00qt  ")
                      + box(b"moov", bytes(40))
                      + (1).to_bytes(4, "big") + b"mdat" + (16 + 300).to_bytes(8, "big")
                      + bytes(300) + bytes(4) + b"free" + bytes(20))
        heic = write("phone.heic", bytes(4) + b"ftypheic" + os.urandom(3000))
        motion = write("motion.jpg", jpeg + os.urandom(2000))   # extra data after the end
        for path in (good, png, video, large, heic, motion):
            ente.check_media(path)

        bad = {
            write("empty.jpg", b""): "Empty file",
            write("zeros.jpg", bytes(8000)): "only zeros",
            write("text.jpg", b"not a picture at all"): "not a picture",
            write("cut.jpg", jpeg[:len(jpeg) // 2]): "cut off",
            write("cut.png", png_data[:len(png_data) - 30]): "Broken file",
            write("junk.heic", b"x" * 500): "not a picture",
            write("text.mp4", b"hello world, no video in here"): "not a video",
            write("noise.webm", os.urandom(500)): "not a video",
        }
        make_mp4(os.path.join(folder, "cut.mp4"), cut=1000)
        bad[os.path.join(folder, "cut.mp4")] = "video cut off"
        make_mp4(os.path.join(folder, "unfinished.mp4"), index=False)
        bad[os.path.join(folder, "unfinished.mp4")] = "without index"
        for path, reason in bad.items():
            with self.assertRaises(ente.Unusable, msg=path) as caught:
                ente.check_media(path)
            self.assertIn(reason, str(caught.exception), path)

    def test_broken_skipped(self):
        self.sign_in()
        self.album()
        good = os.path.join(self.photos, "good.jpg")
        make_jpeg(good, (320, 240), exif=False)
        with open(good, "rb") as source:
            data = source.read()
        cut = os.path.join(self.photos, "cut.jpg")
        with open(cut, "wb") as out:
            out.write(data[:len(data) // 2])
        empty = os.path.join(self.photos, "empty.jpg")
        open(empty, "wb").close()
        make_mp4(os.path.join(self.photos, "unfinished.mp4"), index=False)

        self.assertEqual(backend.scan([self.photos]), 4)
        backend.wait()
        results = {e[1]["name"]: e[1] for e in backend.events if e[0] == "result"}
        self.assertEqual({n: r["status"] for n, r in results.items()},
                         {"good.jpg": "done", "cut.jpg": "skipped", "empty.jpg": "skipped",
                          "unfinished.mp4": "skipped"})
        self.assertIn("cut off", results["cut.jpg"]["detail"])
        final = self.last("upload")[1]
        self.assertEqual((final["done"], final["skipped"], final["failed"]), (1, 3, 0))
        self.assertEqual(len(self.account.files), 1)
        # remembered: not offered again, and counted apart in the sources
        self.assertEqual(backend.scan([self.photos]), 0)
        backend.add_source(self.photos)
        mine = [s for s in backend.sources() if s["path"] == self.photos][0]
        self.assertEqual((mine["total"], mine["fresh"], mine["broken"]), (4, 0, 3))
        # chosen by hand, it is skipped at once with the same reason
        backend.events.clear()
        backend.upload([cut])
        backend.wait()
        self.assertIn("cut off", self.last("result")[1]["detail"])
        # once the file is whole (it changes), it is tried again
        with open(cut, "wb") as out:
            out.write(data)
        os.utime(cut, (1700000000, 1700000000))
        self.assertEqual(backend.scan([self.photos]), 1)
        backend.wait()
        self.assertEqual(self.last("result")[1]["status"], "skipped")   # same photo as good.jpg
        self.assertIn("Same photo", self.last("result")[1]["detail"])

    def test_failures(self):
        self.sign_in()
        path = os.path.join(self.photos, "a.jpg")
        make_jpeg(path, (320, 240))
        backend.upload([path])
        backend.wait()
        self.assertIn("Choose an album", self.last("result")[1]["detail"])
        self.album()
        backend.upload([os.path.join(self.photos, "missing.jpg")])
        backend.wait()
        self.assertEqual(self.last("result")[1]["status"], "failed")
        # the session ends on the server: signed out, and the UI is told why
        self.account.token = "changed"
        backend.upload([path])
        backend.wait()
        self.assertFalse(backend.state()["signedIn"])
        self.assertIn("sign in again", backend.state()["error"])
        self.assertIn("sign in again", [e for e in backend.events if e[0] == "upload"
                                        and e[1].get("error")][-1][1]["error"])

    def test_cancel(self):
        self.sign_in()
        self.album()
        paths = []
        for index in range(4):
            paths.append(os.path.join(self.photos, "c%d.jpg" % index))
            make_jpeg(paths[-1], (320, 240), exif=False)
        backend.upload(paths[:2])
        backend.upload(paths[2:])
        backend.cancel()
        backend.wait()
        self.assertLess(len(self.account.files), 4)
        self.assertFalse(self.last("upload")[1]["active"])
        backend.upload(paths)
        backend.wait()
        self.assertEqual(len(self.account.files), 4)


class Waiting(Base):
    def wait_thumbs(self, ids, seconds=15):
        import time
        wanted = set(ids)
        end = time.time() + seconds
        while time.time() < end:
            got = {e[1]: e[2] for e in backend.events if e[0] == "thumb"}
            if wanted <= set(got):
                return got
            time.sleep(0.05)
        self.fail("thumbnails missing: %r" % (wanted - set(got)))


class Browsing(Waiting):
    def test_browse(self):
        import io
        import zipfile
        from PIL import Image
        acc = self.account
        acc.page = 2                                # makes the sync ask several times
        self.sign_in()
        backend.create_album("Phone")
        backend.wait()
        phone = backend.album["id"]
        first = os.path.join(self.photos, "first.jpg")
        make_jpeg(first, (1600, 1200))              # taken 2024-05-06
        with open(first, "rb") as source:
            first_data = source.read()
        backend.upload([first])
        backend.wait()
        uploaded = acc.files[-1]["id"]

        jpeg = io.BytesIO()
        Image.new("RGB", (64, 48), "blue").save(jpeg, "JPEG")
        older = acc.add_file(phone, jpeg.getvalue(), "old.jpg", taken=1500000000)
        heic = acc.add_file(phone, b"\x00\x00\x00\x18ftypheic" + bytes(100), "x.heic",
                            taken=1500000100)
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w") as z:
            z.writestr("IMG_1.jpg", jpeg.getvalue())
            z.writestr("IMG_1.mov", b"movie")
        live = acc.add_file(phone, packed.getvalue(), "IMG_1.zip", kind=2, taken=1500000200)
        clip = os.urandom(ente.CHUNK + 999)          # two chunks
        video = acc.add_file(phone, clip, "clip.MOV", kind=1, taken=1500000300,
                             public={"caption": "At the lake"})
        archived = acc.add_file(phone, jpeg.getvalue(), "arch.jpg", taken=1500000400,
                                private={"visibility": 1})
        hidden_one = acc.add_file(phone, jpeg.getvalue(), "hid.jpg", taken=1500000500,
                                  private={"visibility": 2})
        edited = acc.add_file(phone, jpeg.getvalue(), "orig.jpg", taken=1500000600,
                              public={"editedTime": 1400000000 * 1000000,
                                      "editedName": "renamed.jpg", "w": 64, "h": 48})
        backend.create_album("Secret", False)
        backend.wait()
        secret = acc.collections[-1]["id"]
        acc.set_magic(secret, {"subType": 1, "visibility": 2})
        in_secret = acc.add_file(secret, jpeg.getvalue(), "s.jpg", taken=1600000000)
        backend.create_album("Old trip", False)
        backend.wait()
        trip = acc.collections[-1]["id"]
        acc.set_magic(trip, {"visibility": 1})
        in_trip = acc.add_file(trip, jpeg.getvalue(), "t.jpg", taken=1600000000)

        cloud_ = backend._cloud
        self.assertTrue(cloud_.sync(backend._master(), 7))
        self.assertEqual(cloud_.status["error"], "")

        def ids(rows):
            return [int(c.split(":")[0]) for r in rows if r["t"] == "r"
                    for c in r["cells"].split(",")]

        timeline = cloud_.rows(0, 3)
        self.assertEqual(ids(timeline), [uploaded, video, live, heic, older, edited])
        self.assertEqual([r["t"] for r in timeline][:2], ["d", "r"])
        self.assertTrue(all(len(r["cells"].split(",")) <= 3 for r in timeline if r["t"] == "r"))
        self.assertIn("%d:1" % video, ",".join(r.get("cells", "") for r in timeline))
        self.assertEqual(ids(cloud_.rows(phone, 4)),
                         [uploaded, archived, video, live, heic, older, edited])
        listed = {a["name"]: a for a in cloud_.albums()}
        self.assertEqual(sorted(listed), ["Old trip", "Phone"])
        self.assertEqual((listed["Phone"]["count"], listed["Phone"]["cover"]), (7, uploaded))
        self.assertTrue(listed["Old trip"]["archived"])
        self.assertEqual(ids(cloud_.rows(trip)), [in_trip])
        self.assertEqual(cloud_.details(edited)["title"], "renamed.jpg")
        self.assertEqual(cloud_.details(edited)["taken"], 1400000000)
        self.assertEqual(cloud_.counts()["timeline"], 6)
        self.assertNotIn(hidden_one, ids(cloud_.rows(phone)))
        self.assertNotIn(in_secret, [i for a in cloud_.albums() for i in ids(cloud_.rows(a["id"]))])
        # the upload albums leave the hidden album out too
        self.assertNotIn("Secret", [a["name"] for a in ente.albums(backend._api(),
                                                                   backend._master(), 7)])

        # thumbnails: fetched in the background, smaller than Ente's
        self.assertEqual(backend.cloud_thumbs([uploaded, older]), {})
        got = self.wait_thumbs([uploaded, older])
        with Image.open(got[uploaded]) as small:
            self.assertEqual(min(small.size), 240)
            self.assertEqual(small.size, (320, 240))
        ente_size = len(acc.objects[acc.files[0]["thumbnail"]["objectKey"]])
        self.assertLess(os.path.getsize(got[uploaded]), ente_size)
        with Image.open(got[older]) as small:      # 720 x 540 from Ente
            self.assertEqual(small.size, (320, 240))
        # thumbnails kept from an earlier version (larger, better) are shrunk once, in place
        old = cloud_.thumb_path(older)
        Image.effect_noise((384, 288), 60).convert("RGB").save(old, "JPEG", quality=60)
        before = os.path.getsize(old)
        with open(os.path.join(cloud_.thumb_dir, "junk.jpg"), "wb") as out:
            out.write(b"not a picture")
        cloud_._sql("DELETE FROM info WHERE name='thumb_version'")
        asked = len(acc.storage_headers)
        self.assertEqual(cloud_.shrink_thumbs(), 1)            # the current one is left alone
        with Image.open(old) as small:
            self.assertEqual(small.size, (320, 240))
        self.assertLess(os.path.getsize(old), before * 0.7)
        self.assertFalse(os.path.exists(os.path.join(cloud_.thumb_dir, "junk.jpg")))
        self.assertEqual(len(acc.storage_headers), asked)      # nothing fetched
        self.assertEqual(cloud_.shrink_thumbs(), 0)            # only once
        self.assertEqual(set(backend.cloud_thumbs([uploaded])), {str(uploaded)})
        self.assertTrue(acc.storage_headers)
        self.assertFalse([h for h in acc.storage_headers
                          if any(k.lower() == "x-auth-token" for k in h)])

        # full photos
        full = cloud_._make_full(uploaded)
        with open(full["path"], "rb") as source:
            self.assertEqual(source.read(), first_data)
        self.assertEqual(full["note"], "")
        self.assertEqual(cloud_._make_full(uploaded)["path"], full["path"])   # kept
        self.assertEqual((full["video"], full["motion"]), ("", False))
        picture = cloud_._make_full(live)
        self.assertTrue(picture["path"].endswith(".jpg"))
        with open(picture["path"], "rb") as source:
            self.assertEqual(source.read(), jpeg.getvalue())
        self.assertTrue(picture["motion"])
        with open(picture["video"], "rb") as source:
            self.assertEqual(source.read(), b"movie")
        self.assertEqual(cloud_._make_full(live), picture)                     # kept
        self.assertIn("HEIC", cloud_._make_full(heic)["note"])
        self.assertIn("HEIC", cloud_._make_full(heic)["note"])                 # kept
        played = cloud_._make_full(video)
        self.assertEqual((played["path"], played["motion"], played["note"]), ("", False, ""))
        self.assertTrue(played["video"].endswith(".mov"))
        with open(played["video"], "rb") as source:
            self.assertEqual(source.read(), clip)
        self.assertEqual(cloud_.details(video)["caption"], "At the lake")
        self.assertEqual(cloud_.details(uploaded)["caption"], "")
        backend.events.clear()
        backend.cloud_open(older)
        import time
        for _ in range(200):
            done = [e for e in backend.events if e[0] == "full" and "path" in e[2]]
            if done:
                break
            time.sleep(0.05)
        self.assertEqual(done[0][1], older)

        # changes: only albums that changed are asked again, also after a change of the
        # album itself (its time is newer than any of its files then)
        asked = acc.calls.count("GET /collections/v2/diff")
        acc.tick(trip)
        cloud_.sync(backend._master(), 7)
        cloud_.sync(backend._master(), 7)
        self.assertEqual(acc.calls.count("GET /collections/v2/diff") - asked, 1)
        acc.delete_file(older, phone)
        asked = acc.calls.count("GET /collections/v2/diff")
        cloud_.sync(backend._master(), 7)
        self.assertEqual(acc.calls.count("GET /collections/v2/diff") - asked, 1)
        self.assertNotIn(older, ids(cloud_.rows(0)))
        self.assertIsNone(cloud_.details(older).get("id"))
        self.assertFalse(os.path.exists(cloud_.thumb_path(older)))

        # the space for full files is limited; the files just made always stay
        import cloud as cloud_module
        old = cloud_module.FULL_BYTES
        cloud_module.FULL_BYTES = len(clip) // 2
        try:
            os.utime(played["video"])                # the newest, but too big now
            cloud_._trim_full(keep=(full["path"],))
            left = os.listdir(cloud_.full_dir)
            self.assertNotIn(os.path.basename(played["video"]), left)
            self.assertIn(os.path.basename(full["path"]), left)
            self.assertIn(os.path.basename(picture["path"]), left)   # small ones fit
            cloud_._trim_full(keep=(played["video"],))  # just made: stays all the same
        finally:
            cloud_module.FULL_BYTES = old

        # signing out leaves nothing behind
        backend.sign_out()
        backend.wait()
        self.assertEqual(cloud_.rows(0), [])
        self.assertEqual(os.listdir(cloud_.thumb_dir), [])
        self.assertEqual(os.listdir(cloud_.full_dir), [])
        self.assertEqual(os.stat(cloud_.path).st_mode & 0o777, 0o600)


class Shared(Waiting):
    def test_shared_albums(self):
        import io
        from PIL import Image
        acc = self.account
        self.sign_in()
        self.assertTrue(backend.session.get("secretKey"))
        jpeg = io.BytesIO()
        Image.new("RGB", (64, 48), "green").save(jpeg, "JPEG")
        backend.create_album("Mine")
        backend.wait()
        mine = acc.collections[-1]["id"]
        own = acc.add_file(mine, jpeg.getvalue(), "own.jpg", taken=1600000000)
        trip = acc.add_shared("Their trip")
        theirs = acc.add_file(trip, jpeg.getvalue(), "theirs.jpg", taken=1700000000)
        tucked = acc.add_shared("Tucked away", shared_magic={"visibility": 2})
        acc.add_file(tucked, jpeg.getvalue(), "t.jpg")
        cloud_ = backend._cloud

        def ids(rows):
            return [int(c.split(":")[0]) for r in rows if r["t"] == "r"
                    for c in r["cells"].split(",")]

        cloud_.sync(backend._master(), 7, backend._keypair())
        self.assertEqual(cloud_.status["error"], "")
        self.assertEqual(ids(cloud_.rows(0)), [own])           # shared ones stay out
        listed = cloud_.albums()
        self.assertEqual([(a["name"], a["owner"], a["section"]) for a in listed],
                         [("Mine", "", "own"), ("Their trip", "friend@example.org", "shared")])
        self.assertEqual(ids(cloud_.rows(trip)), [theirs])
        self.assertEqual(cloud_.counts()["locked"], 0)
        backend.cloud_thumbs([theirs])
        self.wait_thumbs([theirs])
        self.assertTrue(cloud_._make_full(theirs)["path"])
        # shared albums are never offered for uploads
        self.assertEqual([a["name"] for a in ente.albums(backend._api(), backend._master(), 7)],
                         ["Mine"])

        # a session from before 0.6.0 has no key pair: the shared albums are only counted
        cloud_.reset()
        cloud_.sync(backend._master(), 7, None)
        self.assertEqual([a["name"] for a in cloud_.albums()], ["Mine"])
        self.assertEqual(cloud_.counts()["locked"], 2)

    def test_index_from_050(self):
        """An index made by 0.5.0 (no captions, no sharers) is read again once."""
        import sqlite3
        self.sign_in()
        backend.create_album("Mine")
        backend.wait()
        mine = self.account.collections[-1]["id"]
        first = self.account.add_file(mine, b"\xff\xd8\xff" + bytes(50), "a.jpg",
                                      public={"caption": "hello"})
        cloud_ = backend._cloud
        cloud_.db.close()
        os.remove(cloud_.path)
        old = sqlite3.connect(cloud_.path)
        old.executescript("""
            CREATE TABLE albums (id INTEGER PRIMARY KEY, name TEXT, type TEXT, key TEXT,
                visibility INTEGER, updated INTEGER, synced INTEGER DEFAULT 0);
            CREATE TABLE files (id INTEGER PRIMARY KEY, key TEXT, title TEXT, taken INTEGER,
                kind INTEGER, w INTEGER, h INTEGER, thumb_header TEXT, file_header TEXT,
                size INTEGER, visibility INTEGER);
            CREATE TABLE members (file INTEGER, album INTEGER, PRIMARY KEY (file, album));
            CREATE TABLE info (name TEXT PRIMARY KEY, value TEXT);""")
        old.execute("INSERT INTO albums VALUES (?, 'Mine', 'album', 'x', 0, 99999, 99999)",
                    (mine,))
        old.commit()
        old.close()
        backend.init(backend.data_dir)
        cloud_ = backend._cloud
        cloud_.sync(backend._master(), 7, backend._keypair())
        self.assertEqual(cloud_.details(first)["caption"], "hello")


class AlreadyInEnte(Waiting):
    def test_skip_what_ente_has(self):
        acc = self.account
        self.sign_in()
        backend.create_album("From the PC", False)
        backend.wait()
        elsewhere = acc.collections[-1]["id"]
        backend.create_album("Phone")
        backend.wait()
        photo = os.path.join(self.photos, "same.jpg")
        make_jpeg(photo, (320, 240), exif=False)
        with open(photo, "rb") as source:
            data = source.read()
        meta_hash = ente.b64(__import__("hashlib").blake2b(data, digest_size=64).digest())
        # the same photo, uploaded from another device, as Ente's apps record it
        other = acc.add_file(elsewhere, data, "IMG_from_pc.jpg")
        for entry in acc.files:
            if entry["id"] == other:
                key = sodium.secretbox_open(unb64(entry["encryptedKey"]),
                                            unb64(entry["keyDecryptionNonce"]),
                                            acc.collection_key(elsewhere))
                meta = {"title": "IMG_from_pc.jpg", "fileType": 0, "hash": meta_hash,
                        "creationTime": 1, "modificationTime": 1}
                blob, header = sodium.blob_encrypt(json.dumps(meta).encode(), key)
                entry["metadata"] = {"encryptedData": b64(blob), "decryptionHeader": b64(header)}
        new = os.path.join(self.photos, "new.jpg")
        make_jpeg(new, (320, 240), exif=False)
        asked = acc.calls.count("POST /files/upload-url")

        backend.upload([photo, new])                    # the index is read first
        backend.wait()
        results = {e[1]["name"]: e[1] for e in backend.events if e[0] == "result"}
        self.assertEqual(results["same.jpg"]["status"], "skipped")
        self.assertEqual(results["same.jpg"]["detail"], 'Already in Ente (album "From the PC")')
        self.assertEqual(results["new.jpg"]["status"], "done")
        self.assertEqual(acc.calls.count("POST /files/upload-url") - asked, 2)   # new.jpg only
        self.assertTrue(backend._cloud.synced_once())
        # the next time the phone's own record answers: the file is not even read again
        backend.events.clear()
        backend.upload([photo])
        backend.wait()
        self.assertEqual(self.last("result")[1]["detail"], "Already uploaded")

        # once it is in Ente's trash it counts no more
        backend.forget_uploads()
        acc.delete_file(other, elsewhere)
        backend._index_checked[0] = 0.0
        backend.events.clear()
        backend.upload([photo])
        backend.wait()
        self.assertEqual(self.last("result")[1]["status"], "done")

    def test_index_unreachable(self):
        """When the index cannot be read, uploads go on as before."""
        self.sign_in()
        backend.create_album("Phone")
        backend.wait()
        backend._cloud.sync = lambda *args: (_ for _ in ()).throw(OSError("no index"))
        photo = os.path.join(self.photos, "a.jpg")
        make_jpeg(photo, (320, 240), exif=False)
        backend.upload([photo])
        backend.wait()
        self.assertEqual(self.last("result")[1]["status"], "done")


class Organizing(Waiting):
    def ids(self, rows):
        return [int(c.split(":")[0]) for r in rows if r["t"] == "r"
                for c in r["cells"].split(",")]

    def key_in(self, file_id, album_id):
        """The file key as the server holds it for that album, opened with the album key."""
        entry = [e for e in self.account.files if e["id"] == file_id
                 and e["collectionID"] == album_id and not e["isDeleted"]][-1]
        return sodium.secretbox_open(unb64(entry["encryptedKey"]),
                                     unb64(entry["keyDecryptionNonce"]),
                                     self.account.collection_key(album_id))

    def test_on_this_day(self):
        import time
        acc = self.account
        self.sign_in()
        backend.create_album("A")
        backend.wait()
        a = acc.collections[-1]["id"]
        now = time.mktime((2026, 11, 12, 12, 0, 0, 0, 0, -1))
        two = acc.add_file(a, b"x", "2024.jpg", taken=int(time.mktime((2024, 11, 12, 9, 0, 0, 0, 0, -1))))
        six = acc.add_file(a, b"x", "2020.jpg", taken=int(time.mktime((2020, 11, 12, 23, 0, 0, 0, 0, -1))))
        acc.add_file(a, b"x", "next.jpg", taken=int(time.mktime((2024, 11, 13, 9, 0, 0, 0, 0, -1))))
        acc.add_file(a, b"x", "today.jpg", taken=int(now - 3600))
        cloud_ = backend._cloud
        cloud_.sync(backend._master(), 7, backend._keypair())
        rows = cloud_.on_this_day(4, now)
        self.assertEqual(self.ids(rows), [two, six])
        self.assertEqual([r["ago"] for r in rows if r["t"] == "d"], [2, 6])

    def test_albums_favorites_trash(self):
        acc = self.account
        self.sign_in()
        backend.create_album("A")
        backend.wait()
        a = acc.collections[-1]["id"]
        backend.create_album("B", False)
        backend.wait()
        b = acc.collections[-1]["id"]
        one = acc.add_file(a, b"one", "one.jpg", taken=1500000001)
        two = acc.add_file(a, b"two", "two.jpg", taken=1500000002)
        theirs = acc.add_file(acc.add_shared("Theirs"), b"t", "t.jpg")
        cloud_ = backend._cloud
        master = backend._master()
        cloud_.sync(master, 7, backend._keypair())
        file_key = sodium.secretbox_open(unb64(acc.files[0]["encryptedKey"]),
                                         unb64(acc.files[0]["keyDecryptionNonce"]),
                                         acc.collection_key(a))

        # add: the file key is sealed for the album it goes to
        self.assertEqual(cloud_.add_to_album([one, two, two, theirs], b), {"done": 2, "skipped": 1})
        self.assertEqual(self.key_in(one, b), file_key)
        self.assertEqual(self.ids(cloud_.rows(b)), [two, one])
        self.assertEqual(cloud_.add_to_album([one], b), {"done": 0, "skipped": 1})
        # move
        self.assertEqual(cloud_.move_to_album([one], a, b), {"done": 1, "skipped": 0})
        cloud_.sync(master, 7, backend._keypair())
        self.assertEqual(self.ids(cloud_.rows(a)), [two])
        self.assertEqual(self.ids(cloud_.rows(b)), [two, one])

        # favorites: Ente's favorites album is made on first use
        self.assertEqual(cloud_.set_favorite([two], True, master)["done"], 1)
        favorites = [c for c in acc.collections if c["type"] == "favorites"]
        self.assertEqual(len(favorites), 1)
        cloud_.sync(master, 7, backend._keypair())
        self.assertTrue(cloud_.details(two)["favorite"])
        self.assertFalse(cloud_.details(one)["favorite"])
        cloud_.set_favorite([one], True, master)
        self.assertEqual(len([c for c in acc.collections if c["type"] == "favorites"]), 1)
        # leaving favorites: to another album that has it already ...
        cloud_.set_favorite([two], False, master)
        cloud_.sync(master, 7, backend._keypair())
        self.assertFalse(cloud_.details(two)["favorite"])
        self.assertIn(two, self.ids(cloud_.rows(a)))
        self.assertFalse([c for c in acc.collections if c["type"] == "uncategorized"])
        # ... or, when it is in no other, to "Uncategorized"
        only = acc.add_file(favorites[0]["id"], b"fav", "fav.jpg", taken=1500000003)
        cloud_.sync(master, 7, backend._keypair())
        cloud_.set_favorite([only], False, master)
        cloud_.sync(master, 7, backend._keypair())
        uncategorized = [c for c in acc.collections if c["type"] == "uncategorized"]
        self.assertEqual(len(uncategorized), 1)
        self.assertEqual(self.ids(cloud_.rows(uncategorized[0]["id"])), [only])

        # the trash: listed, viewed, restored into the album it left
        backend.events.clear()
        backend.cloud_trash_many([one])
        self.wait_event("trashed", 0)
        self.assertEqual(cloud_.sync_trash(), {"count": 1, "unreadable": 0})
        self.assertEqual(self.ids(cloud_.trash_rows()), [one])
        self.assertTrue(cloud_.trash_details(one)["deleteBy"] > 0)
        self.assertEqual(backend.cloud_details(one)["title"], "one.jpg")
        backend.cloud_thumbs([one])
        self.assertTrue(self.wait_thumbs([one])[one])        # a path, not a failure
        with open(cloud_._make_full(one)["path"], "rb") as source:
            self.assertEqual(source.read(), b"one")
        self.assertEqual(cloud_.restore([one], master), {"done": 1, "skipped": 0})
        self.assertEqual(cloud_.trash_rows(), [])
        cloud_.sync(master, 7, backend._keypair())
        trashed_from = [r for r in acc.trash.values() if r["file"]["id"] == one][0]["file"]
        self.assertIn(one, self.ids(cloud_.rows(trashed_from["collectionID"])))
        self.assertEqual(cloud_.sync_trash()["count"], 0)

        # deleted for good, and emptied (only up to what was read)
        cloud_.trash_many([two, only])
        cloud_.sync_trash()
        self.assertEqual(cloud_.delete_forever([two]), {"done": 1, "skipped": 0})
        self.assertTrue(acc.trash[two]["isDeleted"])
        self.assertEqual(self.ids(cloud_.trash_rows()), [only])
        read_up_to = int(cloud_._info("trash_updated"))
        self.assertEqual(cloud_.empty_trash(), {"done": 1, "skipped": 0})
        self.assertEqual(acc.empty_requests, [{"lastUpdatedAt": read_up_to}])
        self.assertTrue(acc.trash[only]["isDeleted"])
        self.assertEqual(cloud_.sync_trash()["count"], 0)

    def wait_event(self, name, file_id, seconds=15):
        import time
        end = time.time() + seconds
        while time.time() < end:
            found = [e for e in backend.events if e[0] == name and e[1] == file_id]
            if found:
                return found[-1][2]
            time.sleep(0.05)
        self.fail("no %s event" % name)

    def test_actions_through_the_backend(self):
        acc = self.account
        self.sign_in()
        backend.create_album("A")
        backend.wait()
        a = acc.collections[-1]["id"]
        one = acc.add_file(a, b"one", "one.jpg")
        backend._cloud.sync(backend._master(), 7, backend._keypair())
        self.addCleanup(os.environ.__setitem__, "HOME", os.environ.get("HOME", "/root"))
        os.environ["HOME"] = self.tmp.name
        backend.events.clear()
        backend.cloud_favorite([one], True)
        result = self.wait_event("acted", 0)
        self.assertEqual((result["what"], result["done"], result["error"]), ("favorite", 1, ""))
        self.assertTrue(backend.cloud_details(one)["favorite"])
        backend.events.clear()
        backend.cloud_save_many([one])
        result = self.wait_event("acted", 0)
        self.assertEqual((result["what"], result["done"], result["folder"]),
                         ("save", 1, os.path.join("Pictures", "Ente")))
        backend.events.clear()
        backend.cloud_trash_sync()
        self.assertEqual(self.wait_event("acted", 0)["count"], 0)
        self.assertEqual(backend.pending(), 0)


class Space(Base):
    def test_space(self):
        acc = self.account
        self.assertEqual(backend.space()["ente"], {})            # not signed in: no account
        self.assertGreater(backend.space()["phone"]["free"], 0)
        self.sign_in()
        ente_space = backend.space()["ente"]
        self.assertEqual(ente_space, {"used": 47079753190, "total": 214748364800,
                                      "free": 214748364800 - 47079753190, "files": 10550,
                                      "expires": 1797856708, "family": False})
        # bonus storage counts; a family plan's storage and all members' usage count
        acc.details["storageBonus"] = 10 * 1024 ** 3
        self.assertEqual(backend.space()["ente"]["total"], 214748364800 + 10 * 1024 ** 3)
        acc.details["familyData"] = {"storage": 2 * 1024 ** 4,
                                     "members": [{"usage": 100}, {"usage": 50}]}
        family = backend.space()["ente"]
        self.assertEqual((family["used"], family["total"], family["family"]),
                         (150, 2 * 1024 ** 4 + 10 * 1024 ** 3, True))
        # more used than the plan holds: none left, not less than none
        acc.details.pop("familyData")
        acc.details["usage"] = 10 ** 15
        self.assertEqual(backend.space()["ente"]["free"], 0)
        # a session the server no longer takes: said so, not a crash
        acc.token = "other"
        self.assertIn("sign in again", backend.space()["ente"]["error"])


class AutomaticBackup(Waiting):
    HEAD = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as out:
            out.write(text)
        return path

    def route(self, *rows):
        self.routes = getattr(self, "routes", 0) + 1
        return self.write("route-%d" % self.routes, self.HEAD + "".join(
            "%s\t%s\t0108A8C0\t%s\t0\t0\t%d\t00000000\t0\t0\t0\n" % row for row in rows))

    def test_network_and_battery(self):
        import autobackup
        wifi = self.route(("wlan0", "00000000", "0003", 0))
        mobile = self.route(("rmnet_data3", "00000000", "0003", 0))
        both = self.route(("rmnet_data3", "00000000", "0003", 10), ("wlan0", "00000000", "0003", 0),
                          ("rmnet_data3", "7898580A", "0007", 0))
        down = self.route(("wlan0", "00000000", "0002", 0))
        self.assertTrue(autobackup.on_wifi(wifi))
        self.assertFalse(autobackup.on_wifi(mobile))
        self.assertTrue(autobackup.on_wifi(both))
        self.assertIsNone(autobackup.on_wifi(down))
        self.assertIsNone(autobackup.on_wifi(self.route()))
        self.assertIsNone(autobackup.on_wifi("/nonexistent"))
        power = os.path.join(self.tmp.name, "power")
        self.write("power/ac/type", "Mains\n")
        self.write("power/battery/type", "Battery\n")
        self.write("power/battery/capacity", "15\n")
        self.write("power/battery/status", "Discharging\n")
        self.assertTrue(autobackup.battery_low(power))
        self.write("power/battery/status", "Charging\n")
        self.assertFalse(autobackup.battery_low(power))
        self.write("power/battery/capacity", "80\n")
        self.write("power/battery/status", "Discharging\n")
        self.assertFalse(autobackup.battery_low(power))
        self.assertFalse(autobackup.battery_low("/nonexistent"))

    def test_runs(self):
        import fcntl
        import autobackup
        self.addCleanup(setattr, backend, "send", backend.send)
        self.addCleanup(setattr, backend, "UPLOAD_LOCK_WAIT", True)
        self.addCleanup(os.environ.__setitem__, "HOME", os.environ.get("HOME", "/root"))
        os.environ["HOME"] = self.tmp.name
        folder = backend.data_dir
        backend._db.close()
        os.remove(os.path.join(folder, "uploads.db"))
        backend.init(folder)                         # the camera folders as sources
        self.sign_in()
        backend.create_album("Phone")
        backend.wait()
        camera = os.path.join(self.tmp.name, "Pictures", "Camera")
        os.makedirs(camera)
        for name in ("a.jpg", "b.jpg"):
            make_jpeg(os.path.join(camera, name), (320, 240), exif=False)
        wifi = self.route(("wlan0", "00000000", "0003", 0))
        mobile = self.route(("rmnet_data3", "00000000", "0003", 0))
        power = os.path.join(self.tmp.name, "nopower")
        status_file = os.path.join(folder, "auto-status.json")

        # switched off: nothing at all
        self.assertIsNone(autobackup.run(folder, wifi, power))
        self.assertFalse(os.path.exists(status_file))
        self.assertEqual(self.account.files, [])

        backend.set_auto({"on": True})
        self.assertEqual(backend.auto_settings(), {"on": True, "wifiOnly": True, "videos": True})
        status = autobackup.run(folder, mobile, power)
        self.assertEqual((status["ran"], status["reason"]), (False, "Waiting for Wi-Fi"))
        self.assertEqual(self.account.files, [])

        # the app is uploading: this run gives way
        with open(os.path.join(folder, "upload.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            status = autobackup.run(folder, wifi, power)
            self.assertEqual((status["ran"], status["reason"]),
                             (False, "The app is uploading right now"))
        self.assertEqual(self.account.files, [])

        status = autobackup.run(folder, wifi, power)
        self.assertEqual((status["ran"], status["uploaded"], status["failed"], status["reason"]),
                         (True, 2, 0, ""))
        self.assertEqual(len(self.account.files), 2)
        self.assertEqual(backend.auto_status()["uploaded"], 2)
        self.assertEqual(backend.state()["autoLast"]["uploaded"], 2)
        self.assertTrue(backend.state()["auto"]["on"])
        status = autobackup.run(folder, wifi, power)          # nothing new
        self.assertEqual((status["ran"], status["uploaded"]), (True, 0))

        # on mobile data when allowed
        backend.set_auto({"wifiOnly": False})
        make_jpeg(os.path.join(camera, "c.jpg"), (320, 240), exif=False)
        self.assertEqual(autobackup.run(folder, mobile, power)["uploaded"], 1)

    def test_app_waits_for_a_run(self):
        """While a backup run uploads, the app's uploads wait instead of failing."""
        import fcntl
        import threading
        import time
        self.sign_in()
        backend.create_album("Phone")
        backend.wait()
        photo = os.path.join(self.photos, "a.jpg")
        make_jpeg(photo, (320, 240), exif=False)
        held = open(os.path.join(backend.data_dir, "upload.lock"), "a")
        fcntl.flock(held, fcntl.LOCK_EX)
        threading.Timer(0.6, held.close).start()
        start = time.time()
        backend.upload([photo])
        backend.wait()
        self.assertGreater(time.time() - start, 0.5)
        self.assertIn("wait", [e[1]["stage"] for e in backend.events if e[0] == "upload"])
        self.assertEqual(self.last("result")[1]["status"], "done")


class Looking(Waiting):
    """Memories, duplicates, big tiles, freeing up the phone."""

    def ids(self, rows):
        return [int(c.split(":")[0]) for r in rows if r["t"] == "r"
                for c in r["cells"].split(",")]

    def final(self, name, number, seconds=20):
        import time
        end = time.time() + seconds
        while time.time() < end:
            found = [e[2] for e in backend.events if e[0] == name and e[1] == number
                     and "progress" not in e[2]]
            if found:
                return found[-1]
            time.sleep(0.05)
        self.fail("no %s event" % name)

    def test_memories(self):
        import time
        acc = self.account
        self.sign_in()
        backend.create_album("A")
        backend.wait()
        a = acc.collections[-1]["id"]

        def at(y, m, d, h=10):
            return int(time.mktime((y, m, d, h, 0, 0, 0, 0, -1)))
        now = at(2026, 11, 12, 12)
        month = acc.add_file(a, b"1", "m.jpg", taken=at(2026, 10, 14))    # 2 days off
        far = acc.add_file(a, b"2", "far.jpg", taken=at(2026, 8, 1))       # no 3 months
        half = acc.add_file(a, b"3", "h.jpg", taken=at(2026, 5, 12))
        year = acc.add_file(a, b"4", "y.jpg", taken=at(2024, 11, 12))
        later = acc.add_file(a, b"5", "l.jpg", taken=at(2024, 11, 20))
        cloud_ = backend._cloud
        cloud_.sync(backend._master(), 7, backend._keypair())
        rows = cloud_.memories(4, now)
        heads = [(r["kind"], r["ago"], r["n"]) for r in rows if r["t"] == "d"]
        self.assertEqual(heads, [("months", 1, 1), ("months", 6, 1), ("years", 2, 1),
                                 ("best", 2, 2)])
        self.assertEqual(self.ids(rows), [month, half, year, year, later])
        self.assertNotIn(far, self.ids(rows))
        best = [r for r in rows if r["t"] == "d"][-1]
        self.assertEqual(best["of"], 2)
        self.assertEqual(time.localtime(best["day"])[:3], (2024, 11, 1))

        # best of: favorites first, then spread over the month, at most BEST_COUNT
        for day in range(1, 29):
            acc.add_file(a, b"n%d" % day, "n%d.jpg" % day, taken=at(2023, 11, day))
        cloud_.sync(backend._master(), 7, backend._keypair())
        liked = [f["id"] for f in acc.files if f["id"] > later][20]
        cloud_.set_favorite([liked], True, backend._master())
        cloud_.sync(backend._master(), 7, backend._keypair())
        rows = cloud_.memories(4, now)
        heads = [r for r in rows if r["t"] == "d" and r["kind"] == "best" and r["ago"] == 3]
        self.assertEqual((heads[0]["n"], heads[0]["of"]), (cloud.BEST_COUNT, 28))
        start = rows.index(heads[0])
        shown = []
        for r in rows[start + 1:]:
            if r["t"] != "r":
                break
            shown += [int(c.split(":")[0]) for c in r["cells"].split(",")]
        self.assertIn(liked, shown)
        self.assertIn("%d:0:1" % liked, ",".join(r.get("cells", "") for r in rows))

        # title pictures: one per memory, in the same order; the favorite is the cover
        covers = cloud_.memory_covers(now)
        self.assertEqual([(c["kind"], c["ago"]) for c in covers],
                         [(r["kind"], r["ago"]) for r in rows if r["t"] == "d"])
        best3 = [c for c in covers if c["kind"] == "best" and c["ago"] == 3][0]
        self.assertEqual((best3["cover"], best3["n"], best3["of"]), (liked, cloud.BEST_COUNT, 28))
        self.assertEqual(covers[0]["cover"], month)
        self.assertIn(liked, cloud_._big)           # covers get the sharper thumbnail
        # one memory alone
        place = covers.index(best3)
        alone = cloud_.memories(4, now, only=place)
        self.assertEqual([(r["kind"], r["ago"]) for r in alone if r["t"] == "d"], [("best", 3)])
        self.assertEqual(len(self.ids(alone)), cloud.BEST_COUNT)

    def test_big_tiles(self):
        items = [(i, 100, 0, 0, 0) for i in range(1, 8)]
        items[2] = (3, 100, 1, 1920, 1080)          # a landscape video
        rows = cloud._cell_rows(items, 4, set(), True)
        cells = [c.split(":") for r in rows for c in r["cells"].split(",")]
        # all there; the small photos after the video fill the gap beside it
        self.assertEqual(sorted(int(c[0]) for c in cells), list(range(1, 8)))
        self.assertEqual([int(c[0]) for c in cells], [1, 2, 4, 5, 3, 6, 7])
        video = [c for c in cells if c[0] == "3"][0]
        self.assertEqual(video[5:], ["3", "2"])
        for r in rows:                              # every tile inside its band
            for c in r["cells"].split(","):
                x, y, w, h = [int(v) for v in c.split(":")[3:]]
                self.assertLessEqual(x + w, 4)
                self.assertLessEqual(y + h, r["h"])
        # upright video: 2 x 3; a photo of unknown size: 2 x 2; small days: no big tiles
        items[2] = (3, 100, 1, 1080, 1920)
        cells = [c.split(":") for r in cloud._cell_rows(items, 4, set(), True)
                 for c in r["cells"].split(",")]
        self.assertEqual([c for c in cells if c[0] == "3"][0][5:], ["2", "3"])
        photos = [(i, 100, 0, 0, 0) for i in range(1, 9)]
        cells = [c.split(":") for r in cloud._cell_rows(photos, 4, {5}, True)
                 for c in r["cells"].split(",")]
        self.assertEqual([c for c in cells if c[0] == "5"][0][5:], ["2", "2"])  # the favorite
        self.assertEqual(sum(1 for c in cells if c[5] != "1"), 1)
        few = cloud._cell_rows(items[:3], 4, set(), True)
        self.assertTrue(all(c.endswith(":1:1") for r in few for c in r["cells"].split(",")))
        flat = cloud._cell_rows(items, 4, set(), False)
        self.assertTrue(all(r["h"] == 1 for r in flat))

    def test_duplicates(self):
        acc = self.account
        self.sign_in()
        backend.create_album("A")
        backend.wait()
        a = acc.collections[-1]["id"]
        backend.create_album("B", False)
        backend.wait()
        b = acc.collections[-1]["id"]
        first = acc.add_file(a, b"same", "s1.jpg", taken=1500000001)
        second = acc.add_file(a, b"same", "s2.jpg", taken=1500000001)
        third = acc.add_file(b, b"same", "s3.jpg", taken=1500000001)
        single = acc.add_file(a, b"other", "o.jpg", taken=1500000002)
        cloud_ = backend._cloud
        master = backend._master()
        cloud_.sync(master, 7, backend._keypair())
        self.assertEqual(cloud_.duplicate_groups(), [[first, second, third]])
        rows = cloud_.duplicates(4)
        self.assertEqual(rows[0]["kind"], "dup")
        self.assertEqual(rows[0]["n"], 3)
        self.assertEqual(self.ids(rows), [first, second, third])

        backend.events.clear()
        backend.cloud_trash_duplicates([second, third, single])
        result = self.final("acted", 0)
        self.assertEqual((result["what"], result["done"], result["skipped"]), ("dedupe", 2, 1))
        # the copy that stays joined album B, so B still shows the photo
        self.assertEqual(self.ids(cloud_.rows(b)), [first])
        self.assertEqual(sorted(self.ids(cloud_.rows(a))), sorted([first, single]))
        self.assertEqual(cloud_.duplicate_groups(), [])
        # all of a group chosen: the first stays
        x = acc.add_file(a, b"twice", "x1.jpg")
        y = acc.add_file(a, b"twice", "x2.jpg")
        cloud_.sync(master, 7, backend._keypair())
        self.assertEqual(cloud_.trash_duplicates([x, y]), {"done": 1, "skipped": 1})

    def test_free_up_phone(self):
        import time
        acc = self.account
        self.sign_in()
        backend.create_album("A")
        backend.wait()
        a = acc.collections[-1]["id"]
        backend.add_source(self.photos)
        old = time.time() - 40 * 86400
        files = {}
        for name in ("inente.jpg", "notinente.jpg", "recent.jpg"):
            path = os.path.join(self.photos, name)
            make_jpeg(path, (64, 48), exif=False)
            files[name] = path
        with open(files["inente.jpg"], "rb") as source:
            acc.add_file(a, source.read(), "inente.jpg")
        with open(files["recent.jpg"], "rb") as source:
            acc.add_file(a, source.read(), "recent.jpg")
        for name in ("inente.jpg", "notinente.jpg"):
            os.utime(files[name], (old, old))

        backend.events.clear()
        backend.cleanup_scan(30)
        found = self.final("cleanup", 0)
        self.assertEqual(found.get("error"), "")
        self.assertEqual([f["name"] for f in found["files"]], ["inente.jpg"])
        self.assertEqual(found["files"][0]["album"], "A")
        self.assertEqual((found["checked"], found["notInEnte"]), (2, 1))
        self.assertEqual(found["bytes"], os.path.getsize(files["inente.jpg"]))

        # deleting checks again: a file changed since stays, as does one not in Ente
        backend.events.clear()
        backend.cleanup_delete([files["inente.jpg"], files["notinente.jpg"]])
        done = self.final("cleanup", 1)
        self.assertEqual((done["done"], done["kept"]), (1, 1))
        self.assertFalse(os.path.exists(files["inente.jpg"]))
        self.assertTrue(os.path.exists(files["notinente.jpg"]))
        self.assertTrue(os.path.exists(files["recent.jpg"]))

        # gone from Ente meanwhile (trash): the phone's copy stays
        os.utime(files["recent.jpg"], (old, old))
        backend.events.clear()
        backend.cleanup_scan(30)
        self.assertEqual([f["name"] for f in self.final("cleanup", 0)["files"]], ["recent.jpg"])
        recent = [f["id"] for f in acc.files if f["id"] and not f["isDeleted"]][-1]
        acc.delete_file(recent, a)
        backend.events.clear()
        backend.cleanup_delete([files["recent.jpg"]])
        self.assertEqual(self.final("cleanup", 1)["kept"], 1)
        self.assertTrue(os.path.exists(files["recent.jpg"]))


class Actions(Waiting):
    def wait_event(self, name, file_id, seconds=15):
        import time
        end = time.time() + seconds
        while time.time() < end:
            found = [e for e in backend.events if e[0] == name and e[1] == file_id]
            if found:
                return found[-1][2]
            time.sleep(0.05)
        self.fail("no %s event" % name)

    def test_save_share_trash(self):
        import io
        import zipfile
        from PIL import Image
        acc = self.account
        home = os.path.join(self.tmp.name, "home")
        self.addCleanup(os.environ.__setitem__, "HOME", os.environ.get("HOME", "/root"))
        os.environ["HOME"] = home
        self.sign_in()
        backend.create_album("Mine")
        backend.wait()
        mine = acc.collections[-1]["id"]
        backend.create_album("Second", False)
        backend.wait()
        second = acc.collections[-1]["id"]
        jpeg = io.BytesIO()
        Image.new("RGB", (64, 48), "red").save(jpeg, "JPEG")
        photo = acc.add_file(mine, jpeg.getvalue(), "IMG_7.jpg", taken=1500000000)
        heic_data = b"\x00\x00\x00\x18ftypheic" + os.urandom(200)
        heic = acc.add_file(mine, heic_data, "IMG_8.HEIC", taken=1500000100)
        clip = os.urandom(3000)
        video = acc.add_file(mine, clip, "VID_9.mp4", kind=1, taken=1500000200)
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w") as z:
            z.writestr("IMG_10.HEIC", heic_data)
            z.writestr("IMG_10.MOV", b"motion")
        live = acc.add_file(mine, packed.getvalue(), "IMG_10.zip", kind=2, taken=1500000300)
        # the same photo in a second album (Ente keeps one file in both)
        twice = acc.add_file(mine, jpeg.getvalue(), "both.jpg", taken=1500000400)
        copy = [dict(f) for f in acc.files if f["id"] == twice][0]
        copy.update(collectionID=second, updationTime=acc.tick(second))
        acc.files.append(copy)
        foreign = acc.add_shared("Theirs")
        theirs = acc.add_file(foreign, jpeg.getvalue(), "theirs.jpg")
        cloud_ = backend._cloud
        cloud_.sync(backend._master(), 7, backend._keypair())

        pictures = os.path.join(home, "Pictures", "Ente")
        videos = os.path.join(home, "Videos", "Ente")
        backend.cloud_save(photo)
        saved = self.wait_event("saved", photo)
        self.assertEqual(saved["error"], "")
        self.assertEqual(saved["paths"], [os.path.join(pictures, "IMG_7.jpg")])
        self.assertEqual(saved["folder"], os.path.join("Pictures", "Ente"))
        with open(saved["paths"][0], "rb") as source:
            self.assertEqual(source.read(), jpeg.getvalue())
        self.assertEqual(int(os.stat(saved["paths"][0]).st_mtime), 1500000000)
        backend.events.clear()
        backend.cloud_save(photo, True)                 # share: saved again, not overwritten
        again = self.wait_event("saved", photo)
        self.assertEqual(again["paths"], [os.path.join(pictures, "IMG_7 (2).jpg")])
        self.assertTrue(again["share"])
        # originals, not previews: HEIC as it is, videos, both parts of a live photo
        def save(file_id):
            backend.events.clear()
            backend.cloud_save(file_id)
            result = self.wait_event("saved", file_id)
            self.assertEqual(result["error"], "")
            return result["paths"]

        self.assertEqual(save(heic), [os.path.join(pictures, "IMG_8.HEIC")])
        with open(os.path.join(pictures, "IMG_8.HEIC"), "rb") as source:
            self.assertEqual(source.read(), heic_data)
        self.assertEqual(save(video), [os.path.join(videos, "VID_9.mp4")])
        cloud_._make_full(video)                        # played before: the kept copy is used
        self.assertEqual(save(video), [os.path.join(videos, "VID_9 (2).mp4")])
        self.assertEqual(save(live), [os.path.join(pictures, "IMG_10.heic"),
                                      os.path.join(videos, "IMG_10.mov")])
        with open(os.path.join(videos, "IMG_10.mov"), "rb") as source:
            self.assertEqual(source.read(), b"motion")
        self.assertFalse([n for n in os.listdir(cloud_.full_dir) if n.endswith("part")])
        # saved copies are never uploaded back
        backend.set_album(mine, "Mine")
        self.assertEqual(backend.scan([pictures, videos]), 0)
        with open(os.path.join(pictures, "IMG_7.jpg"), "ab") as out:
            out.write(b"edited")                         # changed on the phone: a new photo
        self.assertEqual(backend.scan([pictures, videos]), 1)
        backend.cancel()
        backend.wait()

        # the trash: one request item per file, and it leaves every album
        self.assertTrue(cloud_.details(twice)["own"])
        self.assertFalse(cloud_.details(theirs)["own"])
        backend.events.clear()
        backend.cloud_trash(twice)
        self.assertEqual(self.wait_event("trashed", twice), {"error": ""})
        self.assertEqual(len(acc.trash_requests), 1)
        self.assertEqual(len(acc.trash_requests[0]), 1)
        self.assertEqual(acc.trash_requests[0][0]["fileID"], twice)
        self.assertEqual(cloud_.details(twice), {})
        cloud_.sync(backend._master(), 7, backend._keypair())
        self.assertEqual(cloud_.details(twice), {})
        self.assertNotIn(twice, [int(c.split(":")[0]) for a in (mine, second)
                                 for r in cloud_.rows(a) if r["t"] == "r"
                                 for c in r["cells"].split(",")])
        backend.cloud_trash(theirs)
        self.assertIn("own albums", self.wait_event("trashed", theirs)["error"])
        self.assertEqual(len(acc.trash_requests), 1)

        # several at once: each file once, in batches, shared ones left alone
        import cloud as cloud_module
        self.addCleanup(setattr, cloud_module.Cloud, "TRASH_BATCH", 1000)
        cloud_module.Cloud.TRASH_BATCH = 2
        backend.events.clear()
        backend.cloud_trash_many([photo, heic, photo, video, theirs])
        result = self.wait_event("trashed", 0)
        self.assertEqual(result, {"done": 3, "skipped": 1, "error": ""})
        self.assertEqual([len(r) for r in acc.trash_requests[1:]], [2, 1])
        self.assertEqual(sorted(i["fileID"] for r in acc.trash_requests[1:] for i in r),
                         sorted([photo, heic, video]))
        for gone in (photo, heic, video):
            self.assertEqual(cloud_.details(gone), {})
        self.assertTrue(cloud_.details(theirs))


if __name__ == "__main__":
    unittest.main()
