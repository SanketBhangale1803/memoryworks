"""Derive every MemoryWorks logo asset from the one vector master.

Master: frontend/public/memoryworks/logo.svg — the symbol (three slanted bars)
stacked over the wordmark, traced to outlines, black on transparent.

Writes the React path data, the symbol/lockup SVGs, the browser and app icons,
and the social preview. Needs rsvg-convert and Pillow. Run from anywhere:

    python3 docs/assets/memoryworks/build-from-svg.py
"""
import re
import subprocess
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[3]
FRONT = REPO / "frontend"
OUT = FRONT / "public/memoryworks"
MASTER = (OUT / "logo.svg").read_text()

INK = "#050507"   # the public site's near-black
WHITE = "#ffffff"
PAD = 1           # breathing room around a tight box, in master units


def paths(group: str) -> list[str]:
    body = re.search(rf'<g id="{group}"[^>]*>(.*?)</g>', MASTER, re.S).group(1)
    return re.findall(r'<path d="([^"]+)"', body)


SYMBOL, WORDMARK = paths("symbol"), paths("wordmark")


def render(svg: str, out: Path, width: int, height: int | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["rsvg-convert", "-w", str(width)] + (["-h", str(height)] if height else []) + ["-o", str(out)]
    subprocess.run(args, input=svg.encode(), check=True)


def bbox(group: list[str]) -> tuple[float, float, float, float]:
    """Tight box of a group in master units, measured from a raster (control points overshoot)."""
    scale = 8
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{747*scale}" height="{581*scale}" '
           f'viewBox="92 115 747 581"><g fill-rule="evenodd">'
           + "".join(f'<path d="{d}"/>' for d in group) + "</g></svg>")
    tmp = OUT / ".bbox.png"
    render(svg, tmp, 747 * scale)
    l, t, r, b = Image.open(tmp).getchannel("A").getbbox()
    tmp.unlink()
    return 92 + l / scale - PAD, 115 + t / scale - PAD, (r - l) / scale + 2 * PAD, (b - t) / scale + 2 * PAD


SYM_BOX, WORD_BOX = bbox(SYMBOL), bbox(WORDMARK)


def fmt(box) -> str:
    return " ".join(f"{v:g}" for v in (round(n, 2) for n in box))


def g(group: list[str], fill: str, transform: str = "") -> str:
    t = f' transform="{transform}"' if transform else ""
    return f'<g fill="{fill}" fill-rule="evenodd"{t}>' + "".join(f'<path d="{d}"/>' for d in group) + "</g>"


def svg(view: str, body: str, title: str, size: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}"{size} role="img" aria-label="{title}">'
            f"<title>{title}</title>{body}</svg>\n")


# ---- horizontal lockup: symbol beside the wordmark, its bars spanning the caps
CAP_TOP, BASELINE = 545.0, 610.5          # the wordmark's cap height, read off the master
sym_scale = (BASELINE - CAP_TOP) * 1.12 / SYM_BOX[3]
sym_w, sym_h = SYM_BOX[2] * sym_scale, SYM_BOX[3] * sym_scale
gap = 34
lock_x0 = WORD_BOX[0] - gap - sym_w
sym_y = (CAP_TOP + BASELINE) / 2 - sym_h / 2
LOCK_TRANSFORM = f"translate({lock_x0:.2f} {sym_y:.2f}) scale({sym_scale:.5f}) translate({-SYM_BOX[0]:.2f} {-SYM_BOX[1]:.2f})"
lock_top = min(sym_y, WORD_BOX[1])
LOCK_BOX = (lock_x0, lock_top, WORD_BOX[0] + WORD_BOX[2] - lock_x0, max(sym_y + sym_h, WORD_BOX[1] + WORD_BOX[3]) - lock_top)


def lockup(fill: str) -> str:
    return g(SYMBOL, fill, LOCK_TRANSFORM) + g(WORDMARK, fill)


