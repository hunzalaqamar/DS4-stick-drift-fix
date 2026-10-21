"""Draws the app icon (assets/icon.ico) with no image libraries: an indigo
tile, the stick gate ring, the drifting amber dot and the fixed mint centre."""

import math
import os
import struct
import zlib

BG = (18, 22, 51)
RING = (78, 90, 170)
RAW = (242, 163, 58)
FIXED = (95, 227, 191)
INK = (232, 235, 255)


def render(size, ss=4):
    n = size * ss
    px = [[(0, 0, 0, 0)] * n for _ in range(n)]
    c = n / 2
    radius_tile = n * 0.22

    def inside_tile(x, y):
        # rounded square
        m = n * 0.04
        lo, hi = m, n - m
        cx = min(max(x, lo + radius_tile), hi - radius_tile)
        cy = min(max(y, lo + radius_tile), hi - radius_tile)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius_tile ** 2 and lo <= x <= hi and lo <= y <= hi

    ring_r, ring_w = n * 0.33, max(n * 0.055, 2)
    raw_c, raw_r = (c - n * 0.2, c - n * 0.11), n * 0.085
    fix_c, fix_r = (c, c), n * 0.07
    for y in range(n):
        for x in range(n):
            px_x, px_y = x + 0.5, y + 0.5
            if not inside_tile(px_x, px_y):
                continue
            col = BG
            d = math.hypot(px_x - c, px_y - c)
            if abs(d - ring_r) <= ring_w / 2:
                col = RING
            # arrow from the raw dot toward the centre
            ax, ay = raw_c
            t = ((px_x - ax) * (c - ax) + (px_y - ay) * (c - ay)) / ((c - ax) ** 2 + (c - ay) ** 2)
            if 0.35 < t < 0.72:
                qx, qy = ax + t * (c - ax), ay + t * (c - ay)
                if math.hypot(px_x - qx, px_y - qy) <= n * 0.022:
                    col = INK
            if math.hypot(px_x - raw_c[0], px_y - raw_c[1]) <= raw_r:
                col = RAW
            if math.hypot(px_x - fix_c[0], px_y - fix_c[1]) <= fix_r:
                col = FIXED
            px[y][x] = col + (255,)
    out = bytearray()
    for y in range(size):
        out.append(0)
        for x in range(size):
            acc = [0, 0, 0, 0]
            for yy in range(ss):
                for xx in range(ss):
                    p = px[y * ss + yy][x * ss + xx]
                    a = p[3]
                    acc[0] += p[0] * a
                    acc[1] += p[1] * a
                    acc[2] += p[2] * a
                    acc[3] += a
            a = acc[3]
            out += bytes([round(acc[0] / a) if a else 0, round(acc[1] / a) if a else 0,
                          round(acc[2] / a) if a else 0, round(a / (ss * ss))])
    return png(size, bytes(out))


def png(size, raw):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [render(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, img in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "icon.ico"), "wb") as f:
        f.write(header + entries + b"".join(images))
    with open(os.path.join(root, "icon.png"), "wb") as f:
        f.write(images[-1])
    print("wrote assets/icon.ico and assets/icon.png")


if __name__ == "__main__":
    main()
