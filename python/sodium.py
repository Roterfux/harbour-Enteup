# Enteup - SPDX-License-Identifier: GPL-3.0-or-later
"""The few libsodium functions Ente's encryption needs, called through ctypes.

The library is loaded at the first use, so a phone without libsodium gets a clear
message (SodiumError) instead of a failed import.
"""
import ctypes
import ctypes.util
import os

KEY_BYTES = 32
NONCE_BYTES = 24              # secretbox
HEADER_BYTES = 24             # secretstream
STREAM_OVERHEAD = 17          # added to every secretstream chunk
SALT_BYTES = 16
SEAL_BYTES = 48
TAG_MESSAGE = 0
TAG_FINAL = 3
ALG_ARGON2ID13 = 2

_NAMES = ("libsodium.so.26", "libsodium.so.23", "libsodium.so.18", "libsodium.so")
_lib = None

_ull = ctypes.c_ulonglong
_buf = ctypes.c_char_p


class SodiumError(Exception):
    pass


def _find():
    names = list(_NAMES)
    try:
        found = ctypes.util.find_library("sodium")
        if found:
            names.insert(0, found)
    except Exception:
        pass
    for folder in ("/usr/lib64", "/usr/lib"):
        try:
            names += sorted(os.path.join(folder, n) for n in os.listdir(folder)
                            if n.startswith("libsodium.so"))
        except OSError:
            pass
    problem = ""
    for name in names:
        try:
            return ctypes.CDLL(name)
        except OSError as err:
            problem = str(err)
    raise SodiumError("libsodium is not installed (%s)" % problem)


def lib():
    global _lib
    if _lib is None:
        so = _find()
        if so.sodium_init() < 0:
            raise SodiumError("libsodium could not start")
        so.crypto_pwhash.argtypes = [_buf, _ull, _buf, _ull, _buf, _ull,
                                     ctypes.c_size_t, ctypes.c_int]
        so.crypto_secretbox_easy.argtypes = [_buf, _buf, _ull, _buf, _buf]
        so.crypto_secretbox_open_easy.argtypes = [_buf, _buf, _ull, _buf, _buf]
        so.crypto_box_seal.argtypes = [_buf, _buf, _ull, _buf]
        so.crypto_box_seal_open.argtypes = [_buf, _buf, _ull, _buf, _buf]
        so.crypto_box_keypair.argtypes = [_buf, _buf]
        so.randombytes_buf.argtypes = [_buf, ctypes.c_size_t]
        so.randombytes_buf.restype = None
        so.crypto_secretstream_xchacha20poly1305_statebytes.restype = ctypes.c_size_t
        so.crypto_secretstream_xchacha20poly1305_init_push.argtypes = [_buf, _buf, _buf]
        so.crypto_secretstream_xchacha20poly1305_init_pull.argtypes = [_buf, _buf, _buf]
        so.crypto_secretstream_xchacha20poly1305_push.argtypes = [
            _buf, _buf, ctypes.POINTER(_ull), _buf, _ull, _buf, _ull, ctypes.c_ubyte]
        so.crypto_secretstream_xchacha20poly1305_pull.argtypes = [
            _buf, _buf, ctypes.POINTER(_ull), ctypes.POINTER(ctypes.c_ubyte),
            _buf, _ull, _buf, _ull]
        _lib = so
    return _lib


def available():
    """"" when libsodium works, otherwise why not."""
    try:
        lib()
        return ""
    except SodiumError as err:
        return str(err)


def random(count):
    out = ctypes.create_string_buffer(count)
    lib().randombytes_buf(out, count)
    return out.raw


