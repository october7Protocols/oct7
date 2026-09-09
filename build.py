#!/usr/bin/env python3
"""
Build the publishable page from the editable source in src/.

    src/מענה ראש הממשלה v2.dc.html   the design, this is what you edit
    src/transcript.json               the document data      (see SCHEMA.md)
    src/assets/portraits/<key>.png    one per speaker key
    src/assets/halevi-aman.png        the AMAN-era portrait

    src/landing.html                  the entry page, this too

    -> dist/index.html                the entry page, light, loads instantly
    -> dist/doc/index.html            the document, one self-contained file

Why a build step
----------------
Opened directly, the design fetches five things that do not exist on a live
URL: ./transcript.json (all 11 chapters, the entire body), the editor's
./.image-slots.state.json portrait sidecar, ./halevi-aman.png, React from
unpkg, and Heebo + IBM Plex Mono from Google Fonts. This build inlines every
one of them, so the output has zero runtime network dependencies and works
from file:// as well as from a web server.

The runtime plumbing (the DC runtime, React, and the fifteen subsetted woff2
faces) is carried in vendor/export-shell.html, a Claude Design export whose
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
import struct
import sys
import zlib

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
# bennett is on the entry page; bar is on the 404.
LANDING_PORTRAITS = ("bennett", "bar")
CONSENT = os.path.join(SRC, "consent.js")
# The contact page. Hebrew and English only: the document is published in
# seven languages, but the person who reads the mail is not.
CONTACT = os.path.join(SRC, "contact.html")
CONTACT_LANGS = ("he", "en")
# The address the page hands out. It is never written into the HTML, see
# mail_parts(), so this is the only place it lives.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "contact@october7.co")
# The about page. Same two languages, same reason.
ABOUT = os.path.join(SRC, "about.html")

# IndexNow. Bing, Yandex, Seznam and Naver take a push instead of waiting to
# crawl; the protocol's only requirement is that this key is also readable at
# https://october7.co/<key>.txt, which the build writes. Google does not
# participate, its side is Search Console, which needs an account.
INDEXNOW_KEY = "e2279691b68b63fd8e8e9f2c4b8e9d14"
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
# empty value ships no third-party script at all, the page keeps its
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
# the bare paths, october7.co is already shared and must not break, and the
# rest sit under a prefix.
#
#     /            /doc/            he
#     /en/         /en/doc/         en   … and so on
#
# The pages still switch language in place; the picker's links are what a
# crawler follows and what a reader copies out of the address bar.
def lang_path(lang, doc=False, sub=None):
    base = "/" if lang == "he" else "/%s/" % lang
    if sub:
        return base + sub + "/"
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
        die("no <style data-om-responsive> block in the src design, the "
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
    } catch (e) { /* no sidecar yet, placeholders stay */ }
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
    first one and truncates the payload, which shows up as a page that
    renders 186 bytes and nothing else. This has been got wrong twice by
    hand; it lives in one place now.
    """
    return json.dumps(html, ensure_ascii=False).replace("</", "<\\u002F")


