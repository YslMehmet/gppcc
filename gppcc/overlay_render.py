"""High-quality Pillow renderer for the keyboard overlay.

Produces an RGBA image with anti-aliased, gradient-shaded donut segments and a
Cupertino-style wheel picker (neighbouring wheels shrink and fade toward the
edges). Only each wheel's donut *tile* is supersampled and LANCZOS-downscaled
(so the curved edges are smooth); text is drawn at native size (Pillow renders
it anti-aliased anyway) and the frame is composited at native resolution, which
keeps it fast. Wheel tiles are cached, since neighbours rarely change. Pure and
side-effect free apart from the tile cache, so it can be snapshotted in tests.
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

_FONT_CACHE: dict = {}
_TILE_CACHE: dict = {}
TILE_SS = 2          # per-tile supersample factor
SEPARATOR_PX = 2     # gap between donut segments, in final (display) pixels


def _gap_deg(r_out, half):
    """Angular gap giving a ~SEPARATOR_PX separator, in supersampled coords."""
    g = math.degrees((SEPARATOR_PX * TILE_SS) / max(1.0, r_out))
    return min(g, half * 0.6)


def _font(name: str, size: int):
    size = max(1, int(size))
    key = (name, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = []
    if name:
        slug = name.replace(" ", "").lower()
        candidates += [f"{slug}b.ttf", f"{slug}bd.ttf", f"{slug}.ttf", name]
    candidates += ["segoeuib.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]
    fontdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    font = None
    for cand in candidates:
        for path in (cand, os.path.join(fontdir, cand)):
            try:
                font = ImageFont.truetype(path, size)
                break
            except Exception:
                continue
        if font:
            break
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _rgb(hexstr: str):
    s = (hexstr or "#000000").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def _mix(a, b, f):
    return tuple(round(a[i] + (b[i] - a[i]) * f) for i in range(3))


def _lighten(c, f):
    return _mix(c, (255, 255, 255), f)


def _darken(c, f):
    return _mix(c, (0, 0, 0), f)


def _vgradient(size, top, bottom):
    w, h = size
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    for y in range(h):
        px[0, y] = _mix(top, bottom, y / max(1, h - 1))
    return strip.resize((w, h))


def _sector_points(cx, cy, r_in, r_out, a0, a1, steps=24):
    pts = []
    for s in range(steps + 1):
        a = math.radians(a0 + (a1 - a0) * s / steps)
        pts.append((cx + r_out * math.cos(a), cy + r_out * math.sin(a)))
    for s in range(steps + 1):
        a = math.radians(a1 + (a0 - a1) * s / steps)
        pts.append((cx + r_in * math.cos(a), cy + r_in * math.sin(a)))
    return pts


def _wheel_sig(wheel):
    return (wheel["name"], tuple((k.get("label"), k.get("key")) for k in wheel["keys"]))


def _theme_sig(theme):
    return tuple(sorted(theme.items()))


def _geom(side):
    """Supersampled donut geometry for a `side`-px tile."""
    SS = side * TILE_SS
    cx = cy = SS / 2
    r_out = SS / 2 * 0.86
    return SS, cx, cy, r_out, r_out * 0.56


def _label_for(k, shift_on):
    key = k.get("key") or ""
    label = k.get("label") or key or ""
    # Single letters follow the shift state so you can see Caps is on.
    if len(key) == 1 and key.isalpha():
        return key.upper() if shift_on else key.lower()
    if shift_on and len(label) == 1 and label.isalpha():
        return label.upper()
    return label


def _build_base(wheel, shift_on, theme, side):
    """The full donut with every segment unselected — supersampled, cached per wheel."""
    SS, cx, cy, r_out, r_in = _geom(side)
    r_mid = (r_in + r_out) / 2
    keys = wheel["keys"]
    n = max(1, len(keys))
    half = 180.0 / n
    gap = _gap_deg(r_out, half)
    key_c, key_edge = _rgb(theme["key_fill"]), _rgb(theme["key_outline"])

    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    seg_pts = [_sector_points(cx, cy, r_in, r_out, -90 + i * 360.0 / n - half + gap,
                              -90 + i * 360.0 / n + half - gap) for i in range(n)]
    mask = Image.new("L", (SS, SS), 0)
    md = ImageDraw.Draw(mask)
    for pts in seg_pts:
        md.polygon(pts, fill=255)
    img.paste(_vgradient((SS, SS), _lighten(key_c, 0.22), _darken(key_c, 0.16)), (0, 0), mask)

    draw = ImageDraw.Draw(img)
    lw = max(2, int(TILE_SS * 1.3))
    f = _rgb(theme["key_text"])
    for i, pts in enumerate(seg_pts):
        draw.line(pts + [pts[0]], fill=key_edge + (255,), width=lw, joint="curve")
        label = _label_for(keys[i], shift_on)
        fsize = (r_out * 0.30) if len(label) <= 2 else (r_out * 0.19)
        centre = math.radians(-90 + i * 360.0 / n)
        draw.text((cx + r_mid * math.cos(centre), cy + r_mid * math.sin(centre)),
                  label, font=_font(theme["font"], fsize), fill=f + (255,), anchor="mm")
    draw.text((cx, cy), wheel["name"], font=_font(theme["font"], r_out * 0.18),
              fill=_rgb(theme["label_text"]) + (235,), anchor="mm")
    return img   # kept at supersample; downscaled by the caller


def _build_selected(base_ss, wheel, slot, shift_on, flashing, theme, side):
    """Copy the supersampled base donut and paint just the selected segment."""
    SS, cx, cy, r_out, r_in = _geom(side)
    r_mid = (r_in + r_out) / 2
    n = max(1, len(wheel["keys"]))
    half = 180.0 / n
    gap = _gap_deg(r_out, half)
    base_c = _rgb(theme["flash_fill"]) if flashing else _rgb(theme["selected_fill"])
    edge_c = _lighten(base_c, 0.3) if flashing else _rgb(theme["selected_outline"])

    img = base_ss.copy()
    centre = -90 + slot * 360.0 / n
    pts = _sector_points(cx, cy, r_in, r_out, centre - half + gap, centre + half - gap)
    wide = _sector_points(cx, cy, r_in - TILE_SS * 3, r_out + TILE_SS * 3, centre - half, centre + half)

    xs = [p[0] for p in wide]; ys = [p[1] for p in wide]
    m = int(r_out * 0.10)
    x0, y0 = max(0, int(min(xs)) - m), max(0, int(min(ys)) - m)
    x1, y1 = min(SS, int(max(xs)) + m), min(SS, int(max(ys)) + m)
    cw, ch = x1 - x0, y1 - y0
    off = (x0, y0)

    # Glow (cropped + blurred).
    glow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon([(x - x0, y - y0) for x, y in wide], fill=base_c + (170,))
    glow = glow.filter(ImageFilter.GaussianBlur(r_out * 0.05))
    img.alpha_composite(glow, off)

    # Accent fill (cropped gradient through the segment mask).
    gmask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(gmask).polygon([(x - x0, y - y0) for x, y in pts], fill=255)
    fill = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    fill.paste(_vgradient((cw, ch), _lighten(base_c, 0.28), _darken(base_c, 0.12)), (0, 0), gmask)
    img.alpha_composite(fill, off)

    draw = ImageDraw.Draw(img)
    draw.line(pts + [pts[0]], fill=edge_c + (255,), width=max(2, int(TILE_SS * 1.3)), joint="curve")
    label = _label_for(wheel["keys"][slot], shift_on)
    fsize = (r_out * 0.30) if len(label) <= 2 else (r_out * 0.19)
    ar = math.radians(centre)
    draw.text((cx + r_mid * math.cos(ar), cy + r_mid * math.sin(ar)), label,
              font=_font(theme["font"], fsize), fill=_rgb(theme["selected_text"]) + (255,), anchor="mm")
    return img   # supersampled; downscaled by the caller


def _tile(wheel, slot, shift_on, flashing, theme, side, theme_sig):
    wsig = _wheel_sig(wheel)
    ss_key = ("ss", theme_sig, side, shift_on, wsig)
    base_ss = _TILE_CACHE.get(ss_key)
    if base_ss is None:
        if len(_TILE_CACHE) > 160:
            _TILE_CACHE.clear()
        base_ss = _build_base(wheel, shift_on, theme, side)
        _TILE_CACHE[ss_key] = base_ss
    if slot is None:
        disp_key = ("disp", ss_key)
        disp = _TILE_CACHE.get(disp_key)
        if disp is None:
            disp = base_ss.resize((side, side), Image.LANCZOS)
            _TILE_CACHE[disp_key] = disp
        return disp
    sel_key = ("sel", ss_key, slot, flashing)
    t = _TILE_CACHE.get(sel_key)
    if t is None:
        t = _build_selected(base_ss, wheel, slot, shift_on, flashing, theme, side).resize(
            (side, side), Image.LANCZOS)
        _TILE_CACHE[sel_key] = t
    return t


def _alpha(tile, factor):
    tile.putalpha(tile.getchannel("A").point(lambda v: int(v * factor)))
    return tile


def _edge_fade(tile, direction):
    w, h = tile.size
    ramp = Image.new("L", (w, 1))
    px = ramp.load()
    for x in range(w):
        f = x / max(1, w - 1)
        g = (1 - f) if direction < 0 else f
        px[x, 0] = int(70 + 185 * g)
    tile.putalpha(ImageChops.multiply(tile.getchannel("A"), ramp.resize((w, h))))
    return tile


def render_frame(w, h, wheels, idx, slot, shift_on, flashing, theme, offset=0.0, ss=None):
    """Render the whole overlay (Cupertino wheel picker) to a (w, h) RGBA image.

    `ss` is accepted for API compatibility but ignored — tiles self-supersample.
    """
    big = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    n = len(wheels)
    tsig = _theme_sig(theme)
    top, bottom = 24, 46         # bottom leaves room for the minimap + hint
    cx = w / 2
    avail = h - top - bottom
    cy = top + avail / 2
    r_out = min(w * 0.21, avail * 0.46)
    side = int((2 * r_out) / 0.86)          # tile side so its donut has radius r_out
    spacing = r_out * 2.05

    for d in sorted(range(-2, 3), key=lambda d: -abs(d + offset)):
        dist = abs(d + offset)
        if dist > 2.3:
            continue
        scale = max(0.42, 1.0 - 0.30 * dist)
        alpha = max(0.0, min(1.0, 1.0 - 0.52 * dist))
        if alpha <= 0.02:
            continue
        wheel = wheels[(idx + d) % n]
        # Only the current wheel (d == 0) shows the selection; `slot` indexes it,
        # so never apply it to a neighbour (which may have fewer keys).
        tslot = slot if d == 0 else None
        if tslot is not None and tslot >= len(wheel["keys"]):
            tslot = None
        tile = _tile(wheel, tslot, shift_on, flashing, theme, side, tsig)
        if scale < 0.999:
            s = max(1, int(side * scale))
            tile = tile.resize((s, s), Image.LANCZOS)
            _alpha(tile, alpha)
            if dist > 0.5:
                _edge_fade(tile, 1 if (d + offset) > 0 else -1)
        x = int(cx + (d + offset) * spacing - tile.width / 2)
        y = int(cy - tile.height / 2)
        big.alpha_composite(tile, (x, y))

    draw = ImageDraw.Draw(big)

    # Minimap: one dot per wheel, the current one highlighted.
    if n > 1:
        gap_d = 16
        dots_w = (n - 1) * gap_d
        dx = cx - dots_w / 2
        dy = h - 34
        acc = _rgb(theme["selected_fill"])
        dim = _rgb(theme["key_outline"])
        for i in range(n):
            r = 5 if i == idx else 3
            col = acc if i == idx else dim
            x = dx + i * gap_d
            draw.ellipse((x - r, dy - r, x + r, dy + r), fill=col + (255,))

    draw.text((cx, h - 14), "L-stick aim · A type · R-stick switch · B/Start close",
              font=_font(theme["font"], 11), fill=_rgb(theme["hint_text"]) + (220,), anchor="mm")
    return big
