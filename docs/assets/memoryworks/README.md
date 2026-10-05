# MemoryWorks identity

The mark is three slanted, rounded bars rising left to right — read as a
stylised **M** — above the **MemoryWorks** wordmark. It is one colour: black on
light, white on dark. Both the symbol and the wordmark are traced vector
outlines, so no font is needed to draw the logo.

Use **MemoryWorks** as the product name and **memoryworks.app** as the address.

## Files

The master is `frontend/public/memoryworks/logo.svg` (symbol stacked over the
wordmark, black, transparent). `build-from-svg.py` in this folder derives
everything else from it — rerun it after changing the master:

    python3 docs/assets/memoryworks/build-from-svg.py

All live in `frontend/public/memoryworks/` unless noted:

| File | Use |
|---|---|
| `logo.svg`, `logo-white.svg` | The stacked logo. The master, and the same in white for dark backgrounds. |
| `logo.png`, `logo-transparent.png` | The stacked logo as supplied, 4096 × 3186 — on white, and on transparent. |
| `symbol.svg`, `symbol-white.svg` | The three bars alone, tightly cropped. |
| `lockup.svg`, `lockup-white.svg` | Horizontal: the symbol beside the wordmark. For navigation bars. |
| `app-icon.png`, `app-icon-512.png`, `apple-icon.png`, `favicon-64.png` | The white symbol on a near-black rounded tile. |
| `frontend/components/brandPaths.ts` | The path data the site draws inline (`BrandMark`, `BrandLockup` in `BrandLogo.tsx`). Generated — do not edit. |

`frontend/app/icon.png` and `apple-icon.png` serve the browser icons;
`frontend/public/og.png` is the 1200 × 630 social preview; `logo.png`,
`logo-icon.png`, and `logo.jpg` in `frontend/public/` are the tile at the paths
older links used.

## In the site

`BrandMark` and `BrandLockup` draw in `currentColor`, so they take the colour of
the text around them: ink in the light app, white on the dark public site and
sign-in page. Size the symbol by width and the lockup by height.

The site palette is separate from the logo: the public site
(`frontend/app/site.css`) is near-black `#050507` with a blue → violet → pink →
peach gradient used sparingly; the app (`frontend/app/globals.css`) is light
neutrals with a violet `#4F2CC7` accent.

## Rules

- Black or white only. Do not recolour it, fill it with the gradient, outline,
  or rotate it.
- On a busy or mid-tone background, use the app-icon tile.
- Keep clear space of at least the height of one bar around it.

## Previous marks

The glowing gradient **M** (2026-09-30) and, before it, the folded two-ribbon
**M** in teal and aqua (`build-brand.py`, `render-brand.mjs`,
`memoryworks-brand-board.png`, and the brand-kit zip in this folder) are
superseded. Their files were removed from the site.
