#!/usr/bin/env python3
"""
Build the publishable page from the editable source in src/.

    src/מענה ראש הממשלה v2.dc.html   the design — this is what you edit
    src/transcript.json               the document data      (see SCHEMA.md)
    src/assets/portraits/<key>.png    one per speaker key
    src/assets/halevi-aman.png        the AMAN-era portrait

    src/landing.html                  the entry page — this too

    -> dist/index.html                the entry page, light, loads instantly
    -> dist/doc/index.html            the document, one self-contained file

Why a build step
----------------
Opened directly, the design fetches five things that do not exist on a live
URL: ./transcript.json (all 11 chapters — the entire body), the editor's
./.image-slots.state.json portrait sidecar, ./halevi-aman.png, React from
unpkg, and Heebo + IBM Plex Mono from Google Fonts. This build inlines every
one of them, so the output has zero runtime network dependencies and works
from file:// as well as from a web server.

The runtime plumbing (the DC runtime, React, and the fifteen subsetted woff2
faces) is carried in vendor/export-shell.html — a Claude Design export whose
self-extracting loader is reused verbatim. Only the design itself is taken
from src/, so editing the .dc.html is all a contributor has to do.

Usage
-----
    python3 build.py                    # -> dist/index.html
    python3 build.py --check            # readiness report, writes nothing
    python3 build.py --inline-portraits # fold photos in too (one file, slower first paint)
    SITE_URL=https://example.org python3 build.py     # adds canonical + og:url
"""

import base64
import datetime
import gzip
import json
import mimetypes
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
SHELL = os.path.join(HERE, "vendor", "export-shell.html")
DIST = os.path.join(HERE, "dist")
# The document is the heavy part: a 1.5 MB bundle plus the portraits. It sits
# one level down so the root can be a page that is on screen at once.
DOC = os.path.join(DIST, "doc")
LANDING = os.path.join(SRC, "landing.html")
# Anything the entry page shows. Copied to dist/assets/ wholesale.
LANDING_ASSETS = os.path.join(SRC, "assets", "landing")
# Portraits the entry page shows. They are the document's own cut-outs, kept
# in one place rather than duplicated into assets/landing/; naming them here
# means a renamed portrait fails the build instead of leaving a broken image
# on the front page.
LANDING_PORTRAITS = ("bennett",)
CONSENT = os.path.join(SRC, "consent.js")
# The share image the meta tags point at. Every link shared to WhatsApp,
# Telegram, Facebook or X shows this or shows nothing.
OG_IMAGE = os.path.join(SRC, "assets", "og.png")

# A chapter marked "draft": true is kept out of the published document and
# out of every language of it. It appears only under /preview/, which is
# linked from nowhere, excluded from the sitemap, and served with a noindex
# so a search engine that stumbles on it does not keep it.
PREVIEW = "preview"
TRANSCRIPT = os.path.join(SRC, "transcript.json")
I18N = os.path.join(SRC, "i18n.json")
PORTRAIT_DIR = os.path.join(SRC, "assets", "portraits")
HALEVI = os.path.join(SRC, "assets", "halevi-aman.png")
SIDECAR = os.path.join(SRC, ".image-slots.state.json")

SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

# Google Tag Manager container, e.g. GTM-ABC1234. Empty by default, and an
# empty value ships no third-party script at all — the page keeps its
# zero-external-request property until someone deliberately sets this.
# Whatever measurement actually happens is configured inside the container,
# not here, so adding or removing a pixel never needs a rebuild.
GTM_ID = os.environ.get("GTM_ID", "").strip()
if GTM_ID and not re.match(r"^GTM-[A-Z0-9]{4,10}$", GTM_ID):
    sys.exit("GTM_ID must look like GTM-ABC1234, got %r" % GTM_ID)

# The site's name, as it identifies itself to a reader, a search engine and
# a link preview. The document is one publication inside it, so the document
# keeps its own title and the site name sits alongside it.
SITE_NAME = "חשיפת הפרוטוקולים"
# The day the site went public. datePublished must not move when the site
# is rebuilt; dateModified is what tracks a rebuild.
PUBLISHED = "2026-09-08"

# One URL per language, so a search engine can index all seven. Hebrew keeps
# the bare paths — october7.co is already shared and must not break — and the
# rest sit under a prefix.
#
#     /            /doc/            he
#     /en/         /en/doc/         en   … and so on
#
# The pages still switch language in place; the picker's links are what a
# crawler follows and what a reader copies out of the address bar.
def lang_path(lang, doc=False):
    base = "/" if lang == "he" else "/%s/" % lang
    return base + "doc/" if doc else base


OG_LOCALE = {"he": "he_IL", "en": "en_US", "fr": "fr_FR", "ar": "ar_AR",
             "de": "de_DE", "es": "es_ES", "ru": "ru_RU"}
TITLE = "תיק 7 באוקטובר: מענה ראש הממשלה לשאלות מבקר המדינה"
DESCRIPTION = (
    "מה שאל מבקר המדינה ומה ענה ראש הממשלה על אירועי 7 באוקטובר 2023. "
    "בתוך התשובות מובאים הדיונים הביטחוניים שקדמו להם, עם התאריך של כל ציטוט. "
    "הדברים הם טענות ועמדות של הדוברים ולא קביעות של גוף בודק."
)


def die(msg):
    sys.stderr.write("build: " + msg + "\n")
    sys.exit(1)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── source design ──────────────────────────────────────────────────────────

