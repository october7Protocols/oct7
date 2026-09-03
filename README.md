# תיק 7 באוקטובר

מענה ראש הממשלה לשאלות מבקר המדינה — scroll-driven presentation, Hebrew RTL.
11 chapters, 325 items, 22 speakers.

```
src/מענה ראש הממשלה v2.dc.html   the design — edit this
src/transcript.json               the document data — see SCHEMA.md
src/assets/portraits/<key>.png    one per speaker key
src/.image-slots.state.json       (optional) the editor's portrait file
build.py                          → dist/index.html
vendor/export-shell.html          runtime + fonts, not edited by hand
```

## Why there is a build step

Opened straight from `src/`, the design fetches five things that do not exist
on a live URL:

| fetched at runtime | contains |
|---|---|
| `./transcript.json` | **all 11 chapters and every quote** |
| `./.image-slots.state.json` | the portrait sidecar |
| `./halevi-aman.png` | the AMAN-era portrait |
| unpkg.com | React + ReactDOM |
| fonts.googleapis.com | Heebo, IBM Plex Mono |

Publishing the folder as-is gives you three 404s and two third-party requests.
What survives is the hero, the nine prologue beats and the footer — no
chapters, no cast strip, no timeline rail. (That is exactly what the earlier
`export.html` did: 10 beats, 0 chapters.)

`build.py` inlines all five and emits **one self-contained file** with zero
runtime network dependencies. It works on any static host and from `file://`.

## Working on it

```bash
python3 build.py                    # → dist/ (index.html + portraits/)
python3 build.py --check            # readiness report, writes nothing, exit 1 if broken
python3 build.py --inline-portraits # everything in one file, slower first paint
open dist/index.html
```

By default the photos are copied to `dist/portraits/` and referenced as
sibling files, so the browser fetches them lazily as the reader scrolls
instead of making everyone download ~4 MB of base64 before the first paint.
`dist/` is the whole site — `index.html` plus that folder.

To edit the design, open `src/מענה ראש הממשלה v2.dc.html` — in Claude Design
for visual editing, or in any editor for the markup and the component script
at the bottom. To edit the content, edit `src/transcript.json` against
`SCHEMA.md`. Then rebuild.

`src/index.html` is a dev wrapper that frames the design directly; it needs a
local server (`python3 -m http.server`) and the network for React and the
fonts. It is for editing, not for publishing — publish `dist/index.html`.

## Deploying

`.github/workflows/deploy.yml` builds on every push to `main` and publishes
`dist/` to GitHub Pages. It runs `--check` first, so a commit that breaks the
transcript fails the build instead of publishing a page with no chapters.

Two things to set on the repo once:

1. **Settings → Pages → Source: GitHub Actions**
2. **Settings → Variables → Actions → new variable `SITE_URL`**, e.g.
   `https://<user>.github.io/<repo>` — without it the page ships no canonical
   URL and no `og:image`, so link previews are bare.

Any static host works just as well: `dist/index.html` is the whole site.

## State

Everything renders: all 11 chapters, 325 items, 116 quote screens with all
116 portraits loading, the 22-person cast strip, 54 document cards, 17
comptroller questions, the timeline rail with 40 stops and 147 dated marks,
chapter tints, the countdown chip, and the rail and cast jump targets. Built
and loaded with **no console errors and no external requests**.

## The reading filter

The cast strip doubles as the control: click a speaker to read only their own
words, click again to clear. A period row underneath narrows further, and it
offers only the periods that speaker actually spoke in, so no chip can lead to
an empty page.

Filtering keeps the speaker's quotes and the chapter headings they sit under,
and drops the document's narration around them — the claims and memos are not
that speaker's words. The prologue hides too; it is the way into the document,
not into one person. The timeline rail re-measures against the filtered set,
so it shows only the years and months that speaker spoke in.

## Before this goes public

- The page attributes quotes to named, real people from a real document. The
  build copies `transcript.json` verbatim and cannot check any of it against
  the source. Have the quotes and citations verified before publishing.
- Keep the footer's framing (*טענות ועמדות … לא קביעות של גוף בודק*) — it is
  what marks the material as the speakers' claims rather than findings.
- Add a 1200×630 `og.png` if you want real link previews.
