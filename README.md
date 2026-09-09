# תיק 7 באוקטובר

מענה ראש הממשלה לשאלות מבקר המדינה, scroll-driven presentation, Hebrew RTL.
12 chapters, 345 items, 23 speakers.

```
src/מענה ראש הממשלה v2.dc.html   the design, edit this
src/transcript.json               the document data, see SCHEMA.md
src/assets/portraits/<key>.png    one per speaker key
src/.image-slots.state.json       (optional) the editor's portrait file
src/landing.html                  the entry page, edit this too
src/i18n.json                     every string, in seven languages
build.py                          → dist/
vendor/export-shell.html          runtime + fonts, not edited by hand
```

## Why there is a build step

Opened straight from `src/`, the design fetches five things that do not exist
on a live URL:

| fetched at runtime | contains |
|---|---|
| `./transcript.json` | **all 12 chapters and every quote** |
| `./.image-slots.state.json` | the portrait sidecar |
| `./halevi-aman.png` | the AMAN-era portrait |
| unpkg.com | React + ReactDOM |
| fonts.googleapis.com | Heebo, IBM Plex Mono |

Publishing the folder as-is gives you three 404s and two third-party requests.
What survives is the hero, the nine prologue beats and the footer, no
chapters, no cast strip, no timeline rail. (That is exactly what the earlier
`export.html` did: 10 beats, 0 chapters.)

`build.py` inlines all five and emits **one self-contained file** with zero
runtime network dependencies. It works on any static host and from `file://`.

## Working on it

```bash
python3 build.py                    # → dist/ (entry page + doc/ + fonts/)
python3 build.py --check            # readiness report, writes nothing, exit 1 if broken
python3 build.py --inline-portraits # everything in one file, slower first paint
open dist/index.html
```

`dist/` is the whole site:

```
dist/index.html        the entry page   ~25 KB, on screen at once
dist/fonts/            3 woff2 faces    ~55 KB, pulled out of the bundle
dist/doc/index.html    the document     1.5 MB
dist/doc/portraits/    24 photos        4.2 MB, fetched as the reader scrolls
dist/CNAME             the custom domain
```

The split is the point: the document is a 1.5 MB bundle plus 4 MB of
portraits, so the first thing a visitor meets is a separate light page. The
photos are copied as sibling files rather than base64 so the browser fetches
them lazily instead of making everyone download 4 MB before the first paint.

The entry page is plain HTML and vanilla JS, no React, no build framework.
Its copy lives in `src/i18n.json` (the `land*` keys) like everything else, and
its fonts are the same faces the document uses, extracted from the bundle so
it makes no third-party request either. The language a reader picks there is
the language the document opens in: both read `localStorage['oct7.lang']`.

To edit the design, open `src/מענה ראש הממשלה v2.dc.html`, in Claude Design
for visual editing, or in any editor for the markup and the component script
at the bottom. To edit the content, edit `src/transcript.json` against
`SCHEMA.md`. Then rebuild.

`src/index.html` is a dev wrapper that frames the design directly; it needs a
local server (`python3 -m http.server`) and the network for React and the
fonts. It is for editing, not for publishing, publish `dist/`.

## Deploying

`.github/workflows/deploy.yml` builds on every push to `main` and publishes
`dist/` to GitHub Pages. It runs `--check` first, so a commit that breaks the
transcript fails the build instead of publishing a page with no chapters.

The site is published at **october7.co**. `src/CNAME` carries the domain and
is copied into `dist/` on every build, GitHub Pages reads it from the
published artifact, so removing it would drop the site back to the github.io
address on the next deploy.

Already set on the repo: Pages source is GitHub Actions, the custom domain is
`october7.co`, and the Actions variable `SITE_URL` is `https://october7.co`
(it feeds the canonical URL and `og:image`).

Any static host works just as well: `dist/` is the whole site.

## State

Everything renders: all 12 chapters, 345 items, 116 quote screens with all
116 portraits loading, the 22-person cast strip, 54 document cards, 17
comptroller questions, the timeline rail with 40 stops and 147 dated marks,
chapter tints, the countdown chip, and the rail and cast jump targets. Built
and loaded with **no console errors and no external requests**.

## The reading filter

The cast strip doubles as the control: the whole card is the target, portrait
included. Pick as many speakers as you want and optionally a period, then
press סנן, selecting only builds a draft, so the document does not rearrange
itself between your first pick and your last, and nothing scrolls until you
apply. The period row offers only periods the chosen speakers actually spoke
in, so no chip can lead to an empty page.

Filtering keeps the speaker's quotes and the chapter headings they sit under,
and drops the document's narration around them, the claims and memos are not
that speaker's words. The prologue hides too; it is the way into the document,
not into one person. The timeline rail re-measures against the filtered set,
so it shows only the years and months that speaker spoke in.

## Languages

`src/i18n.json` holds the interface copy for Hebrew, English, French, Arabic,
German, Spanish and Russian, plus translated speaker names, posts and chapter
titles.
The picker sits top-left; the choice is remembered per reader. Hebrew and
Arabic render RTL, the rest LTR, `dir` follows the language, and the
timeline gutter stays on the right in every language.

Everything is translated, the quotations included: 520 document strings per
language on top of the interface copy. Because a translated quotation is no
longer the quotation, every translated language carries a `srcNote` marking
the quotations as an unofficial translation, and each quoted element keeps its
Hebrew original on its `title` attribute, hover, and you get the words that
were actually said.

Translation is per-string with a Hebrew fallback, so a language that is
missing a key shows the Hebrew rather than an empty screen.

To add a language, add a key to `i18n.json` with the same fields as `he`;
the picker and the build pick it up with no code change. `build.py` fails if
the file is missing or has no `he` entry to fall back to.

## Before this goes public

- The page attributes quotes to named, real people from a real document. The
  build copies `transcript.json` verbatim and cannot check any of it against
  the source. Have the quotes and citations verified before publishing.
- Keep the footer's framing (*טענות ועמדות … לא קביעות של גוף בודק*), it is
  what marks the material as the speakers' claims rather than findings.
- Add a 1200×630 `og.png` at `dist/` if you want real link previews. Both
  pages already point at it.