def find_design():
    names = [n for n in os.listdir(SRC) if n.endswith(".dc.html")]
    if len(names) != 1:
        die("expected exactly one .dc.html in src/, found %d" % len(names))
    return os.path.join(SRC, names[0])


CAMEL_ATTR = re.compile(r'(?<=[\s"])([a-z]+(?:[A-Z][a-zA-Z]*)+)=(")')


def normalise_camel_attrs(html):
    """DOMParser lowercases attribute names, which would silently drop the
    design's camelCase bindings (onClick on the timeline and cast buttons,
    viewBox on inline SVG). The runtime reads them from an `sc-camel-*`
    spelling instead; the exporter rewrites them and so must we, or every
    jump target on the page stops working.

    Left alone: `data-*`, `aria-*` and anything already prefixed, none of
    which can match, since the pattern requires an interior capital.
    """
    def sub(m):
        name = m.group(1)
        kebab = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()
        return "sc-camel-" + kebab + "=" + m.group(2)
    return CAMEL_ATTR.sub(sub, html)


def slice_design(design_html):
    """The three pieces a contributor edits: the markup between </helmet> and
    </x-dc>, the component script after it, and the responsive stylesheet.

    The published helmet comes from the export shell (that is where Google
    Fonts got resolved into the bundled woff2 faces), so anything else the
    src helmet holds is dropped. The one block that has to survive is the
    responsive stylesheet, which is why it carries a marker.
    """
    if "</helmet>" not in design_html or "</x-dc>" not in design_html:
        die("src design is not a canvas document (no <helmet>/<x-dc>)")
    body = design_html.split("</helmet>", 1)[1].split("</x-dc>", 1)[0]
    m = re.search(r'<script type="text/x-dc"[^>]*>(.*?)</script>', design_html, re.S)
    if not m:
        die("no <script type=\"text/x-dc\"> component script in the src design")
    css = re.search(r'<style data-om-responsive>.*?</style>', design_html, re.S)
    if not css:
        die("no <style data-om-responsive> block in the src design — the "
            "breakpoints would be silently dropped from the published page")
    return normalise_camel_attrs(body), m.group(0), css.group(0)


# ── shell (fonts + runtime, carried by the export) ─────────────────────────

def load_shell():
    lines = read(SHELL).split("\n")
    idx = next((i for i, l in enumerate(lines)
                if l.lstrip().startswith('"<!DOCTYPE html>')), None)
    if idx is None:
        die("vendor/export-shell.html has no __bundler/template payload")
    return lines, idx, json.loads(lines[idx].strip())


# ── payload patches ────────────────────────────────────────────────────────

FETCH_TRANSCRIPT_OLD = """    try {
      const res = await fetch('./transcript.json');
      this.setState({ data: await res.json() });
    } catch (e) { console.error('transcript load failed', e); }"""

FETCH_TRANSCRIPT_NEW = """    // Built for publication: the document data is inlined in the page
    // (see #dc-transcript), so there is no fetch to 404 on a live URL.
    try {
      const el = document.getElementById('dc-transcript');
      if (el && el.textContent.trim()) this.setState({ data: JSON.parse(el.textContent) });
      else console.error('transcript missing from bundle');
    } catch (e) { console.error('transcript parse failed', e); }"""

FETCH_SIDECAR_OLD = """    const map = {};
    try {
      const r = await fetch('./.image-slots.state.json', { cache: 'no-store' });
      if (!r.ok) throw new Error('no sidecar');
      const j = await r.json();
      for (const k in j) {
        if (k.indexOf('cast-') !== 0) continue;
        const v = j[k];
        const u = typeof v === 'string' ? v : (v && v.u);
        if (u && u.indexOf('data:image/') === 0) map[k.slice(5)] = u;
      }
    } catch (e) { /* no sidecar yet — placeholders stay */ }
    // Era-specific portrait shipped with the page, not user-dropped.
    map['halevi-aman'] = './halevi-aman.png';"""

FETCH_SIDECAR_NEW = """    const map = {};
    // Built for publication: the portrait map ships in the page (see
    // #dc-portraits) instead of being read from the editor's sidecar.
    // Values are sibling file paths by default, or data: URIs when the
    // page was built with --inline-portraits.
    try {
      const el = document.getElementById('dc-portraits');
      const j = el && el.textContent.trim() ? JSON.parse(el.textContent) : {};
      for (const k in j) if (typeof j[k] === 'string' && j[k]) map[k] = j[k];
    } catch (e) { console.error('portrait map parse failed', e); }"""

# The cast strip's <image-slot> is an editor element: it reads the sidecar
# itself, so it 404s on a live URL and shows placeholders even with the
# portraits inlined. Swap it for the plain div the chapter blocks already use,
# which paintPortraits() fills from the inlined map.
IMAGE_SLOT_OLD_RE = re.compile(
    r'<image-slot id="cast-\{\{ c\.key \}\}"[^>]*></image-slot>')
IMAGE_SLOT_NEW = (
    # The name is the fallback for a speaker with no photo. It sits behind
    # the portrait, and the portraits are transparent cut-outs, so
    # paintPortraits() hides it once a photo actually lands.
    '<div class="om-ph" style="position:absolute;inset:0;display:flex;'
    'align-items:center;justify-content:center;font-size:11px;color:#7a86b4;'
    'text-align:center;padding:6px;line-height:1.3">{{ c.name }}</div>'
    '<div data-portrait="{{ c.key }}" style="position:absolute;inset:0;'
    'background-size:cover;background-position:center 20%;'
    'background-repeat:no-repeat"></div>'
)


