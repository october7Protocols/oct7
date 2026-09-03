# תיק 7 באוקטובר — production build

Turns the Claude Design export into one self-contained HTML file that can be
published anywhere.

## Why a build step exists

The export cannot go live as it stands. At runtime the page fetches three files
the bundle never shipped:

| fetched | contains | status in the export |
|---|---|---|
| `./transcript.json` | **all 11 chapters and every quote** | missing |
| `./.image-slots.state.json` | the portrait sidecar | missing |
| `./halevi-aman.png` | one shipped portrait | missing |

On a real URL those are three 404s. What survives is the hero, the nine
prologue beats (hardcoded in the page's own script), the 06:29 beat and the
footer — no chapters, no cast strip, no timeline rail, no countdown. Verified
against the export directly: 10 beats render, 0 chapters.

`build.py` inlines all three into the bundle and rewrites the fetches to read
the inlined copies, so the output has **zero runtime network dependencies** —
no CDN, no Google Fonts, no sidecar. It is one file you can drop on any static
host, and it works from `file://` too.

## Use

```bash
python3 build.py --check          # readiness report, writes nothing (exit 1 if data missing)
python3 build.py                  # → dist/index.html
SITE_URL=https://example.org/oct7 python3 build.py   # adds canonical + og:url
```

Inputs, all next to `build.py`:

```
source-export.html        the Claude Design export (already here)
transcript.json           the document data          ← see SCHEMA.md
assets/portraits/<key>.png
assets/halevi-aman.png
```

Missing inputs are reported, never fatal — you can build at any stage and see
how far along the page is.

## What still needs supplying

**`transcript.json`.** It is the body of the piece and it is not recoverable
from the export — the export ships the design, not the data. It has to come out
of the editor project the canvas was built in (the file sat next to the
`.dc.html`), or be rebuilt from the source document. `SCHEMA.md` documents the
exact format the page expects, derived from the page's own code.

Portraits are optional in the sense that the build succeeds without them; the
cast strip and the quote screens then show name placeholders instead of faces.

## Verifying

```bash
python3 build.py --fixture        # builds test/dist/index.html from a synthetic fixture
python3 -m http.server 8781 --directory test/dist
```

The fixture contains **no real quotes** — it is placeholder text whose only job
is to exercise every render path. It lives in `test/` precisely so it cannot be
confused with, or overwrite, the real build.

Last fixture run rendered clean: 11 chapters, 133 items, 34 quote screens with
34 portraits loaded and 0 broken, 11 torn-paper cards, 11 redaction blocks,
15 timeline stops, 86 dated marks, chapter tints tracking — with **no console
errors and no failed requests**.

## Before publishing

- The page presents quotes attributed to named, real people from a real
  document. The build pipeline copies whatever `transcript.json` says,
  verbatim — accuracy against the source document is not something it can
  check. Have the quotes and citations checked against the document before this
  goes to a public URL.
- The footer's framing ("טענות ועמדות … לא קביעות של גוף בודק") is part of the
  design and should stay.
- `SITE_URL` is worth setting: without it the page ships no canonical URL and
  no `og:image`, so link previews will be bare.
- An `og.png` (1200×630) at the site root is referenced when `SITE_URL` is set;
  add one or link previews fall back to text only.
