"""Pull an app icon out of an IPA as a normal PNG.

iOS bundles store icons as Apple "CgBI" PNGs (raw deflate, BGRA,
premultiplied alpha) that browsers can't show, so those get converted.
Icons that only live inside Assets.car can't be read; then the largest
loose icon PNG in the app folder is used instead. Stdlib only.
"""
import re
import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunks(data):
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        yield ctype, data[pos + 8:pos + 8 + length]
        pos += 12 + length


def _chunk(ctype, body):
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))


def _unfilter(raw, width, height, bpp=4):
    stride = width * bpp
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        out += line
        prev = line
    return out


def uncrush(data):
    """Return a standard PNG; non-CgBI PNGs come back unchanged."""
    if not data.startswith(PNG_SIG):
        raise ValueError("not a PNG")
    chunks = list(_chunks(data))
    if not any(t == b"CgBI" for t, _ in chunks):
        return data
    ihdr = next(b for t, b in chunks if t == b"IHDR")
    width, height, depth, ctype = struct.unpack(">IIBB", ihdr[:10])
    if depth != 8 or ctype != 6:
        raise ValueError("unsupported CgBI layout")
    raw = zlib.decompress(b"".join(b for t, b in chunks if t == b"IDAT"), -15)
    px = _unfilter(raw, width, height)
    for i in range(0, len(px), 4):
        b, g, r, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        if a and a < 255:
            r, g, b = (min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a))
        px[i], px[i + 1], px[i + 2] = r, g, b
    stride = width * 4
    rows = b"".join(b"\x00" + bytes(px[y * stride:(y + 1) * stride]) for y in range(height))
    return PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(rows, 9)) + _chunk(b"IEND", b"")


def _png_size(data):
    for t, b in _chunks(data):
        if t == b"IHDR":
            return struct.unpack(">II", b[:8])
    return (0, 0)


def extract_icon(zf, info):
    """Best icon in an open IPA ZipFile as standard PNG bytes, or None."""
    app_dir = next(
        (m.group(1) for n in zf.namelist() if (m := re.match(r"^(Payload/[^/]+\.app/)Info\.plist$", n))),
        None,
    )
    if not app_dir:
        return None
    root_pngs = [n for n in zf.namelist()
                 if n.startswith(app_dir) and "/" not in n[len(app_dir):] and n.lower().endswith(".png")]

    names = []
    for key in ("CFBundleIcons", "CFBundleIcons~ipad"):
        icons = info.get(key) or {}
        primary = icons.get("CFBundlePrimaryIcon") or {}
        names += primary.get("CFBundleIconFiles") or []
        if primary.get("CFBundleIconName"):
            names.append(primary["CFBundleIconName"])
        for alt in (icons.get("CFBundleAlternateIcons") or {}).values():
            names += alt.get("CFBundleIconFiles") or []
    names += info.get("CFBundleIconFiles") or []
    if info.get("CFBundleIconFile"):
        names.append(info["CFBundleIconFile"])

    base = lambda n: n[len(app_dir):]
    candidates = [n for n in root_pngs if any(base(n).startswith(nm) for nm in names)]
    if not candidates:
        candidates = [n for n in root_pngs if "icon" in base(n).lower()]

    best, best_px = None, 0
    for n in candidates:
        try:
            png = uncrush(zf.read(n))
        except (ValueError, zlib.error, struct.error):
            continue
        w, h = _png_size(png)
        if w == h and w > best_px:
            best, best_px = png, w
    return best
