from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import struct
import subprocess
import tempfile
import zlib

BASE_WIDTH = 1080
LOGO_GAP = 40
CTA_GAP = 48
MIN_CAP = 28
ART_TOLERANCE = 0.02
MAX_LINES = {"headline": 3, "subline": 2, "cta": 1}
LEADING = {"headline": 1.05, "subline": 1.3}
PILL_HEIGHT = 2.2
PILL_PAD = 1.1
WIDTH_CLASSES = {50: 1, 62.5: 2, 75: 3, 87.5: 4, 100: 5, 112.5: 6, 125: 7, 150: 8, 200: 9}
CLASS_WIDTHS = {number: width for width, number in WIDTH_CLASSES.items()}
FFMPEG = "compose needs ffmpeg 6.1 or later with drawtext"
SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ComposeError(ValueError):
    pass


@dataclass(frozen=True)
class Layout:
    safe: tuple[float, float, float, float]
    logo: int
    sizes: dict[str, int]
    band: float


LAYOUTS = {
    (4, 5): Layout((0.07, 0.07, 0.93, 0.93), 90, {"headline": 96, "subline": 46, "cta": 42}, 0.3),
    (1, 1): Layout((0.07, 0.07, 0.93, 0.93), 90, {"headline": 80, "subline": 44, "cta": 42}, 0.3),
    (9, 16): Layout((0.08, 0.15, 0.92, 0.64), 90, {"headline": 96, "subline": 46, "cta": 42}, 0.2),
}


@dataclass(frozen=True)
class Font:
    file: str
    path: Path
    cap: float
    chars: frozenset[int]


@dataclass(frozen=True)
class Style:
    font: Font
    weight: int
    width: float
    upper: bool
    color: str


@dataclass(frozen=True)
class Logo:
    file: str
    path: Path
    width: int
    height: int


@dataclass(frozen=True)
class Brand:
    backgrounds: tuple[str, ...]
    logo: Logo | None
    button: str
    opacity: float
    styles: dict[str, Style]


@dataclass(frozen=True)
class Copy:
    ratio: tuple[int, int]
    background: str
    lines: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class Mask:
    width: int
    data: bytes
    box: tuple[int, int, int, int]
    baseline: int


@dataclass(frozen=True)
class Line:
    role: str
    text: str
    size: int
    mask: Mask
    x: int
    y: int

    @property
    def box(self):
        x0, y0, x1, y1 = self.mask.box
        return self.x + x0, self.y + y0, self.x + x1, self.y + y1


@dataclass(frozen=True)
class Plan:
    brand: Brand
    copy: Copy
    width: int
    height: int
    safe: tuple[int, int, int, int]
    logo: tuple[int, int, int, int] | None
    pill: tuple[int, int, int, int]
    lines: tuple[Line, ...]
    top: int
    bottom: int


def hex_color(value, where):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ComposeError("{} must be a #rrggbb color".format(where))
    return value.lower()


def rgb(color):
    return bytes.fromhex(color[1:])


def inside(folder, name, where):
    if not isinstance(name, str) or not name:
        raise ComposeError("{} must be a file name".format(where))
    path = (folder / name).resolve()
    if not path.is_relative_to(folder):
        raise ComposeError("{} {} is outside the run folder".format(where, name))
    if not path.is_file():
        raise ComposeError("{} {} is missing".format(where, name))
    return path


def png_info(path):
    with open(path, "rb") as file:
        header = file.read(26)
    if len(header) < 26 or header[:8] != SIGNATURE or header[12:16] != b"IHDR":
        return None
    width, height, depth, kind = struct.unpack(">IIBB", header[16:26])
    return (width, height, depth, kind) if width and height else None


