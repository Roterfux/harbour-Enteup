"""Render the launcher icon at the Sailfish sizes (standard library only; development only).

    python3 tools/make_icons.py

A photo (mountains and sun) on a rounded square, with a round upload badge: the app both
browses the photos and backs them up.
"""
import math
import os
import struct
import zlib

SIZES = (86, 108, 128, 172)
SAMPLES = 4                                  # 4 x 4 samples per pixel for smooth edges
TOP, BOTTOM = (0x3A, 0xC8, 0xB0), (0x1F, 0x55, 0xC4)     # background: teal to blue
CORNER = 0.225                               # corner radius of the rounded square
WHITE = (255, 255, 255)
SKY = (0xE3, 0xF3, 0xFA)
FAR, NEAR = (0x4F, 0xB8, 0xD8), (0x1F, 0x6F, 0xC8)       # the two mountains
SUN = (0xFF, 0xC4, 0x3D)
BADGE = (0xFF, 0x7A, 0x3D)

CARD = (0.15, 0.20, 0.75, 0.70, 0.06)        # left, top, right, bottom, corner radius
PHOTO = (0.195, 0.245, 0.705, 0.655, 0.025)
BADGE_C, BADGE_R, RING = (0.715, 0.705), 0.175, 0.03


def rounded(x, y, box):
    left, top, right, bottom, r = box
    if not (left <= x <= right and top <= y <= bottom):
        return False
    cx = min(max(x, left + r), right - r)
    cy = min(max(y, top + r), bottom - r)
    return math.hypot(x - cx, y - cy) <= r


def peak(x, y, px, py, slope):
    return y >= py + abs(x - px) * slope


def colour(x, y):
    """(r, g, b) at a point inside the rounded square."""
    bx, by = BADGE_C
    d = math.hypot(x - bx, y - by)
    if d <= BADGE_R:
        if d > BADGE_R - RING:
            return WHITE
        head = 0.615 <= y <= 0.715 and abs(x - bx) <= 0.08 * (y - 0.615) / 0.10
        stem = 0.715 <= y <= 0.795 and abs(x - bx) <= 0.03
        return WHITE if head or stem else BADGE
    if rounded(x, y, PHOTO):
        if peak(x, y, 0.36, 0.38, 1.05):
            return NEAR
        if peak(x, y, 0.55, 0.44, 0.9):
            return FAR
        if math.hypot(x - 0.57, y - 0.34) <= 0.05:
            return SUN
        return SKY
    if rounded(x, y, CARD):
        return WHITE
    t = x * 0.35 + y * 0.65
    return tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))


def sample(x, y):
    """(r, g, b, a) at a point of the unit square."""
    cx = min(max(x, CORNER), 1 - CORNER)
    cy = min(max(y, CORNER), 1 - CORNER)
    if math.hypot(x - cx, y - cy) > CORNER:
        return 0, 0, 0, 0
    return colour(x, y) + (255,)


def render(size):
    rows = []
    n = SAMPLES * SAMPLES
    for py in range(size):
        row = bytearray([0])                 # PNG filter type "none"
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    r, g, b, a = sample((px + (sx + 0.5) / SAMPLES) / size,
                                        (py + (sy + 0.5) / SAMPLES) / size)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            alpha = acc[3]
            row += bytes((acc[0] // alpha, acc[1] // alpha, acc[2] // alpha, alpha // n)
                         if alpha else (0, 0, 0, 0))
        rows.append(bytes(row))
    return b"".join(rows)


def chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def png(size):
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(render(size), 9))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "icons")
    for s in SIZES:
        folder = os.path.join(root, "%dx%d" % (s, s))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "harbour-enteup.png"), "wb") as handle:
            handle.write(png(s))
        print("wrote %dx%d" % (s, s))
