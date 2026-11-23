"""Generate the MarvelHeroes resource-pack textures (Iron Man, Spider-Man) from hand-made pixel art.

Every pixel is defined in this file (no vanilla assets are copied), so the output can be
committed freely. Run from anywhere:

    python plugin_template/marvel_heroes/tools/gen_hero_textures.py

Requires Pillow. Output goes to ``plugin_template/marvel_heroes/resourcepack``.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

PACK = Path(__file__).resolve().parent.parent / "resourcepack"

PALETTE = {
    ".": None,
    "R": (178, 24, 24),  # suit red
    "r": (118, 12, 14),  # dark red / panel seams
    "H": (222, 58, 46),  # red highlight
    "G": (236, 184, 44),  # gold
    "g": (172, 120, 22),  # dark gold
    "Y": (255, 226, 112),  # gold highlight
    "E": (214, 250, 255),  # glowing eye slit
    "C": (104, 212, 255),  # arc reactor / repulsor cyan
    "W": (240, 255, 255),  # reactor core
    "B": (36, 118, 204),  # deep blue ring
    "K": (38, 10, 12),  # outline / interior
    "s": (150, 152, 162),  # joint silver
    # Spider-Man
    "P": (200, 24, 34),  # suit red
    "p": (138, 14, 22),  # dark red
    "U": (32, 74, 176),  # suit blue
    "u": (20, 46, 120),  # dark blue
    "k": (16, 14, 18),  # web lines / emblem / eye rims
    "w": (246, 248, 252),  # eye lenses
}

# Glowing parts get no noise so they stay crisp.
NO_NOISE = set("EWCBKkw")

rng = random.Random(3000)


def put(img: Image.Image, x0: int, y0: int, rows: list[str], noise: int = 7) -> None:
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            rgb = PALETTE[ch]
            if rgb is None:
                continue
            if ch not in NO_NOISE and noise:
                d = rng.randint(-noise, noise)
                rgb = tuple(max(0, min(255, c + d)) for c in rgb)
            img.putpixel((x0 + dx, y0 + dy), (*rgb, 255))


def fill(w: int, h: int, ch: str) -> list[str]:
    return [ch * w for _ in range(h)]


def shade(rows: list[str]) -> list[str]:
    """Darken the outer ring of a face so every armour plate reads as a separate panel."""
    dark = {"R": "r", "G": "g", "H": "R", "Y": "G"}
    out = [list(r) for r in rows]
    h, w = len(rows), len(rows[0])
    for y in range(h):
        for x in range(w):
            edge = y in (0, h - 1) or x in (0, w - 1)
            if edge and out[y][x] in dark and rows[y][x] != ".":
                out[y][x] = dark[out[y][x]]
    return ["".join(r) for r in out]


# --------------------------------------------------------------------------------------
# Armour layer 1: helmet (head), chestplate (body + arms), boots (legs)
# Standard 64x32 player-model UV layout.
# --------------------------------------------------------------------------------------

HEAD_FRONT = [
    "rRRRRRRr",
    "RGGYYGGR",
    "RGGGGGGR",
    "REEGGEER",
    "gGGGGGGg",
    "RgGGGGgR",
    "RGgKKgGR",
    "rRGGGGRr",
]
HEAD_RIGHT = [  # right edge touches the face
    "rRRRRRRr",
    "RHRRRRRR",
    "RRRRRRRG",
    "RRrrRRGG",
    "RRrrRRGG",
    "RRRRRRGG",
    "RRRRRRgG",
    "rRRRRRRg",
]
HEAD_LEFT = [row[::-1] for row in HEAD_RIGHT]
HEAD_TOP = [
    "rRRRRRRr",
    "RHHRRHHR",
    "RHRGGRHR",
    "RRRGGRRR",
    "RRRGGRRR",
    "RRRGGRRR",
    "RRRRRRRR",
    "rRRRRRRr",
]
HEAD_BACK = shade(
    [
        "RRRRRRRR",
        "RRRrrRRR",
        "RRRrrRRR",
        "RRRRRRRR",
        "RRRRRRRR",
        "RrRRRRrR",
        "RRRRRRRR",
        "RRRRRRRR",
    ]
)

BODY_FRONT = [
    "rRRRRRRr",
    "RHRRRRHR",
    "RRRBBRRR",
    "RRBWWBRR",
    "RRBWWBRR",
    "RRRBBRRR",
    "rRRRRRRr",
    "gGGGGGGg",
    "RgGYYGgR",
    "RGgGGgGR",
    "RGGggGGR",
    "rrGGGGrr",
]
BODY_BACK = [
    "rRRRRRRr",
    "RRRRRRRR",
    "RRgRRgRR",
    "RRgRRgRR",
    "RRRRRRRR",
    "RRRrrRRR",
    "RRRrrRRR",
    "RRRRRRRR",
    "rRRRRRRr",
    "RRRRRRRR",
    "RRrrrrRR",
    "rrRRRRrr",
]
BODY_SIDE = [
    "rRRr",
    "RRRR",
    "RHRR",
    "RRRR",
    "RRRR",
    "RRRR",
    "rRRr",
    "gGGg",
    "GGGG",
    "GggG",
    "GGGG",
    "rRRr",
]
ARM_SIDE = [
    "RRRR",
    "RHHR",
    "RRRR",
    "rrrr",
    "GGGG",
    "GYGG",
    "GGGG",
    "gggg",
    "RRRR",
    "RHRR",
    "RRRR",
    "rrrr",
]
ARM_TOP = shade(fill(4, 4, "R"))
ARM_PALM = ["KBBK", "BCWB", "BWCB", "KBBK"]  # repulsor in the palm

BOOT_SIDE = fill(4, 5, ".") + [
    "gGGg",
    "RRRR",
    "RHRR",
    "RRRR",
    "rRRr",
    "gGGg",
    "KKKK",
]
BOOT_SOLE = ["KBBK", "BCCB", "BCWB", "KBBK"]  # blue thruster


def layer1() -> Image.Image:
    img = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    # Head (u=0, v=0)
    put(img, 8, 0, HEAD_TOP)
    put(img, 16, 0, fill(8, 8, "K"), noise=0)
    put(img, 0, 8, HEAD_RIGHT)
    put(img, 8, 8, HEAD_FRONT)
    put(img, 16, 8, HEAD_LEFT)
    put(img, 24, 8, HEAD_BACK)
    # Body (u=16, v=16)
    put(img, 20, 16, shade(fill(8, 4, "R")))
    put(img, 28, 16, shade(fill(8, 4, "G")))
    put(img, 16, 20, BODY_SIDE)
    put(img, 20, 20, BODY_FRONT)
    put(img, 28, 20, BODY_SIDE)
    put(img, 32, 20, BODY_BACK)
    # Arm (u=40, v=16)
    put(img, 44, 16, ARM_TOP)
    put(img, 48, 16, ARM_PALM, noise=0)
    for x in (40, 44, 48, 52):
        put(img, x, 20, ARM_SIDE)
    # Boots on the leg (u=0, v=16): only the lower part is painted
    put(img, 8, 16, BOOT_SOLE, noise=0)
    for x in (0, 4, 8, 12):
        put(img, x, 20, BOOT_SIDE)
    return img


# --------------------------------------------------------------------------------------
# Armour layer 2: leggings (belt on the body, thighs/knees on the legs)
# --------------------------------------------------------------------------------------

LEG_FRONT = [
    "rRRr",
    "GGRR",
    "GYGR",
    "GGGR",
    "gGgR",
    "RRRR",
    "sKKs",
    "RssR",
    "RRRR",
    "rRRr",
    "....",
    "....",
]
LEG_SIDE = [
    "rRRr",
    "RRRR",
    "RHRR",
    "RRRR",
    "RRRR",
    "RRRR",
    "ssss",
    "RRRR",
    "RRRR",
    "rRRr",
    "....",
    "....",
]
BELT = fill(8, 8, ".") + ["gGGGGGGg", "GGYGGYGG", "gGGGGGGg", "rRRrrRRr"]
BELT_SIDE = fill(4, 8, ".") + ["gGGg", "GGGG", "gGGg", "rRRr"]


def layer2() -> Image.Image:
    img = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    put(img, 4, 16, shade(fill(4, 4, "R")))
    put(img, 0, 20, LEG_SIDE)
    put(img, 4, 20, LEG_FRONT)
    put(img, 8, 20, LEG_SIDE)
    put(img, 12, 20, LEG_SIDE)
    put(img, 16, 20, BELT_SIDE)
    put(img, 20, 20, BELT)
    put(img, 28, 20, BELT_SIDE)
    put(img, 32, 20, BELT)
    return img


# --------------------------------------------------------------------------------------
# 16x16 inventory icons
# --------------------------------------------------------------------------------------

ICONS = {
    "iron_helmet": [
        "................",
        "................",
        "....KKKKKKKK....",
        "...KHHRRRRRRK...",
        "..KRHRRRRRRRRK..",
        "..KRRGGGGGGRRK..",
        "..KRGGYYGGGGRK..",
        "..KRGEEGGEEGRK..",
        "..KRGGGGGGGGRK..",
        "..KRgGGGGGGgRK..",
        "..KRRgGKKGgRRK..",
        "...KRgGGGGgRK...",
        "....KKKKKKKK....",
        "................",
        "................",
        "................",
    ],
    "iron_chestplate": [
        "................",
        "..KKKK....KKKK..",
        ".KRHRRKKKKRRRRK.",
        ".KHRRRRRRRRRRRK.",
        ".KRRRRRBBRRRRRK.",
        ".KRRRRBWWBRRRRK.",
        ".KKKRRBWWBRRKKK.",
        "...KRRRBBRRRK...",
        "...KRRRRRRRRK...",
        "...KgGGGGGGgK...",
        "...KGgGYYGgGK...",
        "...KGGgGGgGGK...",
        "...KGGGggGGGK...",
        "...KRRGGGGRRK...",
        "...KKKKKKKKKK...",
        "................",
    ],
    "iron_leggings": [
        "................",
        "...KKKKKKKKKK...",
        "...KgGYGGYGgK...",
        "...KrRRRRRRrK...",
        "...KGGRKKRGGK...",
        "...KGYRK.KRYGK..",
        "...KGGRK.KRGGK..",
        "...KgGRK.KRGgK..",
        "...KRRRK.KRRRK..",
        "...KssK...KssK..",
        "...KRRK...KRRK..",
        "...KRHK...KRHK..",
        "...KRRK...KRRK..",
        "...KKKK...KKKK..",
        "................",
        "................",
    ],
    "iron_boots": [
        "................",
        "................",
        "................",
        "................",
        "...KKKK..KKKK...",
        "...KRHK..KRHK...",
        "...KRRK..KRRK...",
        "...KGGK..KGGK...",
        "...KRRK..KRRK...",
        "..KRRRK.KRRRK...",
        ".KHRRRK.KHRRRK..",
        ".KGGGGK.KGGGGK..",
        ".KKKKKK.KKKKKK..",
        "..CBBC...CBBC...",
        "................",
        "................",
    ],
    # Fully transparent: the thruster slot (hotbar 8) should look empty.
    "thruster": ["................"] * 16,
    "repulsor": [
        "................",
        "....K.K.K.......",
        "...KRKRKRK......",
        "...KRKRKRKK.....",
        "...KRKRKRKRK....",
        "..KRRRRRRKRK....",
        "..KRRBBBRRRK....",
        ".KRRBCWCBRRK....",
        ".KRRBWWWBRK.....",
        ".KRRBCWCBRK.....",
        "..KRRBBBRRK.....",
        "...KRRRRRK......",
        "...KGGGGGK......",
        "...KgGYGgK......",
        "...KKKKKKK......",
        "................",
    ],
}


def icon(rows: list[str]) -> Image.Image:
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    put(img, 0, 0, [r.ljust(16, ".")[:16] for r in rows], noise=5)
    return img


# --------------------------------------------------------------------------------------
# Spider-Man: leather armour. The leather base layer is tinted by the dye colour, the overlay
# layer is not, so the base is left empty and the whole design lives in the overlay. That way
# every leather set looks like the suit whatever it is dyed.
# --------------------------------------------------------------------------------------

WEB_PERIOD = 7


def webbed(rows: list[str], x0: int, y0: int) -> list[str]:
    """Draw a diagonal web lattice over the red parts. Uses texture coordinates so the lines
    run on from one face to the next."""
    out = []
    for dy, row in enumerate(rows):
        line = []
        for dx, ch in enumerate(row):
            x, y = x0 + dx, y0 + dy
            on_web = (x + y) % WEB_PERIOD == 0 or (x - y) % WEB_PERIOD == 0
            line.append("k" if ch == "P" and on_web else ch)
        out.append("".join(line))
    return out


def sput(img: Image.Image, x0: int, y0: int, rows: list[str], web: bool = True) -> None:
    put(img, x0, y0, webbed(rows, x0, y0) if web else rows, noise=5)


SPIDER_FACE = [  # big white eyes with black rims, drawn without the lattice
    "PPkPPkPP",
    "PkkPPkkP",
    "kwwkkwwk",
    "wwwkkwww",
    "kwwkkwwk",
    "PkkPPkkP",
    "PPPkkPPP",
    "PPkPPkPP",
]
SPIDER_EMBLEM = [  # black spider on the chest, rows 2-7 of the body front
    "PPkPPkPP",
    "PPPkkPPP",
    "PkkkkkkP",
    "PPPkkPPP",
    "PPkkkkPP",
    "PkPkkPkP",
]
SPIDER_LOWER = [  # red centre, blue flanks below the ribs
    "UUPPPPUU",
    "UUPPPPUU",
    "UUPPPPUU",
    "uUPPPPUu",
]
SPIDER_BODY_SIDE = fill(4, 11, "U") + ["uuuu"]
SPIDER_ARM = fill(4, 4, "P") + fill(4, 4, "U") + fill(4, 4, "P")  # red shoulder, blue arm, red glove
SPIDER_BOOT = fill(4, 5, ".") + fill(4, 6, "P") + ["kkkk"]


def spider_layer1() -> Image.Image:
    img = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    # Head (u=0, v=0)
    sput(img, 8, 0, fill(8, 8, "P"))
    sput(img, 16, 0, fill(8, 8, "P"))
    sput(img, 0, 8, fill(8, 8, "P"))
    sput(img, 8, 8, SPIDER_FACE, web=False)
    sput(img, 16, 8, fill(8, 8, "P"))
    sput(img, 24, 8, fill(8, 8, "P"))
    # Body (u=16, v=16)
    sput(img, 20, 16, fill(8, 4, "P"))
    sput(img, 28, 16, fill(8, 4, "U"))
    sput(img, 16, 20, SPIDER_BODY_SIDE)
    sput(img, 20, 20, fill(8, 2, "P"))
    sput(img, 20, 22, SPIDER_EMBLEM, web=False)  # keep the emblem clean
    sput(img, 20, 28, SPIDER_LOWER)
    sput(img, 28, 20, SPIDER_BODY_SIDE)
    sput(img, 32, 20, fill(8, 8, "P") + SPIDER_LOWER)
    # Arms (u=40, v=16)
    sput(img, 44, 16, fill(4, 4, "P"))
    sput(img, 48, 16, fill(4, 4, "P"))
    for x in (40, 44, 48, 52):
        sput(img, x, 20, SPIDER_ARM)
    # Boots on the legs (u=0, v=16)
    sput(img, 8, 16, fill(4, 4, "k"), web=False)
    for x in (0, 4, 8, 12):
        sput(img, x, 20, SPIDER_BOOT)
    return img


SPIDER_LEG_FRONT = ["UPPU"] * 10 + ["....", "...."]  # red stripe down the front of blue legs
SPIDER_LEG_SIDE = fill(4, 10, "U") + ["....", "...."]
SPIDER_BELT = fill(8, 8, ".") + ["PPPPPPPP", "PPPPPPPP", "PPPPPPPP", "pppppppp"]
SPIDER_BELT_SIDE = fill(4, 8, ".") + ["UUUU", "UUUU", "UUUU", "uuuu"]


def spider_layer2() -> Image.Image:
    img = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    sput(img, 4, 16, fill(4, 4, "U"))
    sput(img, 0, 20, SPIDER_LEG_SIDE)
    sput(img, 4, 20, SPIDER_LEG_FRONT)
    sput(img, 8, 20, SPIDER_LEG_SIDE)
    sput(img, 12, 20, SPIDER_LEG_FRONT)
    sput(img, 16, 20, SPIDER_BELT_SIDE)
    sput(img, 20, 20, SPIDER_BELT)
    sput(img, 28, 20, SPIDER_BELT_SIDE)
    sput(img, 32, 20, SPIDER_BELT)
    return img


SPIDER_ICONS = {
    "leather_helmet": [
        "................",
        "................",
        "....kkkkkkkk....",
        "...kPPkPPkPPk...",
        "..kPkPPkkPPkPk..",
        "..kkPkPPPPkPkk..",
        "..kPkkkPPkkkPk..",
        "..kkwwwkkwwwkk..",
        "..kkwwwkkwwwkk..",
        "..kPkkkPPkkkPk..",
        "..kPPkPPPPkPPk..",
        "..kPkPPkkPPkPk..",
        "...kPPkPPkPPk...",
        "....kkkkkkkk....",
        "................",
        "................",
    ],
    "leather_chestplate": [
        "................",
        "..kkkk....kkkk..",
        ".kPkPPkkkkPPkPk.",
        ".kkPPkPPPPkPPkk.",
        ".kPPkPkPPkPkPPk.",
        ".kPkPPPkkPPPkPk.",
        ".kkkPkkkkkkPkkk.",
        "...kPPPkkPPPk...",
        "...kPPkkkkPPk...",
        "...kPkPkkPkPk...",
        "...kUUPPPPUUk...",
        "...kUUPkkPUUk...",
        "...kUUPPPPUUk...",
        "...kuUPkkPUuk...",
        "...kkkkkkkkkk...",
        "................",
    ],
    "leather_leggings": [
        "................",
        "...kkkkkkkkkk...",
        "...kPkPPPPkPk...",
        "...kppppppppk...",
        "...kUPUkkUPUk...",
        "...kUPUk.kUPUk..",
        "...kUPUk.kUPUk..",
        "...kUPUk.kUPUk..",
        "...kUPUk.kUPUk..",
        "...kUPk...kPUk..",
        "...kUPk...kPUk..",
        "...kUPk...kPUk..",
        "...kuuk...kuuk..",
        "...kkkk...kkkk..",
        "................",
        "................",
    ],
    "leather_boots": [
        "................",
        "................",
        "................",
        "................",
        "...kkkk..kkkk...",
        "...kPkk..kPkk...",
        "...kkPk..kkPk...",
        "...kPkk..kPkk...",
        "...kkPk..kkPk...",
        "..kPkPk.kPkPk...",
        ".kPkPkk.kPkPkk..",
        ".kkPkPk.kkPkPk..",
        ".kkkkkk.kkkkkk..",
        "................",
        "................",
        "................",
    ],
}


# --------------------------------------------------------------------------------------
# Thor: Mjolnir (a custom item model on an iron sword). Drawn diagonally like vanilla tools:
# handle from the bottom-left, the head across it at the top-right.
# --------------------------------------------------------------------------------------

MJOLNIR_COLORS = {
    "head": (150, 154, 164),
    "head_hi": (206, 210, 220),
    "head_lo": (98, 102, 112),
    "rune": (120, 200, 255),
    "leather": (112, 66, 36),
    "leather_lo": (74, 42, 22),
    "strap": (60, 36, 20),
    "outline": (28, 26, 30),
}


def mjolnir_pixels() -> dict[tuple[int, int], tuple[int, int, int]]:
    """Rasterise the hammer in handle-aligned coordinates: u along the handle, v across it."""
    import math

    hx, hy = 10.0, 5.6  # head centre
    du = (-1 / math.sqrt(2), 1 / math.sqrt(2))  # down-left, along the handle
    dv = (1 / math.sqrt(2), 1 / math.sqrt(2))  # across the handle
    c = MJOLNIR_COLORS
    px = {}
    for y in range(16):
        for x in range(16):
            rx, ry = x + 0.5 - hx, y + 0.5 - hy
            u = rx * du[0] + ry * du[1]
            v = rx * dv[0] + ry * dv[1]
            if -2.4 <= u <= 2.4 and abs(v) <= 4.6:  # head block
                if u < -1.4:
                    col = c["head_hi"]
                elif u > 1.4:
                    col = c["head_lo"]
                else:
                    col = c["head"]
                if abs(v) < 0.75 and abs(u) < 0.9:  # glowing rune in the middle of the face
                    col = c["rune"]
                px[(x, y)] = col
            elif 2.4 < u <= 11.6 and abs(v) <= 0.75:  # handle
                band = int((u - 2.4) / 1.4) % 2
                px[(x, y)] = c["leather"] if band == 0 else c["leather_lo"]
            elif 11.6 < u <= 12.6 and abs(v) <= 1.3:  # strap loop at the end
                px[(x, y)] = c["strap"]
    return px


def mjolnir_icon(outline_only: bool = False) -> Image.Image:
    px = mjolnir_pixels()
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    edge = set()
    for (x, y) in px:
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) not in px and 0 <= nx < 16 and 0 <= ny < 16:
                edge.add((nx, ny))
    if outline_only:
        # Thrown: a faint glowing outline where the hammer used to be.
        for (x, y) in edge:
            img.putpixel((x, y), (170, 215, 255, 170))
        return img
    for (x, y), col in px.items():
        img.putpixel((x, y), (*col, 255))
    for (x, y) in edge:
        img.putpixel((x, y), (*MJOLNIR_COLORS["outline"], 255))
    return img


def main() -> None:
    armor = PACK / "assets/minecraft/textures/models/armor"
    items = PACK / "assets/minecraft/textures/item"
    custom = PACK / "assets/marvel/textures/item"
    for d in (armor, items, custom):
        d.mkdir(parents=True, exist_ok=True)

    layer1().save(armor / "iron_layer_1.png")
    layer2().save(armor / "iron_layer_2.png")
    for name, rows in ICONS.items():
        target = custom if name in ("repulsor", "thruster") else items
        icon(rows).save(target / f"{name}.png")

    # Spider-Man: empty (dye-tinted) base layers, the design lives in the untinted overlays
    empty_layer = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    empty_icon = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    empty_layer.save(armor / "leather_layer_1.png")
    empty_layer.save(armor / "leather_layer_2.png")
    spider_layer1().save(armor / "leather_layer_1_overlay.png")
    spider_layer2().save(armor / "leather_layer_2_overlay.png")
    for name, rows in SPIDER_ICONS.items():
        empty_icon.save(items / f"{name}.png")
        icon(rows).save(items / f"{name}_overlay.png")

    # Thor
    mjolnir_icon().save(custom / "mjolnir.png")
    mjolnir_icon(outline_only=True).save(custom / "mjolnir_thrown.png")
    print(f"wrote textures to {PACK}")


if __name__ == "__main__":
    main()