def mapped(data, cmap):
    chars = set()
    for record in range(cmap + 4, cmap + 4 + 8 * struct.unpack_from(">H", data, cmap + 2)[0], 8):
        platform, encoding, offset = struct.unpack_from(">HHI", data, record)
        if platform != 0 and (platform, encoding) not in ((3, 1), (3, 10)):
            continue
        table = cmap + offset
        kind = struct.unpack_from(">H", data, table)[0]
        if kind == 4:
            count = struct.unpack_from(">H", data, table + 6)[0] // 2
            ends = table + 14
            starts, deltas, ranges = (ends + 2 * count * step + 2 for step in (1, 2, 3))
            for segment in range(count):
                end, start, delta, skip = (struct.unpack_from(">H", data, array + 2 * segment)[0]
                                           for array in (ends, starts, deltas, ranges))
                for char in range(start, end + 1):
                    if skip:
                        glyph = struct.unpack_from(">H", data, ranges + 2 * segment + skip + 2 * (char - start))[0]
                        glyph = glyph and (glyph + delta) & 0xFFFF
                    else:
                        glyph = (char + delta) & 0xFFFF
                    if glyph:
                        chars.add(char)
        elif kind == 12:
            for group in range(table + 16, table + 16 + 12 * struct.unpack_from(">I", data, table + 12)[0], 12):
                start, end, glyph = struct.unpack_from(">III", data, group)
                chars.update(range(start + (glyph == 0), end + 1))
    return frozenset(chars)


def read_font(folder, where, name, weight, width_class):
    path = inside(folder, name, where)
    data = path.read_bytes()
    where = "{} {}".format(where, name)
    if data[:4] not in (b"\0\1\0\0", b"OTTO", b"true"):
        raise ComposeError("{} is not a TTF or OTF file".format(where))
    try:
        tables = {data[record:record + 4]: struct.unpack_from(">I", data, record + 8)[0]
                  for record in range(12, 12 + 16 * struct.unpack_from(">H", data, 4)[0], 16)}
        version, _, found_weight, found_class = struct.unpack_from(">HhHH", data, tables[b"OS/2"])
        cap = struct.unpack_from(">h", data, tables[b"OS/2"] + 88)[0] if version >= 2 else 0
        units = struct.unpack_from(">H", data, tables[b"head"] + 18)[0]
        chars = mapped(data, tables[b"cmap"])
    except (KeyError, struct.error):
        raise ComposeError("{} is not a readable TTF or OTF file".format(where)) from None
    if b"fvar" in tables:
        raise ComposeError("{} is a variable font, which renders only its default instance; use a static "
                           "instance at weight {} width {}".format(where, weight, CLASS_WIDTHS[width_class]))
    if (found_weight, found_class) != (weight, width_class):
        raise ComposeError("{} is weight {} width {}, not the asked weight {} width {}".format(
            where, found_weight, CLASS_WIDTHS.get(found_class, "class {}".format(found_class)), weight,
            CLASS_WIDTHS[width_class]))
    return Font(name, path, cap / units if cap > 0 and units else 0.7, chars)


def read_style(folder, role, style):
    where = "layer.json type." + role
    if not isinstance(style, dict):
        raise ComposeError("{} must be an object".format(where))
    weight, width = style.get("weight"), style.get("width")
    if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 1000:
        raise ComposeError("{} weight must be an integer from 1 to 1000".format(where))
    if isinstance(width, bool) or not isinstance(width, (int, float)) or width not in WIDTH_CLASSES:
        raise ComposeError("{} width must be one of {}".format(where, ", ".join(map(str, WIDTH_CLASSES))))
    if style.get("case", "upper") != "upper":
        raise ComposeError('{} case must be "upper" or absent'.format(where))
    color = hex_color(style.get("color"), where + " color")
    font = read_font(folder, where + " font", style.get("font"), weight, WIDTH_CLASSES[width])
    return Style(font, weight, width, "case" in style, color)


