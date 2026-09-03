# `transcript.json` — the data contract

Derived from the page's own code (`build()` and `Component.findDate()` in the
canvas logic). Everything here is what the existing design already expects —
nothing was invented for it.

## Top level

```json
{
  "speakers": { "<key>": { "name": "...", "role": "..." } },
  "chapters": [ ... ]
}
```

`speakers` — one entry per person quoted. The key is short and stable
(`netanyahu`, `halevi`, `bar`, `aman`, `shabak`, …); it links a quote to a name,
a role and a portrait file.

## Chapter

```json
{
  "id": "policy-gaza",
  "n": 4,
  "title": "מדיניות ישראל כלפי עזה",
  "page": "17",
  "questions": ["שאלת המבקר הראשונה?", "שאלה שנייה?"],
  "items": [ ... ]
}
```

| field | required | notes |
|---|---|---|
| `id` | yes | becomes the anchor `#ch-<id>`. `"oct7"` is special: see *Dates*. |
| `n` | yes | chapter number. `0` = unnumbered — no "פרק NN" badge, kept out of the top nav. |
| `title` | yes | shown full-bleed; the nav truncates past 44 chars. |
| `page` | no | page label in the source document. |
| `questions` | no | if present, renders the comptroller's question block with his portrait. |
| `items` | yes | the chapter body, in reading order. |

## Items

Every item needs a `kind`. Unknown kinds render as nothing, silently.

| `kind` | fields | renders as |
|---|---|---|
| `speech` | `speaker`, `text`, `role?`, `note?`, `source?` | full-screen portrait + quote (auto-wrapped in “ ”). The centrepiece. |
| `claim` | `text`, `source?`, `note?` | indented paragraph with a red rule. |
| `quote` | `text`, `source?` | torn-paper card, highlighter marker. `\n` is preserved. |
| `heading` | `text`, `note?` | red block heading inside a chapter. |
| `list` | `title`, `items[]` | boxed numbered list, blue top rule. |
| `timeline` | `time`, `title`, `text` | red-bar row with a big clock time (`"06:29"`). |
| `note` | `text` | quiet editorial paragraph. `\n` preserved. |
| `stage` | `text` | centred stage direction. |
| `source` | `text` | small mono citation line. Hidden when the *showSources* prop is off. |
| `redacted` | `text` | black box + caption, for material not cleared for publication. |

`speaker` must be a key in `speakers`. `source` is where citations belong —
it is also where dates are read from.

## Dates — how the timeline rail is built

There is **no date field**. The page scans each item's `source`, then `text`,
then `title` for the first `dd.mm.yyyy` and uses that:

```json
{ "kind": "speech", "speaker": "bar",
  "text": "…",
  "source": "(דיון קבינט, 12.02.2023 — פרק 4 במסמך)" }
```

That one string drives all of it: the countdown chip, the item's stop on the
right-hand rail, and the days-to-7-October arithmetic (`Date.UTC(2023,9,7)`
minus the item's date).

- **An item with no `dd.mm.yyyy` anywhere never appears on the rail.** It still
  renders; it just isn't a dated stop.
- Inside a chapter whose `id` is `"oct7"`, undated items fall back to
  07.10.2023 — that chapter is timestamped by clock time, not by date.
- Rail stops group as one per year up to 2022, then one per month through 2023.

## The AMAN attribution rule

Already in the code, worth knowing because it affects which portraits you need:
a `speech` with `speaker: "aman"` whose contextual date falls between
**01.09.2014 and 31.03.2018** is re-attributed to `halevi` by name and rendered
with a separate era portrait keyed `halevi-aman`. Outside that window `aman`
quotes stay unattributed. Context date carries forward from the last dated item
in the chapter.

## Portraits

`assets/portraits/<key>.png` — filename stem must equal the speaker key.
`.png`, `.jpg`, `.jpeg`, `.webp` all work.

Also expected:

- `assets/portraits/comptroller.png` — Matanyahu Englman, used by the cast strip
  and every question block.
- `assets/halevi-aman.png` — the AMAN-era portrait for the rule above.

Cut-out PNGs with transparency look best: the design clips its print-dot screen
to the image's own alpha, so a cut-out stays cut out instead of sitting on a
grey plate. Portraits are inlined as base64, so keep them reasonably sized
(≈300–500 KB each) — they land in the single output file.

`python3 build.py --check` lists every speaker key that has no portrait.

## Minimal working example

```json
{
  "speakers": {
    "netanyahu": { "name": "בנימין נתניהו", "role": "ראש הממשלה" },
    "bar":       { "name": "רונן בר",       "role": "ראש השב״כ" }
  },
  "chapters": [
    {
      "id": "policy-gaza", "n": 4, "title": "מדיניות ישראל כלפי עזה", "page": "17",
      "questions": ["מה הייתה מדיניות הממשלה כלפי רצועת עזה?"],
      "items": [
        { "kind": "heading", "text": "רקע" },
        { "kind": "speech", "speaker": "bar",
          "text": "…",
          "source": "(דיון קבינט, 12.02.2023 — פרק 4 במסמך)" },
        { "kind": "claim", "text": "…",
          "source": "(מענה ראש הממשלה, עמ׳ 18)" },
        { "kind": "redacted", "text": "קטע שלא אושר לפרסום" }
      ]
    }
  ]
}
```
