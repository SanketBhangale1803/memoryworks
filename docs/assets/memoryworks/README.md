# MemoryWorks identity

The mark is a glowing **M**: one continuous ribbon that folds over itself, lit
from within in a blue → violet → pink → peach gradient on black. It was drawn
as a raster, so it is used as an image, not rebuilt in vector.

Use **MemoryWorks** as the product name and **memoryworks.app** as the address.

## Files

All live in `frontend/public/memoryworks/`:

| File | Use |
|---|---|
| `logo-source.png` | The original 1254 × 1254 artwork on black. The master; everything below is derived from it. |
| `mark.png` | The mark with transparency (alpha from its own brightness). For dark backgrounds — the public site uses it. |
| `mark-on-black.png` | The mark on black, square, edges faded to true black. For dark media such as video and social posts. |
| `app-icon.png`, `app-icon-512.png` | The mark on a black rounded tile. For light backgrounds, the app sidebar, and anywhere an icon is expected. |
| `apple-icon.png`, `favicon-64.png` | Small tiles. `frontend/app/icon.png` and `apple-icon.png` serve the browser icons. |

`frontend/public/og.png` is the 1200 × 630 social preview; `logo.png`,
`logo-icon.png`, and `logo.jpg` in `frontend/public/` are the same mark at the
paths older links used.

## Colour

Sampled from the mark: blue `#50A8FC`, violet `#A168FA`, lavender `#EAB8FA`,
pink `#F485AD`, coral `#FC786D`, peach `#FECB91`, deep violet `#360B78`, navy
`#0C1F77`.

- **Public site** (`frontend/app/site.css`): near-black `#050507`, the full
  gradient used sparingly — one headline phrase, one border, the light behind
  the hero.
- **App** (`frontend/app/globals.css`): light neutrals with a faint violet cast;
  the accent is violet `#4F2CC7`, the logo's deep violet made dark enough to read
  on white. Sign-in uses the dark site palette.

## Rules

- Show the mark on black or near-black, or inside its tile. On a light
  background, always use the tile — the bare mark's dark folds wash out.
- Keep clear space of at least a quarter of the mark's width around it.
- Do not recolour, outline, add a second glow, or rotate it.

## The previous mark

The folded, two-ribbon **M** in teal and aqua (`build-brand.py`,
`render-brand.mjs`, `memoryworks-brand-board.png`, and the brand-kit zip in this
folder) is superseded. It is kept as a record; its exported files were removed
from the site.