def read_brand(folder):
    try:
        brand = json.loads((folder / "layer.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ComposeError("compose needs layer.json in the run folder") from None
    except ValueError as error:
        raise ComposeError("layer.json is not JSON ({})".format(error)) from None
    if not isinstance(brand, dict):
        raise ComposeError("layer.json must be an object")
    backgrounds = brand.get("backgrounds")
    if not isinstance(backgrounds, list) or not backgrounds:
        raise ComposeError("layer.json backgrounds must be a non-empty list of #rrggbb colors")
    backgrounds = tuple(hex_color(value, "layer.json backgrounds") for value in backgrounds)
    logo = None
    if brand.get("logo") is not None:
        path = inside(folder, brand["logo"], "layer.json logo")
        info = png_info(path)
        if info is None or info[2:] not in ((8, 2), (8, 6)):
            raise ComposeError("layer.json logo {} is not an 8-bit RGB or RGBA PNG".format(brand["logo"]))
        logo = Logo(brand["logo"], path, *info[:2])
    button = brand.get("button") if isinstance(brand.get("button"), dict) else {}
    fill = hex_color(button.get("color"), "layer.json button color")
    opacity = button.get("opacity")
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not 0 < opacity <= 1:
        raise ComposeError("layer.json button opacity must be a number in (0, 1]")
    styles = brand.get("type")
    if not isinstance(styles, dict) or set(styles) != set(MAX_LINES):
        raise ComposeError("layer.json type needs exactly headline, subline, and cta")
    return Brand(backgrounds, logo, fill, opacity,
                 {role: read_style(folder, role, styles[role]) for role in MAX_LINES})


def read_copy(brand, block, size):
    if not isinstance(block, dict):
        raise ComposeError("compose must be an object")
    lines = {}
    for role, most in MAX_LINES.items():
        text = block.get(role)
        split = text.split("\n") if isinstance(text, str) else []
        if not 1 <= len(split) <= most or not all(line.strip() for line in split):
            raise ComposeError("compose {} must be 1 to {} lines, none empty".format(role, most))
        style = brand.styles[role]
        lines[role] = tuple(line.upper() if style.upper else line for line in split)
        missing = next((char for char in "".join(lines[role]) if ord(char) not in style.font.chars), None)
        if missing:
            raise ComposeError("{}: {} has no glyph for {!r} (U+{:04X})".format(
                role, style.font.file, missing, ord(missing)))
    background = hex_color(block.get("background", brand.backgrounds[0]), "compose background")
    if background not in brand.backgrounds:
        raise ComposeError("compose background {} is not in layer.json backgrounds".format(background))
    sides = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size) if isinstance(size, str) else None
    ratio = sides and next((ratio for ratio in LAYOUTS
                            if int(sides[1]) * ratio[1] == int(sides[2]) * ratio[0]), None)
    if not ratio:
        names = ["{}:{}".format(*ratio) for ratio in LAYOUTS]
        raise ComposeError("compose needs a size in {}, or {}".format(", ".join(names[:-1]), names[-1]))
    return Copy(ratio, background, lines)


def ffmpeg(failure, *arguments, cwd=None):
    try:
        ran = subprocess.run(["ffmpeg", "-v", "error", *arguments], stdin=subprocess.DEVNULL, capture_output=True,
                             cwd=cwd, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise ComposeError("{} ({})".format(FFMPEG, error)) from None
    if ran.returncode:
        lines = [line.strip() for line in ran.stderr.decode(errors="replace").splitlines() if line.strip()]
        raise ComposeError("{} ({})".format(failure, (lines or ["exit status {}".format(ran.returncode)])[-1]))
    return ran.stdout


def ink(data, width, height):
    x0, y0, x1, y1 = width, None, 0, None
    for y in range(height):
        row = data[y * width:(y + 1) * width]
        right = len(row.rstrip(b"\0"))
        if right:
            x0, x1 = min(x0, width - len(row.lstrip(b"\0"))), max(x1, right)
            y0, y1 = y if y0 is None else y0, y + 1
    return None if y0 is None else (x0, y0, x1, y1)


def rasterize(font, size, text):
    width, height, baseline = 2 * size + math.ceil(1.5 * size * len(text)), math.ceil(2.4 * size), round(1.6 * size)
    with tempfile.TemporaryDirectory(prefix="static-ads-") as temp:
        shutil.copyfile(font, Path(temp, "f.ttf"))
        Path(temp, "t.txt").write_text(text, encoding="utf-8")
        data = ffmpeg(FFMPEG, "-f", "lavfi", "-i", "color=c=black:s={}x{}:d=1,format=gray".format(width, height),
                      "-vf", "drawtext=fontfile=f.ttf:textfile=t.txt:fontsize={0}:fontcolor=white:x={0}:y={1}"
                      ":y_align=baseline:expansion=none:ft_load_flags=no_hinting".format(size, baseline),
                      "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-", cwd=temp)
    if len(data) != width * height:
        raise ComposeError("{} (drawtext gave {} bytes, not {})".format(FFMPEG, len(data), width * height))
    box = ink(data, width, height)
    if box is None:
        raise ComposeError('"{}" draws nothing in {}'.format(text, font.name))
    return Mask(width, data, box, baseline)


def layout(brand, copy, width, height):
    spec = LAYOUTS[copy.ratio]
    scale = width / BASE_WIDTH
    safe = tuple(round(edge * side) for edge, side in zip(spec.safe, (width, height, width, height)))
    left, top, right, bottom = safe
    room = right - left
    masks = {}

    def px(units):
        return round(units * scale)

    def drawn(role, size):
        font = brand.styles[role].font.path
        for text in copy.lines[role]:
            if (font, size, text) not in masks:
                masks[font, size, text] = rasterize(font, size, text)
        return [masks[font, size, text] for text in copy.lines[role]]

    def minimum(role):
        return math.ceil(MIN_CAP * scale / brand.styles[role].font.cap)

    def need(role, size):
        widths = [mask.box[2] - mask.box[0] for mask in drawn(role, size)]
        widest = max(range(len(widths)), key=widths.__getitem__)
        return widths[widest] + (2 * round(PILL_PAD * size) if role == "cta" else 0), copy.lines[role][widest]

    def fit(role, size):
        wide, text = need(role, size)
        if wide > room:
            size = size * room // wide
        while size >= minimum(role):
            wide, text = need(role, size)
            if wide <= room:
                return size
            size -= 1
        raise ComposeError('{} line "{}" needs {} px at {} wide, under the {} px minimum; {}'.format(
            role, text, round(size / scale), BASE_WIDTH, round(minimum(role) / scale),
            "shorten it" if MAX_LINES[role] == 1 else "shorten it or split it"))

    def line(role, size, text, mask, baseline):
        return Line(role, text, size, mask, left + (room - mask.box[2] + mask.box[0]) // 2 - mask.box[0],
                    baseline - mask.baseline)

    def place(sizes):
        logo, below = None, top
        if brand.logo:
            high = px(spec.logo)
            wide = round(high * brand.logo.width / brand.logo.height)
            if wide > room:
                wide, high = room, round(room * brand.logo.height / brand.logo.width)
            x = left + (room - wide) // 2
            logo = (x, top, x + wide, top + high)
            below = top + high + px(LOGO_GAP)
        size = sizes["headline"]
        first = below + round(brand.styles["headline"].font.cap * size)
        heads = [line("headline", size, text, mask, first + round(LEADING["headline"] * size) * index)
                 for index, (text, mask) in enumerate(zip(copy.lines["headline"], drawn("headline", size)))]
        size = sizes["cta"]
        mask = drawn("cta", size)[0]
        high = round(PILL_HEIGHT * size)
        wide = mask.box[2] - mask.box[0] + 2 * round(PILL_PAD * size)
        x = left + (room - wide) // 2
        pill = (x, bottom - high, x + wide, bottom)
        cta = line("cta", size, copy.lines["cta"][0], mask,
                   pill[1] + round((high + round(brand.styles["cta"].font.cap * size)) / 2))
        size = sizes["subline"]
        subs = drawn("subline", size)
        step = round(LEADING["subline"] * size)
        last = pill[1] - px(CTA_GAP) - max(0, subs[-1].box[3] - subs[-1].baseline)
        subs = [line("subline", size, text, mask, last - step * (len(subs) - 1 - index))
                for index, (text, mask) in enumerate(zip(copy.lines["subline"], subs))]
        return Plan(brand, copy, width, height, safe, logo, pill, (*heads, *subs, cta), heads[-1].box[3],
                    subs[0].box[1])

    sizes = {role: fit(role, px(spec.sizes[role])) for role in MAX_LINES}
    while True:
        plan = place(sizes)
        if plan.bottom - plan.top >= spec.band * height:
            return plan
        smaller = sizes["headline"] * 96 // 100
        if smaller < minimum("headline"):
            raise ComposeError("the type leaves too little room for the art, under {:.0%} of the height; shorten "
                               "the headline or sub-line".format(spec.band))
        sizes["headline"] = fit("headline", smaller)


def preview(folder, block, size):
    brand = read_brand(folder)
    copy = read_copy(brand, block, size)
    return layout(brand, copy, BASE_WIDTH, BASE_WIDTH * copy.ratio[1] // copy.ratio[0])


def describe(plan):
    sizes = {line.role: line.size for line in plan.lines}
    return "compose {}:{} on {}, headline {}x{}px, subline {}x{}px, cta {}px, logo {}".format(
        *plan.copy.ratio, plan.copy.background, len(plan.copy.lines["headline"]), sizes["headline"],
        len(plan.copy.lines["subline"]), sizes["subline"], sizes["cta"],
        plan.brand.logo.file if plan.brand.logo else "none")


def key(pixels, target, start, end):
    t0, t1, t2 = target
    near = [pixel for pixel in (pixels[at:at + 3] for at in range(0, len(pixels), 21))
            if abs(pixel[0] - t0) <= 32 and abs(pixel[1] - t1) <= 32 and abs(pixel[2] - t2) <= 32]
    out = bytearray(pixels)
    if len(near) < 0.05 * math.ceil(len(pixels) / 21):
        return out, None
    base = bytes(statistics.median_low(pixel[channel] for pixel in near) for channel in range(3))
    b0, b1, b2 = base
    done = {}
    for at in range(start, end, 3):
        pixel = pixels[at:at + 3]
        keyed = done.get(pixel)
        if keyed is None:
            distance = max(abs(pixel[0] - b0), abs(pixel[1] - b1), abs(pixel[2] - b2))
            if distance <= 8:
                keyed = target
            elif distance >= 40:
                keyed = pixel
            else:
                alpha = (distance - 8) / 32
                keyed = bytes(round(value + (old - value) * alpha) for old, value in zip(pixel, target))
            done[pixel] = keyed
        out[at:at + 3] = keyed
    return out, base


def tint(out, start, end, target, alpha):
    for channel, value in enumerate(target):
        table = bytes(round(old + (value - old) * alpha) for old in range(256))
        out[start + channel:end:3] = out[start + channel:end:3].translate(table)


def mix(out, at, color, alpha):
    out[at:at + 3] = bytes(round(old * (1 - alpha) + value * alpha) for old, value in zip(out[at:at + 3], color))


def encode(width, height, pixels):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    stride = 3 * width
    raw = b"".join(b"\0" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    return (SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def render(plan, art, png, record):
    info = png_info(art)
    if info is None:
        raise ComposeError("compose needs PNG art, and {} is not a PNG".format(art.name))
    width, height = info[:2]
    across, down = plan.copy.ratio
    if abs(width * down / (height * across) - 1) > ART_TOLERANCE:
        raise ComposeError("art {}x{} is not within {:.0%} of {}:{}".format(width, height, ART_TOLERANCE, across, down))
    brand, copy = plan.brand, plan.copy
    plan = layout(brand, copy, width, height)
    pixels = ffmpeg("ffmpeg could not decode " + art.name, "-i", str(art.absolute()), "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-")
    stride = 3 * width
    if len(pixels) != stride * height:
        raise ComposeError("ffmpeg decoded {} to {} bytes, not {}".format(art.name, len(pixels), stride * height))
    target = rgb(copy.background)
    gap, fade = round(0.02 * height), round(0.05 * height)
    solid_top, solid_bottom = plan.top + gap, plan.bottom - gap
    out, base = key(pixels, target, stride * solid_top, stride * solid_bottom)
    out[:stride * solid_top] = target * width * solid_top
    out[stride * solid_bottom:] = target * width * (height - solid_bottom)
    for step in range(fade):
        t = (step + 0.5) / fade
        alpha = t * t * (3 - 2 * t)
        for row in (solid_top + fade - 1 - step, solid_bottom - fade + step):
            tint(out, stride * row, stride * (row + 1), target, alpha)
    if plan.logo:
        x0, y0, x1, y1 = plan.logo
        rgba = ffmpeg("ffmpeg could not decode " + brand.logo.file, "-i", str(brand.logo.path), "-vf",
                      "scale={}:{}:flags=lanczos".format(x1 - x0, y1 - y0), "-frames:v", "1", "-f", "rawvideo",
                      "-pix_fmt", "rgba", "-")
        if len(rgba) != 4 * (x1 - x0) * (y1 - y0):
            raise ComposeError("ffmpeg decoded {} to {} bytes".format(brand.logo.file, len(rgba)))
        for index in range(0, len(rgba), 4):
            if rgba[index + 3]:
                y, x = divmod(index // 4, x1 - x0)
                mix(out, stride * (y0 + y) + 3 * (x0 + x), rgba[index:index + 3], rgba[index + 3] / 255)
    x0, y0, x1, y1 = plan.pill
    radius = (y1 - y0) / 2
    fill = rgb(brand.button)
    for y in range(y0, y1):
        for x in range(x0, x1):
            dx = max(x0 + radius - x - 0.5, 0, x + 0.5 - x1 + radius)
            cover = min(max(0.5 - math.hypot(dx, y + 0.5 - y0 - radius) + radius, 0), 1)
            if cover:
                mix(out, stride * y + 3 * x, fill, brand.opacity * cover)
    for line in plan.lines:
        color = rgb(brand.styles[line.role].color)
        mx0, my0, mx1, my1 = line.mask.box
        for my in range(my0, my1):
            for mx in range(mx0, mx1):
                m = line.mask.data[my * line.mask.width + mx]
                if m:
                    at = stride * (line.y + my) + 3 * (line.x + mx)
                    out[at:at + 3] = bytes((old * (255 - m) + value * m + 127) // 255
                                           for old, value in zip(out[at:at + 3], color))
    partial = png.with_name(png.name + ".partial")
    partial.write_bytes(encode(width, height, out))
    partial.replace(png)
    record.write_text(json.dumps({
        "size": "{}x{}".format(width, height), "layout": "{}:{}".format(across, down), "background": copy.background,
        "art": art.name, "art_background": "#" + base.hex() if base else None, "safe_box": list(plan.safe),
        "logo": {"file": brand.logo.file, "box": list(plan.logo)} if plan.logo else None,
        "button": {"color": brand.button, "opacity": brand.opacity, "box": list(plan.pill)},
        "text": [{"role": line.role, "text": line.text, "font": brand.styles[line.role].font.file,
                  "weight": brand.styles[line.role].weight, "width": brand.styles[line.role].width,
                  "size": line.size, "color": brand.styles[line.role].color, "box": list(line.box)}
                 for line in plan.lines],
    }, indent=2) + "\n", encoding="utf-8")