def template_line(html):
    """Serialise the page into the <script type="__bundler/template"> tag.

    Every "</" must be escaped or the HTML parser closes that tag at the
    first one and truncates the payload — which shows up as a page that
    renders 186 bytes and nothing else. This has been got wrong twice by
    hand; it lives in one place now.
    """
    return json.dumps(html, ensure_ascii=False).replace("</", "<\\u002F")


def write_text(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def json_for_script(obj):
    """Serialize for a <script type="application/json">. '<' is escaped so no
    payload can close the tag; the result is still valid JSON."""
    return json.dumps(obj, ensure_ascii=False,
                      separators=(",", ":")).replace("<", "\\u003C")


def data_uri(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return "data:" + mime + ";base64," + base64.b64encode(f.read()).decode("ascii")


def split_drafts(transcript):
    """Return (published, preview). Both are whole transcripts; the first has
    the draft chapters removed."""
    if not transcript:
        return transcript, transcript
    chapters = transcript.get("chapters") or []
    drafts = [c for c in chapters if c.get("draft")]
    if not drafts:
        return transcript, transcript
    published = dict(transcript)
    published["chapters"] = [c for c in chapters if not c.get("draft")]
    return published, transcript


def load_transcript():
    if not os.path.exists(TRANSCRIPT):
        return None, "missing src/transcript.json — the page would have no chapters"
    try:
        data = json.loads(read(TRANSCRIPT))
    except Exception as e:
        return None, "src/transcript.json is not valid JSON: %s" % e

    problems = []
    if not isinstance(data.get("chapters"), list) or not data["chapters"]:
        problems.append("no 'chapters' array")
    if not isinstance(data.get("speakers"), dict) or not data["speakers"]:
        problems.append("no 'speakers' map")
    speakers = data.get("speakers") or {}
    for ch in data.get("chapters") or []:
        for key in ("id", "n", "title"):
            if key not in ch:
                problems.append("chapter %r has no %r" % (ch.get("id", "?"), key))
        for j, it in enumerate(ch.get("items") or []):
            if "kind" not in it:
                problems.append("%s item %d has no 'kind'" % (ch.get("id"), j))
            elif it["kind"] == "speech":
                if not it.get("speaker"):
                    problems.append("%s item %d is a speech with no speaker" % (ch.get("id"), j))
                elif it["speaker"] not in speakers:
                    problems.append("%s item %d: unknown speaker %r"
                                    % (ch.get("id"), j, it["speaker"]))
    return data, "; ".join(problems[:8]) if problems else None


def speech_speakers(transcript):
    """Speaker keys that actually get a portrait screen, plus the two the
    design hardcodes."""
    keys = set()
    for ch in (transcript or {}).get("chapters") or []:
        for it in ch.get("items") or []:
            if it.get("kind") == "speech" and it.get("speaker"):
                keys.add(it["speaker"])
    keys.add("comptroller")
    # 'aman' stays in: only quotes dated 09/2014-03/2018 are re-attributed to
    # halevi, so the unnamed AMAN chief still needs his own portrait for
    # every quote outside that window.
    return keys


INLINE_PORTRAITS = "--inline-portraits" in sys.argv


def load_portraits(transcript):
    """Portrait key -> URL. By default the photos are copied into
    dist/portraits/ and referenced as sibling files, so the browser fetches
    them lazily as the reader scrolls instead of making everyone download
    ~5 MB of base64 before the first paint. --inline-portraits folds them
    into the HTML instead, for when a single file matters more than speed.

    Both sources are read, sidecar first, so a loose file in
    assets/portraits/ can override one photo without touching the sidecar.
    """
    out, notes = {}, []
    copies = {}   # dist-relative path -> source file to copy

    # The canvas editor stores every dropped photo in this one (hidden) file,
    # keyed "cast-<speaker>", as a data URI. Copying it out of the editor
    # project restores all the portraits at once.
    if os.path.exists(SIDECAR):
        try:
            for k, v in json.loads(read(SIDECAR)).items():
                if not k.startswith("cast-"):
                    continue
                u = v if isinstance(v, str) else (v or {}).get("u")
                if isinstance(u, str) and u.startswith("data:image/"):
                    out[k[5:]] = u
        except Exception as e:
            notes.append("could not read the portrait sidecar: %s" % e)

    def add(key, path):
        if INLINE_PORTRAITS:
            out[key] = data_uri(path)
        else:
            rel = "portraits/" + key + os.path.splitext(path)[1].lower()
            # Root-absolute: /en/doc/ and /doc/ are different directories but
            # share one set of photos, so a relative path would 404 in six of
            # the seven.
            out[key] = "/doc/" + rel
            copies[rel] = path

    if os.path.isdir(PORTRAIT_DIR):
        for name in sorted(os.listdir(PORTRAIT_DIR)):
            stem, ext = os.path.splitext(name)
            if ext.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                add(stem, os.path.join(PORTRAIT_DIR, name))
    if os.path.exists(HALEVI):
        add("halevi-aman", HALEVI)
    else:
        notes.append("no src/assets/halevi-aman.png")

    # A sidecar entry is a data URI with no file behind it, so it always
    # inlines; only keys that came from a file can be shipped separately.
    inlined = [k for k, v in out.items() if v.startswith("data:")]
    if inlined and not INLINE_PORTRAITS:
        notes.append("%d portrait(s) from the sidecar stay inlined "
                     "(no file to copy): %s" % (len(inlined), ", ".join(sorted(inlined))))

    missing = sorted(speech_speakers(transcript) - set(out))
    if missing:
        notes.append("%d of %d speakers have no portrait: %s"
                     % (len(missing), len(speech_speakers(transcript)),
                        ", ".join(missing)))
        if not os.path.exists(SIDECAR):
            notes.append("src/.image-slots.state.json is absent — that hidden "
                         "file in the editor project holds every photo already "
                         "placed on the canvas")
    return out, copies, "; ".join(notes) if notes else None


# Which element each string belongs in. The page's own script fills these
# from the reader's language; this fills them with the Hebrew at build time
# so the HTML that leaves the server is not an empty shell. Google runs
# scripts, but WhatsApp, Telegram, Slack, Facebook and X do not, and a
# shared link is how this site travels.
PRERENDER = {
    "k-title":      lambda t: t["landTitle"],
    "k-lead":       lambda t: t["landLead"],
    "k-nowlabel":   lambda t: t["landNowLabel"],
    "k-doctitle":   lambda t: (t["t1"] + " " + t["t2"]).strip(),
    "k-docsub":     lambda t: t["subtitle"] + " \u00b7 " + t["issued"],
    "k-enter":      lambda t: t["landEnter"],
    "k-newslabel":  lambda t: t["landNewsLabel"],
    "k-newspull":   lambda t: t["landNewsPull"],
    "k-newsquote":  lambda t: t["landNewsQuote"],
    "k-newsattr":   lambda t: t["landNewsAttr"],
    "k-newsnote":   lambda t: t["landNewsNote"],
    "k-benlabel":   lambda t: t["landBennettLabel"],
    "k-benpull":    lambda t: t["landBennettPull"],
    "k-benattr":    lambda t: t["landBennettAttr"],
    "k-soonlabel":  lambda t: t["landSoonLabel"],
    "k-soon":       lambda t: t["landSoon"],
    "k-soonnote":   lambda t: t["landSoonNote"],
    "k-foot":       lambda t: t["landFoot"],
}


def prerender(page, he):
    def esc(v):
        return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for el, pick in PRERENDER.items():
        pat = re.compile(r'(id="%s"[^>]*>)(</)' % re.escape(el))
        page, n = pat.subn(lambda m: m.group(1) + esc(pick(he)) + m.group(2), page, count=1)
        if not n:
            die("src/landing.html: nothing to prerender into #%s" % el)
    # The listen link's label sits in a bare span inside the anchor.
    pat = re.compile(r'(id="k-benlink"[\s\S]*?<span)></span>')
    page, n = pat.subn(
        lambda m: m.group(1) + ">" + esc(he["landBennettLink"]) + "</span>",
        page, count=1)
    if not n:
        die("src/landing.html: no span inside #k-benlink to prerender")
    # An unclosed tag here does not look broken — it silently swallows
    # everything after it into the link, which is how the "coming soon" block
    # ended up opening a radio programme.
    if page.count("</span>") != page.count("<span"):
        die("prerender left an unbalanced <span> in the entry page")
    return page


def json_ld(kind, title, description, path):
    """Structured data. Without it a search engine has to infer what the page
    is from prose; with it, the document is declared as an article with a
    date, a language and a publisher."""
    if not SITE_URL:
        return ""
    site = {"@type": "WebSite", "name": SITE_NAME, "url": SITE_URL + "/",
            "inLanguage": "he"}
    if kind == "site":
        data = dict(site, description=description)
    else:
        data = {"@type": "Article", "headline": title, "description": description,
                "inLanguage": "he", "datePublished": PUBLISHED,
                "dateModified": datetime.date.today().isoformat(),
                "mainEntityOfPage": SITE_URL + path,
                "isPartOf": site,
                "publisher": {"@type": "Organization", "name": SITE_NAME,
                              "url": SITE_URL + "/"},
                "image": SITE_URL + "/og.png"}
    data["@context"] = "https://schema.org"
    return ('<script type="application/ld+json">%s</script>'
            % json_for_script(data))


def gtm_head():
    """The consent gate, and the container it gates.

    Outside Israel nothing of Google's is fetched until the reader accepts.
    A banner that lets the container load and then asks is decoration, and
    under the GDPR it is not consent either. Inside Israel the container
    loads on sight, which is what was asked for.

    The gate runs before the page's own script and so cannot reach the
    page's i18n; build.py hands it the three strings it needs per language.
    """
    if not GTM_ID:
        return ""
    if not os.path.exists(CONSENT):
        die("src/consent.js is missing — GTM_ID is set but nothing would gate it")
    keys = ("consentText", "consentYes", "consentNo")
    strings = json.loads(read(I18N))
    subset = {}
    for lang, t in strings.items():
        picked = {}
        for k in keys:
            v = t.get(k) or strings.get("he", {}).get(k) or ""
            if not v:
                die("src/i18n.json: %s.%s is empty — the consent banner needs it"
                    % (lang, k))
            picked[k] = v
        subset[lang] = picked
    js = read(CONSENT)
    for token, value in (("__GTM_ID__", GTM_ID),
                         ("__CONSENT_STRINGS__", json_for_script(subset))):
        if token not in js:
            die("src/consent.js has no %s placeholder" % token)
        js = js.replace(token, value)
    return "<script>" + js + "</script>"


def draft_banner(anchor=None):
    """A visible mark on the preview copy, and a way to reach the draft.

    /preview/ is linked from nowhere and carries a noindex, but the URL is
    still reachable by anyone who has it, and a draft that looks published
    is worse than no draft. The draft chapter is unnumbered, which keeps it
    out of the document's own navigation, so the banner is also how a
    reviewer finds it: the document renders after its script runs, and a
    plain #hash would have been resolved before the chapter existed.
    """
    jump = ""
    if anchor:
        jump = (
            '<a href="#%s" style="color:#04081a;font-weight:800;'
            'text-decoration:underline;text-underline-offset:3px">'
            'לפרק הטיוטה ←</a>' % anchor)
    return (
        '<div dir="rtl" style="position:fixed;z-index:9998;inset-inline:0;bottom:0;'
        'background:#fbe94f;color:#04081a;font-family:Heebo,system-ui,sans-serif;'
        'font-size:13px;font-weight:700;line-height:1.5;padding:9px 16px;'
        'text-align:center;box-shadow:0 -4px 18px rgba(0,0,0,.45);'
        'display:flex;gap:14px;align-items:center;justify-content:center;'
        'flex-wrap:wrap">'
        '<span>טיוטה שלא פורסמה · התמלול טרם אומת מול המסמכים · '
        'אינה מקושרת מהאתר</span>' + jump + '</div>'
        '<script>(function(){'
        'function go(){var h=location.hash&&document.querySelector(location.hash);'
        'if(h){h.scrollIntoView();return true}return false}'
        # The chapters exist only once the document has rendered, so a
        # hash in the address bar has nothing to find at load time.
        'var n=0,t=setInterval(function(){if(go()||++n>40)clearInterval(t)},250);'
        'addEventListener("hashchange",go);})();</script>')


def gtm_body():
    if not GTM_ID:
        return ""
    return ('<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=%s"'
            ' height="0" width="0" style="display:none;visibility:hidden"></iframe>'
            "</noscript>" % GTM_ID)


ROBOTS = "index,follow"


def head_meta(title, description, path="/", lang="he", langs=("he",)):
    def esc(s):
        return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")

    locale = OG_LOCALE.get(lang, "he_IL")

    tags = [
        '<meta name="description" content="%s">' % esc(description),
        '<meta name="robots" content="%s">' % ROBOTS,
        '<meta property="og:type" content="article">',
        '<meta property="og:site_name" content="%s">' % esc(SITE_NAME),
        '<meta property="og:locale" content="%s">' % locale,
        '<meta name="robots" content="max-image-preview:large">',
        '<meta property="og:title" content="%s">' % esc(title),
        '<meta property="og:description" content="%s">' % esc(description),
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="%s">' % esc(title),
        '<meta name="twitter:description" content="%s">' % esc(description),
        '<meta name="theme-color" content="#04081a">',
        '<link rel="icon" href="data:image/svg+xml,'
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
        "%3Crect width='32' height='32' fill='%2304081a'/%3E"
        "%3Crect x='6' y='14' width='20' height='4' fill='%23c8102e'/%3E%3C/svg%3E\">",
    ]
    if SITE_URL:
        here = esc(SITE_URL + path)
        tags.append('<link rel="canonical" href="%s">' % here)
        tags.append('<meta property="og:url" content="%s">' % here)
        # Every translation points at every other, and at itself. Without
        # this a search engine treats them as duplicates and keeps one.
        doc = path.endswith("/doc/")
        for other in langs:
            tags.append('<link rel="alternate" hreflang="%s" href="%s">'
                        % (other, esc(SITE_URL + lang_path(other, doc))))
        tags.append('<link rel="alternate" hreflang="x-default" href="%s">'
                    % esc(SITE_URL + lang_path("he", doc)))
        for t in ('<meta property="og:image" content="%s/og.png">',
                  '<meta name="twitter:image" content="%s/og.png">'):
            tags.append(t % esc(SITE_URL))
    tags.append(json_ld("site" if path == "/" else "article",
                        title, description, path))
    tags.append(gtm_head())
    return "\n".join(t for t in tags if t)


# ── The entry page ──────────────────────────────────────────────────────────
# It reuses the document's own faces rather than pulling Heebo from Google:
# the fonts are already in the bundle, and a third-party request is exactly
# what the rest of this build exists to remove.
#
# Only what the landing sets: Heebo covers the Hebrew and the Latin
# languages, IBM Plex Mono the small caps-lock labels. Heebo has no Arabic,
# so the Arabic reader falls to a system face (see landing.html).
LANDING_FACES = [
    ("Heebo", "hebrew"),
    ("Heebo", "latin"),
    ("IBM Plex Mono", "latin"),
]

FACE_RE = re.compile(r"@font-face \{\\n(.*?)\\n\}")


def subset_of(unicode_range):
    if "U+0590-05FF" in unicode_range:
        return "hebrew"
    if unicode_range.startswith("U+0000-00FF") or unicode_range.startswith("U+0100-02BA"):
        return "latin"
    return "other"


def landing_fonts(shell_text, manifest):
    """Pull the woff2 files the entry page needs out of the bundle.

    Returns (css, {dist-relative path: bytes}). The faces are declared over a
    weight *range*: these are variable fonts, which is why one file serves
    every weight of a family in the export's own CSS.
    """
    want = set(LANDING_FACES)
    seen, files, css = {}, {}, []
    for block in FACE_RE.findall(shell_text):
        fam = re.search(r"font-family: '([^']+)'", block)
        url = re.search(r'url\(\\"([0-9a-f-]+)\\"', block)
        rng = re.search(r"unicode-range: (.*?);", block)
        if not (fam and url and rng):
            continue
        key = (fam.group(1), subset_of(rng.group(1)))
        if key not in want or key in seen:
            continue
        entry = manifest.get(url.group(1))
        if not entry:
            die("font %s (%s) is not in the bundle manifest" % key)
        raw = base64.b64decode(entry["data"])
        if entry.get("compressed"):
            raw = gzip.decompress(raw)
        name = "%s-%s.woff2" % (key[0].lower().replace(" ", "-"), key[1])
        files["fonts/" + name] = raw
        seen[key] = True
        css.append(
            "@font-face {\n"
            "  font-family: '%s';\n  font-style: normal;\n"
            "  font-weight: 100 900;\n  font-display: swap;\n"
            "  src: url('fonts/%s') format('woff2');\n"
            "  unicode-range: %s;\n}" % (key[0], name, rng.group(1)))
    missing = want - set(seen)
    if missing:
        die("could not find %s in the bundle's @font-face blocks"
            % ", ".join("%s/%s" % m for m in sorted(missing)))
    return "\n".join(css), files


def load_manifest(shell_text):
    m = re.search(r'<script type="__bundler/manifest">(.*?)</script>', shell_text, re.S)
    if not m:
        die("vendor/export-shell.html has no __bundler/manifest")
    return json.loads(m.group(1))


def build_landing(shell_text, manifest, lang, langs):
    """Render src/landing.html with the copy, the languages and the fonts."""
    if not os.path.exists(LANDING):
        die("src/landing.html is missing — there would be no entry page")
    strings = json.loads(read(I18N))

    # Only the keys the entry page shows. The document's 411 translated
    # strings have no business being downloaded before anyone has opened it.
    KEEP = ("label", "code", "dir", "issued", "t1", "t2", "subtitle",
            "landTitle", "landLead", "landNowLabel", "landEnter",
            "landSoonLabel", "landSoon", "landSoonNote", "landFoot",
            "landNewsLabel", "landNewsPull", "landNewsQuote", "landNewsAttr",
            "landNewsNote", "landBennettLabel", "landBennettPull",
            "landBennettAttr", "landBennettLink")
    # `code` is the picker's own label for a language. Falling it back to
    # Hebrew would put עב on all six buttons, so it is the one key a
    # language may leave unset — the picker then uses the key in capitals.
    NO_FALLBACK = ("code",)
    subset, order = {}, []
    # `each`, not `lang`: the parameter of this function is the language the
    # page is being built for, and a loop over every language would shadow it.
    for each in strings:
        src = strings[each]
        base = strings.get("he", {})
        picked = {}
        for k in KEEP:
            v = src.get(k) or ("" if k in NO_FALLBACK else base.get(k)) or ""
            if not v and each == "he" and k not in NO_FALLBACK:
                die("src/i18n.json: he.%s is empty — the entry page needs it" % k)
            picked[k] = v
        subset[each] = picked
        order.append(each)
    # Hebrew first, then the rest in file order: the picker reads left to
    # right whatever the page direction is.
    order = ["he"] + [k for k in order if k != "he"]

    css, files = landing_fonts(shell_text, manifest)
    he = subset["he"]
    t = subset.get(lang) or he
    page = read(LANDING)
    # The document shell is written for Hebrew; every other language flips it.
    page = page.replace('<html lang="he" dir="rtl">',
                        '<html lang="%s" dir="%s">' % (lang, t.get("dir") or "rtl"), 1)
    for token, value in (
        ("__FONT_CSS__", css),
        ("__STRINGS__", json_for_script(subset)),
        ("__LANGS__", json_for_script(order)),
        ("__TITLE__", t["landTitle"]),
        ("__META__", head_meta(t["landTitle"], t["landLead"],
                               lang_path(lang), lang, langs)),
        ("__LANG__", lang),
        ("__GTM_BODY__", gtm_body()),
    ):
        n = page.count(token)
        if n != 1:
            die("src/landing.html has %d %s placeholders, expected exactly one"
                % (n, token))
        page = page.replace(token, value)
    return prerender(page, t), files


def build_template(shell_template, design_body, design_script, design_css,
                   transcript, portraits, lang="he", langs=("he",), path=None):
    """Graft the current design onto the export shell, then inline the data.

    The shell contributes its <helmet> — which is where the export resolved
    Google Fonts into fifteen bundled woff2 faces — and its loader. The design
    contributes everything a contributor actually edits.
    """
    head, rest = shell_template.split("</helmet>", 1)
    tail = rest.split("</x-dc>", 1)[1]
    # Drop the shell's own component script; the src one replaces it.
    tail = re.sub(r'<script type="text/x-dc".*?</script>', "", tail, flags=re.S)

    design_body = IMAGE_SLOT_OLD_RE.sub(
        lambda m: IMAGE_SLOT_NEW, design_body, count=1)
    if "<image-slot" in design_body:
        die("an <image-slot> survived the swap — it would 404 on a live URL")

    for old, new, label in (
        (FETCH_TRANSCRIPT_OLD, FETCH_TRANSCRIPT_NEW, "transcript fetch"),
        (FETCH_SIDECAR_OLD, FETCH_SIDECAR_NEW, "portrait sidecar fetch"),
    ):
        if design_script.count(old) != 1:
            die("could not find the %s to patch (%d matches). The design's "
                "code changed — update build.py's anchors."
                % (label, design_script.count(old)))
        design_script = design_script.replace(old, new)

    if not os.path.exists(I18N):
        die("src/i18n.json is missing — the page would have no interface copy")
    try:
        strings = json.loads(read(I18N))
    except Exception as e:
        die("src/i18n.json is not valid JSON: %s" % e)
    if "he" not in strings:
        die("src/i18n.json has no 'he' entry to fall back to")

    payload = (
        '<script type="application/json" id="dc-transcript">%s</script>\n'
        '<script type="application/json" id="dc-portraits">%s</script>\n'
        '<script type="application/json" id="dc-i18n">%s</script>\n'
        '<script>window.__lang=%s;</script>\n'
        % (json_for_script(transcript or {}), json_for_script(portraits),
           json_for_script(strings), json.dumps(lang))
    )
    t = strings.get(lang) or strings["he"]
    title = ("%s %s: %s" % (t.get("t1", ""), t.get("t2", ""),
                            t.get("subtitle", ""))).strip()
    description = t.get("heroLead") or DESCRIPTION
    head, n = re.subn(r"<title>.*?</title>",
                      lambda m: "<title>" + title + "</title>", head, count=1, flags=re.S)
    if not n:
        die("no <title> in the shell helmet to set")
    out = (head + head_meta(title, description, path or lang_path(lang, True),
                            lang, langs)
           + "\n" + design_css + "\n</helmet>"
           + gtm_body() + design_body + "</x-dc>"
           + payload + design_script + tail)

    # The editor's Google Fonts preconnects are dead weight once the woff2
    # files ride in the bundle: two third-party handshakes off the critical
    # path, and two fewer requests that can leak a reader's IP.
    out = re.sub(
        r'<link rel="preconnect" href="https://fonts\.(googleapis|gstatic)\.com"[^>]*>\n?',
        "", out)
    if "fonts.googleapis.com/css2" in out:
        die("a Google Fonts stylesheet link survived — fonts must come from the bundle")
    return out


def main():
    check_only = "--check" in sys.argv
    if not os.path.exists(SHELL):
        die("vendor/export-shell.html is missing")

    design_path = find_design()
    body, script, css = slice_design(read(design_path))
    lines, idx, shell_template = load_shell()

    transcript, t_note = load_transcript()
    published, preview = split_drafts(transcript)
    drafts = [c for c in (transcript or {}).get("chapters") or [] if c.get("draft")]
    transcript = published
    portraits, copies, p_note = load_portraits(preview)

    n_items = sum(len(c.get("items") or []) for c in (transcript or {}).get("chapters") or [])
    print("build report")
    print("  design     : %s" % os.path.basename(design_path))
    print("  transcript : " + (
        "%d chapters, %d items, %d speakers"
        % (len(transcript["chapters"]), n_items, len(transcript.get("speakers") or {}))
        if transcript and transcript.get("chapters") else "MISSING"))
    langs = sorted(json.loads(read(I18N)).keys()) if os.path.exists(I18N) else []
    print("  languages  : %s" % (", ".join(langs) or "MISSING"))
    print("  portraits  : %d %s" % (
        len(portraits), "inlined" if INLINE_PORTRAITS else "as sibling files"))
    if t_note:
        print("  ! " + t_note)
    if p_note:
        print("  ! " + p_note)
    print("  site url   : " + (SITE_URL or "(unset — no canonical/og:url)"))
    if drafts:
        print("  drafts     : %s — /%s/ only, noindex, not in the sitemap"
              % (", ".join(c["id"] for c in drafts), PREVIEW))
    print("  analytics  : " + (GTM_ID or "none (no third-party request)"))

    # Render the entry page even in --check: it is the first thing a visitor
    # sees, so a broken placeholder or a missing string should fail the
    # workflow, not the deploy.
    shell_text = read(SHELL)
    manifest = load_manifest(shell_text)
    # Hebrew first: it holds the bare paths, and the others are checked
    # against it.
    order = json.loads(read(I18N)).keys()
    LANGS = ["he"] + [k for k in order if k != "he"]
    pages = {}
    for lang in LANGS:
        pages[lang], fonts = build_landing(shell_text, manifest, lang, LANGS)
    print("  entry page : %d languages, %d fonts" % (len(LANGS), len(fonts)))
    print("  urls       : / and /doc/ for he, /<lang>/ and /<lang>/doc/ for the rest")

    if check_only:
        print("\n--check: nothing written")
        return 0 if transcript and not t_note else 1

    # Rebuild the generated trees from scratch so a renamed or deleted
    # speaker cannot leave an orphan behind for the next deploy to publish.
    shutil.rmtree(os.path.join(DOC, "portraits"), ignore_errors=True)
    shutil.rmtree(os.path.join(DIST, "fonts"), ignore_errors=True)
    shutil.rmtree(os.path.join(DIST, "assets"), ignore_errors=True)
    shutil.rmtree(os.path.join(DIST, PREVIEW), ignore_errors=True)
    # Before the entry page existed the document was the root and its photos
    # sat in dist/portraits/. Left behind, they are 4 MB of dead weight in
    # every deploy from a working tree that predates the split.
    shutil.rmtree(os.path.join(DIST, "portraits"), ignore_errors=True)
    for lang in LANGS:
        if lang != "he":
            shutil.rmtree(os.path.join(DIST, lang), ignore_errors=True)
    os.makedirs(DIST, exist_ok=True)
    # Every language, both pages. The bare paths stay Hebrew so no link that
    # has already been shared stops working.
    strings = json.loads(read(I18N))
    titles = {}
    for lang in LANGS:
        t = strings.get(lang) or strings["he"]
        titles[lang] = ("%s %s: %s" % (t.get("t1", ""), t.get("t2", ""),
                                       t.get("subtitle", ""))).strip()
        template = build_template(shell_template, body, script, css,
                                  transcript, portraits, lang, LANGS)
        # The template rides inside a <script type="__bundler/template"> tag,
        # so every "</" has to be escaped or the HTML parser closes that tag
        # early and truncates the payload. The export does the same.
        page = list(lines)
        page[idx] = template_line(template)
        # The loader page carries its own <title>: it is what the tab shows
        # while the bundle unpacks, and what a crawler that runs no script
        # reads.
        for i, line in enumerate(page[:idx]):
            if "<title>" in line:
                page[i] = re.sub(r"<title>.*?</title>",
                                 lambda m: "<title>" + titles[lang] + "</title>",
                                 line, count=1)
                break
        else:
            die("no <title> in the loader page to set")

        docdir = os.path.join(DIST, *( ["doc"] if lang == "he" else [lang, "doc"] ))
        os.makedirs(docdir, exist_ok=True)
        write_text(os.path.join(docdir, "index.html"), "\n".join(page))

        landdir = DIST if lang == "he" else os.path.join(DIST, lang)
        os.makedirs(landdir, exist_ok=True)
        write_text(os.path.join(landdir, "index.html"), pages[lang])
    if drafts:
        global ROBOTS
        ROBOTS = "noindex,nofollow"
        template = build_template(shell_template, body, script, css,
                                  preview, portraits, "he", LANGS,
                                  path="/%s/doc/" % PREVIEW)
        page = list(lines)
        page[idx] = template_line(template)
        title = titles["he"] + " — טיוטה"
        for i, line in enumerate(page[:idx]):
            if "<title>" in line:
                page[i] = re.sub(r"<title>.*?</title>",
                                 lambda m: "<title>" + title + "</title>", line, count=1)
                break
        page[idx] = template_line(template.replace(
            "</x-dc>", "</x-dc>" + draft_banner(drafts[0]["id"] and
                                                "ch-" + drafts[0]["id"]), 1))
        pdir = os.path.join(DIST, PREVIEW, "doc")
        os.makedirs(pdir, exist_ok=True)
        write_text(os.path.join(pdir, "index.html"), "\n".join(page))
        ROBOTS = "index,follow"

    out = os.path.join(DOC, "index.html")
    if os.path.exists(OG_IMAGE):
        shutil.copyfile(OG_IMAGE, os.path.join(DIST, "og.png"))
    elif SITE_URL:
        print("  ! no src/assets/og.png — shared links will preview bare")
    if os.path.isdir(LANDING_ASSETS):
        shutil.copytree(LANDING_ASSETS, os.path.join(DIST, "assets"))
    os.makedirs(os.path.join(DIST, "assets"), exist_ok=True)
    for key in LANDING_PORTRAITS:
        srcfile = os.path.join(PORTRAIT_DIR, key + ".png")
        if not os.path.exists(srcfile):
            die("the entry page shows %s.png, which is not in assets/portraits/" % key)
        shutil.copyfile(srcfile, os.path.join(DIST, "assets", key + ".png"))
    for rel, blob in sorted(fonts.items()):
        dest = os.path.join(DIST, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(blob)

    if SITE_URL:
        write_text(os.path.join(DIST, "robots.txt"),
                   "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % SITE_URL)
        today = datetime.date.today().isoformat()
        rows = []
        for doc, freq, pri in ((False, "weekly", "1.0"), (True, "monthly", "0.9")):
            for lang in LANGS:
                alts = "".join(
                    '<xhtml:link rel="alternate" hreflang="%s" href="%s%s"/>'
                    % (o, SITE_URL, lang_path(o, doc)) for o in LANGS)
                alts += ('<xhtml:link rel="alternate" hreflang="x-default" '
                         'href="%s%s"/>' % (SITE_URL, lang_path("he", doc)))
                rows.append("  <url><loc>%s%s</loc><lastmod>%s</lastmod>"
                            "<changefreq>%s</changefreq><priority>%s</priority>%s"
                            "</url>\n"
                            % (SITE_URL, lang_path(lang, doc), today, freq, pri, alts))
        write_text(os.path.join(DIST, "sitemap.xml"),
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
                   '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
                   + "".join(rows) + "</urlset>\n")
        print("  crawling   : robots.txt + sitemap.xml (%d urls)" % len(rows))

    # GitHub Pages reads the custom domain from a CNAME file at the root of
    # the published artifact. Without it in dist/, every deploy drops the
    # domain back to the github.io address.
    cname = os.path.join(SRC, "CNAME")
    if os.path.exists(cname):
        shutil.copyfile(cname, os.path.join(DIST, "CNAME"))
        print("  domain     : %s" % read(cname).strip())

    total = os.path.getsize(out)
    for rel, srcfile in sorted(copies.items()):
        dest = os.path.join(DOC, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(srcfile, dest)
        total += os.path.getsize(dest)

    print("\nwrote %d entry pages (%.0f KB each, %d shared fonts)"
          % (len(LANGS),
             len(pages["he"].encode("utf-8")) / 1024.0, len(fonts)))
    print("wrote %d documents, one per language" % len(LANGS))
    print("largest: dist/doc/index.html (%.1f MB)%s"
          % (os.path.getsize(out) / 1048576.0,
             "" if not copies else " + %d portraits, %.1f MB total"
             % (len(copies), total / 1048576.0)))
    if not transcript:
        print("NOTE: no transcript — the page will show hero, prologue and footer only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