def pwhash(password, salt, ops_limit, mem_limit, length=KEY_BYTES):
    """Argon2id key derivation (Ente's key encryption key from the password)."""
    if len(salt) != SALT_BYTES:
        raise SodiumError("Unexpected salt length %d" % len(salt))
    data = password.encode("utf-8")
    out = ctypes.create_string_buffer(length)
    if lib().crypto_pwhash(out, length, data, len(data), salt, int(ops_limit),
                           int(mem_limit), ALG_ARGON2ID13) != 0:
        raise SodiumError("Key derivation failed: not enough memory (%d MB needed)"
                          % (int(mem_limit) // (1024 * 1024)))
    return out.raw


def secretbox(message, key):
    """(ciphertext, nonce)"""
    nonce = random(NONCE_BYTES)
    out = ctypes.create_string_buffer(len(message) + 16)
    lib().crypto_secretbox_easy(out, message, len(message), nonce, key)
    return out.raw, nonce


def secretbox_open(cipher, nonce, key):
    if len(cipher) < 16 or len(nonce) != NONCE_BYTES or len(key) != KEY_BYTES:
        raise SodiumError("Malformed encrypted key")
    out = ctypes.create_string_buffer(len(cipher) - 16)
    if lib().crypto_secretbox_open_easy(out, cipher, len(cipher), nonce, key) != 0:
        raise SodiumError("Decryption failed")
    return out.raw


def box_keypair():
    public = ctypes.create_string_buffer(32)
    secret = ctypes.create_string_buffer(32)
    lib().crypto_box_keypair(public, secret)
    return public.raw, secret.raw


def seal(message, public):
    out = ctypes.create_string_buffer(len(message) + SEAL_BYTES)
    lib().crypto_box_seal(out, message, len(message), public)
    return out.raw


def seal_open(cipher, public, secret):
    if len(cipher) < SEAL_BYTES:
        raise SodiumError("Malformed sealed data")
    out = ctypes.create_string_buffer(len(cipher) - SEAL_BYTES)
    if lib().crypto_box_seal_open(out, cipher, len(cipher), public, secret) != 0:
        raise SodiumError("Decryption failed")
    return out.raw


class StreamEncryptor:
    """secretstream (XChaCha20-Poly1305): .header, then push() chunk by chunk."""

    def __init__(self, key):
        so = lib()
        self._state = ctypes.create_string_buffer(
            so.crypto_secretstream_xchacha20poly1305_statebytes())
        header = ctypes.create_string_buffer(HEADER_BYTES)
        so.crypto_secretstream_xchacha20poly1305_init_push(self._state, header, key)
        self.header = header.raw

    def push(self, chunk, final):
        out = ctypes.create_string_buffer(len(chunk) + STREAM_OVERHEAD)
        lib().crypto_secretstream_xchacha20poly1305_push(
            self._state, out, None, chunk, len(chunk), None, 0,
            TAG_FINAL if final else TAG_MESSAGE)
        return out.raw


class StreamDecryptor:
    def __init__(self, header, key):
        so = lib()
        self._state = ctypes.create_string_buffer(
            so.crypto_secretstream_xchacha20poly1305_statebytes())
        if so.crypto_secretstream_xchacha20poly1305_init_pull(self._state, header, key) != 0:
            raise SodiumError("Bad stream header")

    def pull(self, chunk):
        """(plaintext, is final)"""
        out = ctypes.create_string_buffer(max(len(chunk) - STREAM_OVERHEAD, 1))
        tag = ctypes.c_ubyte(0)
        if lib().crypto_secretstream_xchacha20poly1305_pull(
                self._state, out, None, ctypes.byref(tag), chunk, len(chunk), None, 0) != 0:
            raise SodiumError("Decryption failed")
        return out.raw[:len(chunk) - STREAM_OVERHEAD], tag.value == TAG_FINAL


def blob_encrypt(data, key):
    """Ente's "blob": a stream of one final chunk. (ciphertext, header)"""
    stream = StreamEncryptor(key)
    return stream.push(data, True), stream.header


def blob_decrypt(cipher, header, key):
    return StreamDecryptor(header, key).pull(cipher)[0]