def write_text(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def write_bytes(path, blob):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(blob)


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
        return None, "missing src/transcript.json, the page would have no chapters"
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
            notes.append("src/.image-slots.state.json is absent, that hidden "
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
    "k-nightlabel": lambda t: t["landNightLabel"],
    "k-nighttitle": lambda t: t["landNightTitle"],
    "k-nightsub":   lambda t: t["landNightSub"],
    "k-nightgo":    lambda t: t["landNightGo"],
    "k-newslabel":  lambda t: t["landNewsLabel"],
    "k-newspull":   lambda t: t["landNewsPull"],
    "k-newsquote":  lambda t: t["landNewsQuote"],
    "k-newsattr":   lambda t: t["landNewsAttr"],
    "k-newsnote":   lambda t: t["landNewsNote"],
    "k-benlabel":   lambda t: t["landBennettLabel"],
    "k-benpull":    lambda t: t["landBennettPull"],
    "k-benattr":    lambda t: t["landBennettAttr"],
    "k-uaelabel":   lambda t: t["landUaeLabel"],
    "k-uaepull":    lambda t: t["landUaePull"],
    "k-uaequote":   lambda t: t["landUaeQuote"],
    "k-uaeattr":    lambda t: t["landUaeAttr"],
    "k-soonlabel":  lambda t: t["landSoonLabel"],
    "k-soon":       lambda t: t["landSoon"],
    "k-soonnote":   lambda t: t["landSoonNote"],
    "k-foot":       lambda t: t["landFoot"],
    "k-landabout":  lambda t: t["landAbout"],
    "k-landcontact": lambda t: t["landContact"],
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
    # An unclosed tag here does not look broken, it silently swallows
    # everything after it into the link, which is how the "coming soon" block
    # ended up opening a radio programme.
    if page.count("</span>") != page.count("<span"):
        die("prerender left an unbalanced <span> in the entry page")
    return page


def json_ld(kind, title, description, path, lang="he"):
    """Structured data. Without it a search engine has to infer what the page
    is from prose; with it, the document is declared as an article with a
    date, a language and a publisher."""
    if not SITE_URL:
        return ""
    site = {"@type": "WebSite", "name": SITE_NAME, "url": SITE_URL + "/",
            "inLanguage": "he"}
    if kind == "site":
        data = dict(site, description=description, inLanguage=lang)
    else:
        # inLanguage has to be this page's language, not the site's: the
        # translated documents were all declaring themselves Hebrew.
        data = {"@type": "Article", "headline": title, "description": description,
                "url": SITE_URL + path,
                "inLanguage": lang, "datePublished": PUBLISHED,
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
        die("src/consent.js is missing, GTM_ID is set but nothing would gate it")
    keys = ("consentText", "consentYes", "consentNo")
    strings = json.loads(read(I18N))
    subset = {}
    for lang, t in strings.items():
        picked = {}
        for k in keys:
            v = t.get(k) or strings.get("he", {}).get(k) or ""
            if not v:
                die("src/i18n.json: %s.%s is empty, the consent banner needs it"
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


def head_meta(title, description, path="/", lang="he", langs=("he",), gtm=True):
    """Everything a crawler reads before it reads the page.

    `gtm` is off for the document's loader page: the loader's whole
    <html> is replaced by the unpacked template, which carries its own
    container snippet, and two of them would load the container twice.
    """
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
        # The SVG stays first for a browser that prefers it; the files
        # behind it are what iOS, Android, Windows and a bookmark bar ask
        # for, and /favicon.ico is requested by name whatever this says.
        '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,'
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
        "%3Crect width='32' height='32' fill='%2304081a'/%3E"
        "%3Crect x='6' y='14' width='20' height='4' fill='%23c8102e'/%3E%3C/svg%3E\">",
        '<link rel="icon" type="image/png" sizes="32x32" href="/icon-32.png">',
        '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">',
        '<link rel="manifest" href="/site.webmanifest">',
    ]
    if SITE_URL:
        here = esc(SITE_URL + path)
        tags.append('<link rel="canonical" href="%s">' % here)
        tags.append('<meta property="og:url" content="%s">' % here)
        # Every translation points at every other, and at itself. Without
        # this a search engine treats them as duplicates and keeps one.
        doc = path.endswith("/doc/")
        sub = next((p for p in ("contact", "about")
                    if path.endswith("/%s/" % p)), None)
        for other in langs:
            tags.append('<link rel="alternate" hreflang="%s" href="%s">'
                        % (other, esc(SITE_URL + lang_path(other, doc, sub))))
        tags.append('<link rel="alternate" hreflang="x-default" href="%s">'
                    % esc(SITE_URL + lang_path("he", doc, sub)))
        for t in ('<meta property="og:image" content="%s/og.png">',
                  '<meta name="twitter:image" content="%s/og.png">'):
            tags.append(t % esc(SITE_URL))
    tags.append(json_ld("site" if path == "/" else "article",
                        title, description, path, lang))
    if gtm:
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


def landing_fonts(shell_text, manifest, prefix=""):
    """Pull the woff2 files the entry page needs out of the bundle.

    `prefix` is what gets the page back to the site root: "" for the Hebrew
    page at /, "../" for /en/ and the other language pages, which sit one
    directory down but share the one fonts/ tree.

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
            "  src: url('%sfonts/%s') format('woff2');\n"
            "  unicode-range: %s;\n}" % (key[0], prefix, name, rng.group(1)))
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
        die("src/landing.html is missing, there would be no entry page")
    strings = json.loads(read(I18N))

    # Only the keys the entry page shows. The document's 411 translated
    # strings have no business being downloaded before anyone has opened it.
    KEEP = ("label", "code", "dir", "issued", "t1", "t2", "subtitle",
            "landTitle", "landLead", "landNowLabel", "landEnter",
            "landSoonLabel", "landSoon", "landSoonNote", "landFoot",
            "landNightLabel", "landNightTitle", "landNightSub", "landNightGo",
            "landNewsLabel", "landNewsPull", "landNewsQuote", "landNewsAttr",
            "landNewsNote", "landBennettLabel", "landBennettPull",
            "landBennettAttr", "landBennettLink",
            "landUaeLabel", "landUaePull", "landUaeQuote", "landUaeAttr",
            "landContact", "landAbout")
    # `code` is the picker's own label for a language. Falling it back to
    # Hebrew would put עב on all six buttons, so it is the one key a
    # language may leave unset, the picker then uses the key in capitals.
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
                die("src/i18n.json: he.%s is empty, the entry page needs it" % k)
            picked[k] = v
        subset[each] = picked
        order.append(each)
    # Hebrew first, then the rest in file order: the picker reads left to
    # right whatever the page direction is.
    order = ["he"] + [k for k in order if k != "he"]

    # Every language but Hebrew lives one directory down (/en/, /fr/ ...)
    # while assets/ and fonts/ exist only at the root. Left relative, each
    # translated page asks for /en/assets/... and /en/fonts/..., which do not
    # exist: broken portraits and fallback fonts on six of the seven pages.
    prefix = "" if lang == "he" else "../"
    # The contact page exists in Hebrew and English only. Left as authored,
    # the footer link resolves to /ru/contact/ and every language but those
    # two serves a 404 to anyone who does not run the script that fixes it.
    def side_href(sub):
        return (sub + "/" if lang == "he"
                else "../%s/" % sub if lang == "en"
                else "../en/%s/" % sub)
    css, files = landing_fonts(shell_text, manifest, prefix)
    he = subset["he"]
    t = subset.get(lang) or he
    page = read(LANDING)
    page, n_img = re.subn(r'(<img\b[^>]*\bsrc=")assets/', r'\1%sassets/' % prefix, page)
    for sub in ("contact", "about"):
        page, n_c = re.subn(r'(\bdata-%s\b[^>]*\bhref=")%s/' % (sub, sub),
                            lambda m, s=sub: m.group(1) + side_href(s), page)
        if n_c != 1:
            die("src/landing.html: expected one data-%s href to re-root, found %d"
                % (sub, n_c))
    if n_img == 0:
        die("src/landing.html has no <img src=\"assets/...\"> to re-root")
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


# The contact page's own strings. Four of them open on a bold lead-in, so
# they go through rich() rather than plain escaping; the page's script does
# exactly the same thing when the reader switches language.
CONTACT_PRERENDER = {
    "k-ctitle":       ("contactTitle", False),
    "k-clead":        ("contactLead", False),
    "k-cmaillabel":   ("contactMailLabel", False),
    "k-cshow":        ("contactShow", False),
    "k-cnojs":        ("contactNoJs", False),
    "k-cbeforelabel": ("contactBeforeLabel", False),
    "k-cb1":          ("contactB1", True),
    "k-cb2":          ("contactB2", True),
    "k-cb3":          ("contactB3", True),
    "k-ccorr":        ("contactCorr", True),
    "k-cback":        ("contactBack", False),
    "k-cfoot":        ("contactFoot", False),
}

CONTACT_KEEP = ("label", "code", "dir") + tuple(
    k for _, (k, _) in sorted(CONTACT_PRERENDER.items())) + ("contactCopy", "contactCopied")


def mail_parts(address):
    """The address, split at the @ and base64'd.

    Nothing in the served HTML then matches an address pattern: a harvester
    that greps the source, or runs a regex over the rendered text, finds no
    address to add to a list. The page joins the halves inside the click
    handler, so even a bot that runs script has to click first.
    """
    if address.count("@") != 1:
        die("CONTACT_EMAIL is not an address: %r" % address)
    local, host = address.split("@")
    return [base64.b64encode(part.encode("utf-8")).decode("ascii")
            for part in (local, host)]


def prerender_side(page, t, table, label):
    """Fill a side page's elements server-side, as the entry page is filled."""
    def esc(v):
        return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def rich(v):
        return esc(v).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")

    for el, (key, is_rich) in table.items():
        value = t.get(key) or ""
        pat = re.compile(r'(id="%s"[^>]*>)(</)' % re.escape(el))
        page, n = pat.subn(
            lambda m: m.group(1) + (rich(value) if is_rich else esc(value)) + m.group(2),
            page, count=1)
        if not n:
            die("%s: nothing to prerender into #%s" % (label, el))
    if page.count("<b>") != page.count("</b>"):
        die("prerender left an unbalanced <b> in %s" % label)
    return page


# ── the icons ──────────────────────────────────────────────────────────────
# The mark is the one the pages already carry as an inline SVG: the site's
# ground with the red rule across it. Written here as real files because a
# data: URI favicon is not what a browser looks for when it wants a tab icon,
# and is not what iOS, Android or a bookmark bar ask for at all.
ICON_BG = (0x04, 0x08, 0x1a)
ICON_FG = (0xc8, 0x10, 0x2e)


def png_bytes(size, bg, fg, bar=(0.1875, 0.4375, 0.625, 0.125)):
    """A flat two-colour PNG, written without an imaging library.

    `bar` is (x, y, w, h) as fractions of the side, so the mark keeps its
    proportions at every size. Rows are filter-type 0 (none), the image is
    small and flat enough that the compressor does the work.
    """
    x0 = int(round(bar[0] * size))
    y0 = int(round(bar[1] * size))
    x1 = x0 + max(1, int(round(bar[2] * size)))
    y1 = y0 + max(1, int(round(bar[3] * size)))
    row_bg = bytes(bg) * size
    row_fg = (bytes(bg) * x0) + (bytes(fg) * (x1 - x0)) + (bytes(bg) * (size - x1))
    raw = b"".join(b"\x00" + (row_fg if y0 <= y < y1 else row_bg)
                   for y in range(size))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico_bytes(pngs):
    """An .ico container holding PNG images, every browser since IE11.

    Windows and the bookmark bar still ask for /favicon.ico by name, whatever
    <link rel="icon"> says.
    """
    n = len(pngs)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries, blobs = b"", b""
    for size, data in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                               1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def write_icons():
    """favicon.ico, the touch icon, the maskable icon and the manifest."""
    sizes = {n: png_bytes(n, ICON_BG, ICON_FG) for n in (16, 32, 48, 180, 512)}
    write_bytes(os.path.join(DIST, "favicon.ico"),
                ico_bytes([(n, sizes[n]) for n in (16, 32, 48)]))
    write_bytes(os.path.join(DIST, "apple-touch-icon.png"), sizes[180])
    write_bytes(os.path.join(DIST, "icon-512.png"), sizes[512])
    write_bytes(os.path.join(DIST, "icon-32.png"), sizes[32])
    write_text(os.path.join(DIST, "site.webmanifest"), json.dumps({
        "name": SITE_NAME,
        "short_name": "october7",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#04081a",
        "theme_color": "#04081a",
        "icons": [
            {"src": "/icon-32.png", "sizes": "32x32", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }, ensure_ascii=False, indent=1))
    return ["favicon.ico", "apple-touch-icon.png", "icon-512.png",
            "icon-32.png", "site.webmanifest"]


def build_404(css):
    """GitHub Pages\' own 404 is a grey page that says "GitHub Pages".

    This one has a joke in it, and the joke is carried by a real quotation:
    the man whose photograph is on the page wrote, in a sworn affidavit this
    site publishes, that his own alert tier had been mistaken. The line above
    the photo is the page speaking about him, in the third person and without
    quotation marks, not words put in his mouth. Nothing on this site,
    including its error page, attributes a sentence to a person who did not
    say it.
    """
    strings = json.loads(read(I18N))
    he, en = strings["he"], strings.get("en") or strings["he"]

    def esc(v):
        return (v or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return (
        '<!DOCTYPE html>\n<html lang="he" dir="rtl">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex">\n'
        '<title>404, %s</title>\n<style>\n%s\n'
        '*{box-sizing:border-box}html{background:#04081a}'
        'body{margin:0;min-height:100svh;background:#04081a;color:#fff;'
        'font-family:Heebo,system-ui,sans-serif;-webkit-font-smoothing:antialiased;'
        'display:flex;align-items:center;justify-content:center;'
        'padding:clamp(24px,6vw,56px)}'
        '.w{display:grid;grid-template-columns:minmax(0,220px) minmax(0,1fr);'
        'align-items:end;gap:clamp(18px,4vw,40px);max-width:820px;width:100%%}'
        # The portrait is not a clean cut-out, it carries its own dark
        # backdrop, which reads as a grey box dropped on the page unless it
        # is framed. The document gives every speaker the same frame.
        '.p{margin:0;align-self:end;border:1px solid rgba(255,255,255,0.16);'
        'background:rgba(255,255,255,0.03);overflow:hidden;line-height:0}'
        '.p img{display:block;width:100%%;height:auto;filter:grayscale(1) contrast(1.06)}'
        '.k{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11px;'
        'font-weight:500;letter-spacing:.22em;color:#ff5a6e}'
        'h1{margin:14px 0 0;font-size:clamp(26px,5.6vw,44px);font-weight:900;'
        'line-height:1.1;letter-spacing:-0.03em;text-wrap:balance}'
        'blockquote{margin:22px 0 0;padding-inline-start:14px;'
        'border-inline-start:3px solid #c8102e;font-weight:300;color:#c3cbe6;'
        'font-size:clamp(14px,3.4vw,17px);line-height:1.6}'
        'blockquote::before{content:"\\201D"}blockquote::after{content:"\\201C"}'
        '.a{margin:9px 0 0;font-size:clamp(11.5px,2.9vw,13px);color:#7a86b4;'
        'padding-inline-start:17px}'
        'p.l{margin:24px 0 0;font-weight:300;color:#c3cbe6;line-height:1.65;'
        'font-size:clamp(13.5px,3.4vw,16px)}'
        '.e{margin:10px 0 0;color:#5d6890;font-size:clamp(12px,3vw,14px);'
        'font-weight:300;line-height:1.6}'
        'a.g{display:inline-block;margin-top:24px;background:#c8102e;color:#fff;'
        'text-decoration:none;padding:14px 26px;font-weight:700;'
        'font-size:clamp(14px,3.8vw,16px)}'
        'a.g:hover{background:#ff5a6e}'
        '@media(max-width:620px){.w{grid-template-columns:1fr;align-items:start}'
        '.p{max-width:180px}}'
        '</style>\n</head>\n<body>\n<div class="w">\n'
        '<figure class="p"><img src="/assets/bar.png" alt="" '
        'width="760" height="744" decoding="async"></figure>\n'
        '<div>\n<div class="k">404 &middot; october7.co</div>\n'
        '<h1>%s</h1>\n'
        '<blockquote>%s</blockquote>\n<div class="a">%s</div>\n'
        '<p class="l">%s</p>\n'
        '<p class="e" lang="en" dir="ltr">%s</p>\n'
        '<a class="g" href="/">%s</a>\n</div>\n</div>\n</body>\n</html>\n'
        % (esc(he["landTitle"]), css,
           esc(he["nfTitle"]), esc(he["nfQuote"]), esc(he["nfAttr"]),
           esc(he["nfLead"]), esc(en["nfLead"]), esc(he["nfGo"])))


ABOUT_PRERENDER = {
    "k-atitle":      ("aboutTitle", False),
    "k-alead":       ("aboutLead", False),
    "k-awhatlabel":  ("aboutWhatLabel", False),
    "k-awhat":       ("aboutWhat", True),
    "k-asrclabel":   ("aboutSrcLabel", False),
    "k-asrc1":       ("aboutSrc1", True),
    "k-asrc2":       ("aboutSrc2", True),
    "k-asrc3":       ("aboutSrc3", True),
    "k-aleaklabel":  ("aboutLeakLabel", False),
    "k-aleak":       ("aboutLeak", True),
    "k-arulelabel":  ("aboutRuleLabel", False),
    "k-arule1":      ("aboutRule1", True),
    "k-arule2":      ("aboutRule2", True),
    "k-arule3":      ("aboutRule3", True),
    "k-anotlabel":   ("aboutNotLabel", False),
    "k-anot":        ("aboutNot", True),
    "k-awholabel":   ("aboutWhoLabel", False),
    "k-awho":        ("aboutWho", True),
    "k-acorrlabel":  ("aboutCorrLabel", False),
    "k-acorr":       ("aboutCorr", True),
    "k-acontact":    ("aboutContactLink", False),
    "k-adates":      ("aboutDates", False),
    "k-aback":       ("aboutBack", False),
    "k-afoot":       ("aboutFoot", False),
}

ABOUT_KEEP = ("label", "code", "dir") + tuple(
    k for _, (k, _) in sorted(ABOUT_PRERENDER.items()))


def build_about(shell_text, manifest, lang, langs):
    """Render src/about.html, who publishes this, and where it came from."""
    updated = datetime.date.today().strftime("%d.%m.%Y")
    return build_side_page(ABOUT, "about", ABOUT_PRERENDER, ABOUT_KEEP,
                           shell_text, manifest, lang, langs,
                           fill=lambda v: v.replace("{d}", updated))


def build_contact(shell_text, manifest, lang, langs):
    """Render src/contact.html. No form, and no address in the markup."""
    return build_side_page(CONTACT, "contact", CONTACT_PRERENDER, CONTACT_KEEP,
                           shell_text, manifest, lang, langs,
                           extra={"__MAIL__": json_for_script(
                               mail_parts(CONTACT_EMAIL))},
                           keep_extra=("contactCopy", "contactCopied"))


def build_side_page(path, sub, table, keep, shell_text, manifest, lang, langs,
                    extra=None, keep_extra=(), fill=None):
    """The pages beside the document: /contact/ and /about/.

    They share everything but their copy, the same chrome, the same
    two-language picker, the same server-side fill so that what leaves the
    build is a finished page rather than a shell of empty elements.
    """
    if not os.path.exists(path):
        die("%s is missing" % os.path.relpath(path, HERE))
    strings = json.loads(read(I18N))
    base = strings.get("he", {})
    subset, order = {}, []
    for each in langs:
        src = strings.get(each) or {}
        picked = {}
        for k in tuple(keep) + tuple(keep_extra):
            v = src.get(k) or ("" if k == "code" else base.get(k)) or ""
            if not v and each == "he" and k != "code":
                die("src/i18n.json: he.%s is empty, /%s/ needs it" % (k, sub))
            picked[k] = fill(v) if (fill and v) else v
        subset[each] = picked
        order.append(each)

    # /contact/ and /en/contact/ sit one and two levels below dist/, and the
    # fonts exist only at the root.
    css, files = landing_fonts(shell_text, manifest,
                               "../" if lang == "he" else "../../")
    t = subset.get(lang) or subset["he"]
    page = read(path)
    page = page.replace('<html lang="he" dir="rtl">',
                        '<html lang="%s" dir="%s">' % (lang, t.get("dir") or "rtl"), 1)
    # The first two entries of the table are the page's own title and lead;
    # they are what the head is built from.
    order_keys = [k for _, (k, _) in sorted(table.items())]
    title_key = next(k for k in order_keys if k.endswith("Title"))
    lead_key = next(k for k in order_keys if k.endswith("Lead"))
    tokens = {
        "__FONT_CSS__": css,
        "__STRINGS__": json_for_script(subset),
        "__LANGS__": json_for_script(order),
        "__TITLE__": t[title_key],
        "__META__": head_meta(t[title_key], t[lead_key],
                              lang_path(lang, sub=sub), lang, langs),
        "__LANG__": lang,
        "__GTM_BODY__": gtm_body(),
    }
    tokens.update(extra or {})
    for token, value in tokens.items():
        n = page.count(token)
        if n != 1:
            die("%s has %d %s placeholders, expected exactly one"
                % (os.path.relpath(path, HERE), n, token))
        page = page.replace(token, value)
    return prerender_side(page, t, table, os.path.relpath(path, HERE)), files


def build_template(shell_template, design_body, design_script, design_css,
                   transcript, portraits, lang="he", langs=("he",), path=None):
    """Graft the current design onto the export shell, then inline the data.

    The shell contributes its <helmet>, which is where the export resolved
    Google Fonts into fifteen bundled woff2 faces, and its loader. The design
    contributes everything a contributor actually edits.
    """
    head, rest = shell_template.split("</helmet>", 1)
    tail = rest.split("</x-dc>", 1)[1]
    # Drop the shell's own component script; the src one replaces it.
    tail = re.sub(r'<script type="text/x-dc".*?</script>', "", tail, flags=re.S)

    design_body = IMAGE_SLOT_OLD_RE.sub(
        lambda m: IMAGE_SLOT_NEW, design_body, count=1)
    if "<image-slot" in design_body:
        die("an <image-slot> survived the swap, it would 404 on a live URL")

    for old, new, label in (
        (FETCH_TRANSCRIPT_OLD, FETCH_TRANSCRIPT_NEW, "transcript fetch"),
        (FETCH_SIDECAR_OLD, FETCH_SIDECAR_NEW, "portrait sidecar fetch"),
    ):
        if design_script.count(old) != 1:
            die("could not find the %s to patch (%d matches). The design's "
                "code changed, update build.py's anchors."
                % (label, design_script.count(old)))
        design_script = design_script.replace(old, new)

    if not os.path.exists(I18N):
        die("src/i18n.json is missing, the page would have no interface copy")
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
        die("a Google Fonts stylesheet link survived, fonts must come from the bundle")
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
    print("  site url   : " + (SITE_URL or "(unset, no canonical/og:url)"))
    if drafts:
        print("  drafts     : %s, /%s/ only, noindex, not in the sitemap"
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
    contact, about = {}, {}
    for lang in CONTACT_LANGS:
        contact[lang], fonts = build_contact(shell_text, manifest, lang, CONTACT_LANGS)
        about[lang], fonts = build_about(shell_text, manifest, lang, CONTACT_LANGS)
    print("  side pages : /contact/ and /about/ in %s" % "/".join(CONTACT_LANGS))
    print("  contact    : %s" % CONTACT_EMAIL)
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
    shutil.rmtree(os.path.join(DIST, "contact"), ignore_errors=True)
    shutil.rmtree(os.path.join(DIST, "about"), ignore_errors=True)
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
        # The loader page is what is actually served. Everything else, the
        # description, canonical, hreflang, og: tags and the JSON-LD, used
        # to live only inside the compressed template, which exists after
        # the script has run. WhatsApp, Telegram, Slack, Facebook and X run
        # no script, so every link ever shared to a document previewed bare;
        # and a crawler saw a title and nothing else until it got round to
        # rendering. The loader's <html> is replaced wholesale once the
        # bundle unpacks, so these are never present twice.
        desc = (strings.get(lang) or strings["he"]).get("heroLead") or DESCRIPTION
        loader_meta = head_meta(titles[lang], desc, lang_path(lang, True),
                                lang, LANGS, gtm=False)
        for i, line in enumerate(page[:idx]):
            if "<title>" in line:
                page[i] = re.sub(r"<title>.*?</title>",
                                 lambda m: "<title>" + titles[lang] + "</title>",
                                 line, count=1) + "\n" + loader_meta
                break
        else:
            die("no <title> in the loader page to set")

        docdir = os.path.join(DIST, *( ["doc"] if lang == "he" else [lang, "doc"] ))
        os.makedirs(docdir, exist_ok=True)
        write_text(os.path.join(docdir, "index.html"), "\n".join(page))

        landdir = DIST if lang == "he" else os.path.join(DIST, lang)
        os.makedirs(landdir, exist_ok=True)
        write_text(os.path.join(landdir, "index.html"), pages[lang])

        for sub, built in (("contact", contact), ("about", about)):
            if lang not in built:
                continue
            sdir = os.path.join(landdir, sub)
            os.makedirs(sdir, exist_ok=True)
            write_text(os.path.join(sdir, "index.html"), built[lang])
    if drafts:
        global ROBOTS
        ROBOTS = "noindex,nofollow"
        template = build_template(shell_template, body, script, css,
                                  preview, portraits, "he", LANGS,
                                  path="/%s/doc/" % PREVIEW)
        page = list(lines)
        page[idx] = template_line(template)
        title = titles["he"] + ", טיוטה"
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
        print("  ! no src/assets/og.png, shared links will preview bare")
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

    icons = write_icons()
    print("  icons      : %s" % ", ".join(icons))

    write_text(os.path.join(DIST, "404.html"), build_404(landing_fonts(
        shell_text, manifest)[0]))

    if SITE_URL:
        write_text(os.path.join(DIST, "robots.txt"),
                   "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % SITE_URL)
        today = datetime.date.today().isoformat()
        rows = []
        # (which page, which languages it exists in, how often, how important)
        for doc, sub, group, freq, pri in (
                (False, None, LANGS, "weekly", "1.0"),
                (True, None, LANGS, "monthly", "0.9"),
                (False, "about", list(CONTACT_LANGS), "yearly", "0.4"),
                (False, "contact", list(CONTACT_LANGS), "yearly", "0.3")):
            for lang in group:
                alts = "".join(
                    '<xhtml:link rel="alternate" hreflang="%s" href="%s%s"/>'
                    % (o, SITE_URL, lang_path(o, doc, sub)) for o in group)
                alts += ('<xhtml:link rel="alternate" hreflang="x-default" '
                         'href="%s%s"/>' % (SITE_URL, lang_path("he", doc, sub)))
                rows.append("  <url><loc>%s%s</loc><lastmod>%s</lastmod>"
                            "<changefreq>%s</changefreq><priority>%s</priority>%s"
                            "</url>\n"
                            % (SITE_URL, lang_path(lang, doc, sub), today, freq, pri, alts))
        write_text(os.path.join(DIST, "%s.txt" % INDEXNOW_KEY), INDEXNOW_KEY + "\n")
        # RFC 9116. Somewhere for a researcher who finds something to write
        # to, so that the first move is an email rather than a disclosure.
        # Expires is required by the RFC and has to be in the future, so it
        # is a year from whenever the build ran.
        expires = (datetime.date.today()
                   + datetime.timedelta(days=365)).isoformat() + "T00:00:00.000Z"
        write_text(os.path.join(DIST, ".well-known", "security.txt"),
                   "Contact: mailto:%s\n"
                   "Expires: %s\n"
                   "Preferred-Languages: he, en\n"
                   "Canonical: %s/.well-known/security.txt\n"
                   % (CONTACT_EMAIL, expires, SITE_URL))
        write_text(os.path.join(DIST, "sitemap.xml"),
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
                   '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
                   + "".join(rows) + "</urlset>\n")
        print("  crawling   : robots.txt + sitemap.xml (%d urls) + IndexNow key"
              % len(rows))

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
        print("NOTE: no transcript, the page will show hero, prologue and footer only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