# ---- vector files
for suffix, fill in (("", "#000000"), ("-white", WHITE)):
    (OUT / f"symbol{suffix}.svg").write_text(svg(fmt(SYM_BOX), g(SYMBOL, fill), "MemoryWorks"))
    (OUT / f"lockup{suffix}.svg").write_text(svg(fmt(LOCK_BOX), lockup(fill), "MemoryWorks"))
(OUT / "logo-white.svg").write_text(MASTER.replace('fill="#000000"', f'fill="{WHITE}"'))


# ---- icons: the white symbol on a near-black rounded tile
def tile(size: int, radius: float = .225, inset: float = .17) -> str:
    w = size * (1 - 2 * inset)
    s = w / SYM_BOX[2]
    y = (size - SYM_BOX[3] * s) / 2
    body = (f'<rect width="{size}" height="{size}" rx="{size * radius:g}" fill="{INK}"/>'
            + g(SYMBOL, WHITE, f"translate({size * inset:g} {y:.2f}) scale({s:.5f}) translate({-SYM_BOX[0]:g} {-SYM_BOX[1]:g})"))
    return svg(f"0 0 {size} {size}", body, "MemoryWorks")


for out, size in ((OUT / "app-icon-512.png", 512), (OUT / "app-icon.png", 256), (OUT / "apple-icon.png", 180),
                  (FRONT / "public/logo.png", 1024), (FRONT / "public/logo-icon.png", 512)):
    render(tile(512), out, size)
# browser tabs draw it at 16-32px: let the symbol fill more of the tile
for out, size in ((OUT / "favicon-64.png", 64), (FRONT / "app/icon.png", 512)):
    render(tile(512, inset=.1), out, size)
# iOS draws its own rounded corners; give it the full square
render(tile(512, radius=0), FRONT / "app/apple-icon.png", 180)
Image.open(FRONT / "public/logo.png").convert("RGB").save(FRONT / "public/logo.jpg", quality=92)


# ---- social preview, 1200 x 630
W, H = 1200, 630
word_scale = 560 / WORD_BOX[2]
og_sym_scale = 300 / SYM_BOX[2]
og = (
    f'<rect width="{W}" height="{H}" fill="{INK}"/>'
    + g(SYMBOL, WHITE, f"translate(450 150) scale({og_sym_scale:.5f}) translate({-SYM_BOX[0]:g} {-SYM_BOX[1]:g})")
    + g(WORDMARK, WHITE, f"translate(320 330) scale({word_scale:.5f}) translate({-WORD_BOX[0]:g} {-WORD_BOX[1]:g})")
    + f'<text x="600" y="470" text-anchor="middle" fill="#b8b6cb" font-family="Helvetica Neue, Helvetica, Arial" '
      f'font-size="30">The memory layer for engineering organizations.</text>'
    + f'<text x="600" y="540" text-anchor="middle" fill="#7f7c96" font-family="Helvetica Neue, Helvetica, Arial" '
      f'font-size="22" letter-spacing="1">memoryworks.app</text>'
)
render(svg(f"0 0 {W} {H}", og, "MemoryWorks", f' width="{W}" height="{H}"'), FRONT / "public/og.png", W, H)


# ---- React path data
def ts_list(group):
    return "[\n" + "".join(f'  "{d}",\n' for d in group) + "]"


(FRONT / "components/brandPaths.ts").write_text(
    "// Generated by docs/assets/memoryworks/build-from-svg.py from public/memoryworks/logo.svg. Do not edit.\n\n"
    f'export const SYMBOL_VIEWBOX = "{fmt(SYM_BOX)}";\n'
    f"export const SYMBOL_PATHS = {ts_list(SYMBOL)};\n\n"
    f'export const WORDMARK_VIEWBOX = "{fmt(WORD_BOX)}";\n'
    f"export const WORDMARK_PATHS = {ts_list(WORDMARK)};\n\n"
    f'export const LOCKUP_VIEWBOX = "{fmt(LOCK_BOX)}";\n'
    f'export const LOCKUP_SYMBOL_TRANSFORM = "{LOCK_TRANSFORM}";\n'
)
print("symbol", fmt(SYM_BOX), "| wordmark", fmt(WORD_BOX), "| lockup", fmt(LOCK_BOX))
