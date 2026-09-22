/**
 * The chat bubble, for a dealership's own website.
 *
 *   <script src="https://linerai.us/<store>/embed.js" defer></script>
 *
 * **This is the third widget and it cannot share code with the other two.**
 * The storefronts here are React inside our bundle; a dealer's page is
 * somebody else's HTML with somebody else's CSS and no build step we control.
 * So this is plain ES5-ish JavaScript with its own styles, and the thing that
 * keeps the duplication honest is how little it is allowed to do: it draws a
 * button and a panel, and the panel is an iframe of the real `/chat`. Every
 * rule about what the assistant may say, which cars it may name and who the
 * buyer is lives on the other side of that frame, exactly as it does for the
 * storefront widget. A second chat *client* is the thing this must never
 * become -- that is how one surface quietly stops drawing the booking card.
 *
 * **The store comes from this script's own URL.** `/<store>/embed.js` names
 * that dealership, because `StorePrefix` strips the slug before the file is
 * served and
 * `document.currentScript.src` still has it. That is deliberate: a dealer
 * copies one line, and the one line already names them. `data-store`
 * overrides it for a page that must hard-code the slug, and the resolved
 * store is logged once -- on a host serving several dealerships, opening the
 * wrong one is silent and looks completely normal, which is the failure this
 * whole prefix exists to prevent.
 *
 * Everything is inside a shadow root. Their stylesheet cannot reach our
 * button and our styles cannot touch their page -- on a site we have never
 * seen, that is the difference between a widget and a bug report.
 */
(function () {
  'use strict';

  var script = document.currentScript;
  if (!script) return; // Loaded in a way that hides the tag; nothing to anchor to.
  if (window.__linerEmbed) return; // Pasted twice, which happens. One bubble.
  window.__linerEmbed = true;

  var src = new URL(script.src, window.location.href);
  // `/<store>/embed.js` -> `/<store>`; `/embed.js` -> `` (the default store).
  var prefix = src.pathname.replace(/\/embed\.js$/, '');
  var store = script.getAttribute('data-store');
  if (store) prefix = '/' + store.replace(/^\/+|\/+$/g, '');
  var origin = src.origin;
  var chatUrl = origin + prefix + '/chat?embed=1';

  var label = script.getAttribute('data-label') || 'Chat with us';
  // Their brand colour, for the button only. The panel is the real /chat and
  // wears whatever that dealership's profile already says.
  var accent = script.getAttribute('data-color') || '#1f2937';
  var side = script.getAttribute('data-side') === 'left' ? 'left' : 'right';

  console.info('[liner] assistant for ' + (prefix || 'the default store') + ' -> ' + chatUrl);

  var host = document.createElement('div');
  host.setAttribute('data-liner-embed', '');
  var root = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host;

  var style = document.createElement('style');
  style.textContent = [
    ':host { all: initial; }',
    '.wrap {',
    '  position: fixed; bottom: 16px; ' + side + ': 16px; z-index: 2147483000;',
    '  display: flex; flex-direction: column; align-items: flex-end; gap: 12px;',
    '  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;',
    '}',
    // The panel: a sheet on a phone, a corner card on a laptop. Same shape as
    // the storefront widget, for the same reason -- at 390px a 24rem box
    // leaves the chat sharing its width with a keyboard and a booking card.
    '.panel {',
    '  position: fixed; top: 12px; bottom: 80px; left: 12px; right: 12px;',
    '  display: flex; flex-direction: column; overflow: hidden;',
    '  background: #fff; border-radius: 16px; border: 1px solid rgba(0,0,0,.1);',
    '  box-shadow: 0 25px 50px -12px rgba(0,0,0,.35);',
    '  transform-origin: bottom ' + side + ';',
    '  transition: opacity .2s ease-out, transform .2s ease-out, visibility .2s;',
    '  visibility: hidden; opacity: 0; transform: translateY(12px) scale(.95);',
    '}',
    '.panel.open { visibility: visible; opacity: 1; transform: none; }',
    '@media (min-width: 640px) {',
    '  .panel { position: static; width: 26rem; height: min(38rem, calc(100dvh - 7rem)); }',
    '}',
    '@media (prefers-reduced-motion: reduce) { .panel, .bubble { transition: none; } }',
    '.bar {',
    '  display: flex; align-items: center; justify-content: space-between;',
    '  padding: 8px 12px; border-bottom: 1px solid rgba(0,0,0,.1);',
    '  font-size: 14px; font-weight: 600; color: #111;',
    '}',
    '.bar button {',
    '  border: 0; background: none; font-size: 20px; line-height: 1; cursor: pointer;',
    '  color: #666; padding: 0 4px;',
    '}',
    '.frame { flex: 1 1 auto; min-height: 0; border: 0; width: 100%; }',
    '.bubble {',
    '  width: 56px; height: 56px; border-radius: 9999px; border: 0; cursor: pointer;',
    '  background: ' + cssColor(accent) + '; color: #fff;',
    '  box-shadow: 0 10px 15px -3px rgba(0,0,0,.3);',
    '  display: flex; align-items: center; justify-content: center;',
    '  transition: transform .15s ease-out;',
    '}',
    '.bubble:hover { transform: scale(1.05); }',
    '.bubble:active { transform: scale(.95); }',
    '.bubble svg { width: 24px; height: 24px; fill: none; stroke: currentColor; stroke-width: 2; }',
  ].join('\n');

  var wrap = document.createElement('div');
  wrap.className = 'wrap';

  var panel = document.createElement('div');
  panel.className = 'panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', label);

  var bar = document.createElement('div');
  bar.className = 'bar';
  var title = document.createElement('span');
  title.textContent = script.getAttribute('data-title') || label;
  var close = document.createElement('button');
  close.setAttribute('aria-label', 'Close chat');
  close.innerHTML = '&times;';
  bar.appendChild(title);
  bar.appendChild(close);
  panel.appendChild(bar);

  var bubble = document.createElement('button');
  bubble.className = 'bubble';
  bubble.setAttribute('aria-label', label);
  bubble.setAttribute('aria-expanded', 'false');
  bubble.innerHTML =
    '<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.6 8.6 0 0 1-3.8-.9L3 21l1.9-5A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z"/>' +
    '</svg>';

  wrap.appendChild(panel);
  wrap.appendChild(bubble);
  root.appendChild(style);
  root.appendChild(wrap);

  var frame = null;
  var open = false;

  function toggle(next) {
    open = next;
    panel.classList.toggle('open', open);
    bubble.setAttribute('aria-expanded', open ? 'true' : 'false');
    // **Mounted on the first open and kept**, the rule the storefront widget
    // already follows: an iframe that exists before anybody clicks starts a
    // conversation for every visitor who never does, and one torn down on
    // close reloads the document and rebuilds the thread on every reopen.
    if (open && !frame) {
      frame = document.createElement('iframe');
      frame.className = 'frame';
      frame.title = 'Chat';
      frame.src = chatUrl;
      panel.appendChild(frame);
    }
  }

  bubble.addEventListener('click', function () { toggle(!open); });
  close.addEventListener('click', function () { toggle(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && open) toggle(false);
  });

  function mount() { document.body.appendChild(host); }
  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount);

  /** Only a colour, because it lands in a stylesheet -- the same rule the
   *  profile's accent follows. Anything else falls back rather than being
   *  interpolated into CSS, where `red;}body{display:none` is a real input. */
  function cssColor(value) {
    return /^#[0-9a-fA-F]{3,8}$/.test(value) ? value : '#1f2937';
  }
})();
