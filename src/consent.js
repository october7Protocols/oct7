/* Consent, and the container it gates.

   Injected into the head of both pages by build.py, which fills in
   __GTM_ID__ and __CONSENT_STRINGS__.

   The point of the gate is that it is real: outside Israel nothing of
   Google's is fetched at all until the reader accepts. A banner that lets
   the container load first and then asks is decoration, and under the GDPR
   it is also not consent. Inside Israel the container loads as before.

   It runs before the page's own script, so it cannot use the page's i18n
   and carries its own three strings per language instead. */
(function () {
  var ID = '__GTM_ID__';
  var T = __CONSENT_STRINGS__;
  var KEY = 'oct7.consent';

  function get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  /* The same rule the pages use to pick a language, for the same reason: a
     geo-IP lookup would be a third-party request carrying the reader's
     address, which is the opposite of what a consent banner is for. */
  function inIsrael() {
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
      return tz === 'Asia/Jerusalem' || tz === 'Asia/Tel_Aviv';
    } catch (e) { return false; }
  }

  function lang() {
    var saved = get('oct7.lang');
    if (saved && T[saved]) return saved;
    if (inIsrael() && T.he) return 'he';
    var want = navigator.languages || [navigator.language || ''];
    for (var i = 0; i < want.length; i++) {
      var c = String(want[i]).slice(0, 2).toLowerCase();
      if (c === 'iw') c = 'he';
      if (T[c]) return c;
    }
    return T.en ? 'en' : 'he';
  }

  function load() {
    if (window.__gtmLoaded) return;
    window.__gtmLoaded = true;
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
    var f = document.getElementsByTagName('script')[0];
    var j = document.createElement('script');
    j.async = true;
    j.src = 'https://www.googletagmanager.com/gtm.js?id=' + ID;
    f.parentNode.insertBefore(j, f);
  }

  /* Events pushed before a decision are kept: the reader who accepts at the
     third chapter should still count as having read three. */
  window.dataLayer = window.dataLayer || [];

  var choice = get(KEY);
  if (inIsrael() || choice === 'granted') { load(); return; }
  if (choice === 'denied') return;

  function banner() {
    var t = T[lang()] || T.en || T.he;
    var rtl = lang() === 'he' || lang() === 'ar';
    var wrap = document.createElement('div');
    wrap.setAttribute('dir', rtl ? 'rtl' : 'ltr');
    wrap.setAttribute('lang', lang());
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-live', 'polite');
    wrap.style.cssText = [
      'position:fixed', 'z-index:9999', 'inset-inline:0', 'bottom:0',
      'background:rgba(4,8,26,0.96)', '-webkit-backdrop-filter:blur(12px)',
      'backdrop-filter:blur(12px)', 'border-top:1px solid rgba(255,255,255,0.18)',
      'box-shadow:0 -8px 30px rgba(0,0,0,0.5)',
      'font-family:Heebo,system-ui,sans-serif', 'color:#c3cbe6',
      'padding:14px clamp(16px,4vw,28px)',
      'display:flex', 'align-items:center', 'justify-content:center',
      'gap:clamp(12px,2.5vw,22px)', 'flex-wrap:wrap'
    ].join(';');

    var p = document.createElement('p');
    p.textContent = t.consentText;
    p.style.cssText = 'margin:0;flex:1 1 320px;max-width:62ch;font-size:clamp(12.5px,3.2vw,14px);font-weight:300;line-height:1.55';

    var row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;flex:0 0 auto';

    function button(label, primary, onclick) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.style.cssText = 'cursor:pointer;border:1px solid ' +
        (primary ? '#c8102e' : 'rgba(255,255,255,0.28)') +
        ';background:' + (primary ? '#c8102e' : 'transparent') +
        ';color:' + (primary ? '#fff' : '#c3cbe6') +
        ';padding:10px 18px;font-family:inherit;font-size:clamp(12.5px,3.2vw,14px);' +
        'font-weight:700;line-height:1;transition:background .2s ease,color .2s ease';
      b.onclick = onclick;
      return b;
    }

    function close() { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); }

    row.appendChild(button(t.consentNo, false, function () { set(KEY, 'denied'); close(); }));
    row.appendChild(button(t.consentYes, true, function () { set(KEY, 'granted'); close(); load(); }));
    wrap.appendChild(p);
    wrap.appendChild(row);
    document.body.appendChild(wrap);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', banner);
  } else {
    banner();
  }
})();
