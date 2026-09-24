/**
 * Liner's chat, on a dealership's own website.
 *
 *   <script src="https://linerai.us/embed.js" data-dealer="alsbou" async></script>
 *
 * One tag, pasted once by the dealer's website provider into their template or
 * added by their agency through Google Tag Manager. Everything else is asked
 * of Liner on every page load (`/<dealer>/api/widget/config`), so the label,
 * the colour, the side, whether lead events go to Tag Manager and whether the
 * bubble shows at all change without anybody touching the dealer's site again.
 *
 * **It draws a button and a frame, and nothing else.** The frame is the real
 * chat (`/widget/<dealer>`), loaded on the buyer's first click and not before
 * -- a frame that exists before anybody clicks starts a conversation for every
 * visitor who never does, and weighs down every page of somebody else's
 * website. Every rule about what the assistant may say, which cars it may name
 * and who the buyer is lives on the other side of that frame. A second chat
 * *client* is the thing this file must never become: that is how one surface
 * quietly stops drawing the booking card.
 *
 * **What it does do, because only the dealer's page can:**
 *
 *  - Reads the page: its address, its title and -- on a car's own page -- the
 *    VIN, so Liner knows which car "is this one still available?" means.
 *  - Keeps the conversation on the dealer's own domain. A frame from another
 *    site gets partitioned, short-lived storage in every modern browser
 *    (Safari clears it after a week without interaction), so the id lives
 *    here, in the dealer site's localStorage, and is handed to the frame. The
 *    chat survives a page load, and a buyer who comes back next week finds
 *    their thread.
 *  - Tells the dealer's Google Tag Manager when something happens -- a chat
 *    started, a lead, an appointment -- with no personal data in it.
 *  - Notices another chat product already running on the page, and the tag
 *    being on the page twice, and says so: in this page's console, and on the
 *    Liner setup page, where somebody can act on it.
 *
 * Plain ES5-shaped JavaScript with its own styles inside a shadow root: their
 * stylesheet cannot reach our button and ours cannot touch their page. On a
 * site we have never seen, that is the difference between a widget and a bug
 * report.
 */
(function () {
  'use strict';

  var VERSION = '2';
  var NAME = '[liner]';

  // ---- which tag is this, and which dealership -------------------------

  var script = document.currentScript || lastTag();
  if (!script || !script.src) {
    console.warn(NAME + ' could not find its own <script> tag, so it cannot tell which dealership it is for.');
    return;
  }
  var src = new URL(script.src, window.location.href);
  var base = src.origin;
  var dealer = clean(
    script.getAttribute('data-dealer') ||
      script.getAttribute('data-store') ||
      src.searchParams.get('dealer') ||
      prefixOf(src.pathname) ||
      (window.LinerWidget && window.LinerWidget.dealer) ||
      ''
  );
  if (!dealer) {
    console.warn(NAME + ' the tag names no dealership. Add data-dealer="<your dealership>" to it.');
    return;
  }

  // **One bubble, however many tags.** A tag in the site template *and* the
  // same tag through Tag Manager is the usual way this happens, and two
  // bubbles stacked in one corner is the kind of thing a dealer notices before
  // we do. The second copy stops here and marks the first, whose install
  // report then carries it to the Liner setup page.
  if (window.__linerWidget) {
    window.__linerWidget.duplicate = true;
    console.warn(NAME + ' the chat tag is on this page more than once (site template and Google Tag Manager?). Only the first one runs; remove the other.');
    return;
  }
  var self = (window.__linerWidget = { version: VERSION, dealer: dealer, duplicate: false });
  window.__linerEmbed = true; // the previous loader's guard, so an old cached copy stands down too

  var local = storage('localStorage');
  var session = storage('sessionStorage');
  var KEY = 'liner.' + dealer + '.';
  /** How long a conversation is picked back up on a return visit. */
  var CONVERSATION_DAYS = 30;

  // ---- ask Liner what to draw ---------------------------------------------

  var configUrl = base + '/' + dealer + '/api/widget/config?origin=' +
    encodeURIComponent(window.location.origin) + '&v=' + VERSION;

  fetch(configUrl, { credentials: 'omit', mode: 'cors' })
    .then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.json();
    })
    .then(start)
    .catch(function (error) {
      console.warn(NAME + ' could not reach Liner for ' + dealer + ' (' + configUrl + '): ' + error.message +
        '. If this site sets a Content-Security-Policy, ' + base + ' must be allowed in connect-src, script-src and frame-src.');
    });

  // A site's own Content-Security-Policy is the one refusal that says nothing
  // on its own -- the bubble simply never appears. Name it.
  document.addEventListener('securitypolicyviolation', function (e) {
    if (e.blockedURI && e.blockedURI.indexOf(base) === 0) {
      console.warn(NAME + " this site's Content-Security-Policy blocked " + e.blockedURI +
        ' (' + e.violatedDirective + '). Add ' + base + ' to it.');
    }
  });

  var config = null;
  var host, root, wrap, panel, bubble, badge, frame;
  var isOpen = false;
  var page = null;
  var scrollLock = null;

  function start(cfg) {
    config = cfg || {};
    if (!config.enabled) {
      console.info(NAME + ' the chat is switched off for ' + dealer + (config.reason ? ': ' + config.reason : '.'));
      return;
    }
    if (!config.allowed) {
      console.warn(NAME + ' not shown here. ' + (config.reason || ''));
      return;
    }
    page = readPage();
    draw();
    console.info(NAME + ' ' + (config.name || dealer) + ' chat ready (dealer ' + dealer + ', loader v' + VERSION + ').');
    // The chat was open on the page the buyer just left: keep it open. They
    // clicked for it once, and a chat that shuts itself on every link they
    // follow reads as one that lost them. **Not on a phone**, where open is
    // the whole screen: a buyer who went back a page is looking for the page,
    // and finding the chat over it again reads as a trap.
    if (session.get(KEY + 'open') === '1' && !phone()) toggle(true, false);
    watchNavigation();
    setTimeout(function () { report(); }, 4000);
    setTimeout(function () { report(); }, 15000);
  }

  // A dealer's own "Chat now" button can open ours rather than a second
  // widget: `LinerWidget.open()`. Defined before the config arrives so a
  // click in that window is not lost -- it opens once the bubble exists.
  var wanted = false;
  var api = (window.LinerWidget = window.LinerWidget || {});
  api.open = function () { if (panel) toggle(true, true); else wanted = true; };
  api.close = function () { if (panel) toggle(false, true); else wanted = false; };

  // ---- the button and the panel -------------------------------------------

  function draw() {
    var launcher = config.launcher || {};
    var side = launcher.side === 'left' ? 'left' : 'right';
    var offset = clamp(parseInt(launcher.offset, 10) || 20, 8, 120);
    var accent = colour(launcher.accent, '#1f2937');
    var ink = colour(launcher.accent_ink, '#ffffff');
    var label = text(launcher.label, 'Chat with us');
    var title = text(launcher.title || config.name, label);

    host = document.createElement('div');
    host.setAttribute('data-liner-embed', '');
    root = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host;

    var style = document.createElement('style');
    style.textContent = [
      ':host { all: initial; }',
      '.wrap { position: fixed; bottom: ' + offset + 'px; ' + side + ': ' + offset + 'px; z-index: 2147483000;',
      '  display: flex; flex-direction: column; align-items: ' + (side === 'left' ? 'flex-start' : 'flex-end') + '; gap: 12px;',
      '  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }',
      // A corner card on a laptop.
      '.panel { width: 380px; height: min(640px, calc(100vh - ' + (offset + 96) + 'px));',
      '  display: flex; flex-direction: column; overflow: hidden; background: #fff;',
      '  border-radius: 16px; border: 1px solid rgba(0,0,0,.1); box-shadow: 0 25px 50px -12px rgba(0,0,0,.35);',
      '  transform-origin: bottom ' + side + '; transition: opacity .2s ease-out, transform .2s ease-out, visibility .2s;',
      '  visibility: hidden; opacity: 0; transform: translateY(12px) scale(.96); }',
      '.panel.open { visibility: visible; opacity: 1; transform: none; }',
      // **The whole screen on a phone.** A 380px card on a 390px screen is a
      // chat sharing its width with the page behind it, and that width is what
      // a keyboard, a booking card and a row of cars all have to fit in.
      '@media (max-width: 639px) {',
      '  .panel { position: fixed; top: 0; left: 0; right: 0; bottom: 0; width: 100%; height: 100%;',
      '    height: 100dvh; border-radius: 0; border: 0; box-shadow: none;',
      '    padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);',
      '    box-sizing: border-box; background: #fff; }',
      '  .wrap.open .bubble { display: none; }',
      '}',
      '@media (prefers-reduced-motion: reduce) { .panel, .bubble { transition: none; } }',
      '.bar { display: flex; align-items: center; justify-content: space-between; gap: 8px;',
      '  padding: 10px 12px 10px 16px; border-bottom: 1px solid rgba(0,0,0,.1); background: #fff;',
      '  font-size: 15px; font-weight: 600; color: #111; }',
      '.bar span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
      '.bar button { border: 0; background: none; font-size: 26px; line-height: 1; cursor: pointer;',
      '  color: #555; padding: 2px 6px; border-radius: 6px; }',
      '.bar button:focus-visible, .bubble:focus-visible { outline: 3px solid ' + accent + '; outline-offset: 2px; }',
      '.frame { flex: 1 1 auto; min-height: 0; border: 0; width: 100%; display: block; background: #fff; }',
      '.bubble { position: relative; width: 60px; height: 60px; border-radius: 9999px; border: 0; cursor: pointer;',
      '  background: ' + accent + '; color: ' + ink + '; box-shadow: 0 10px 15px -3px rgba(0,0,0,.3);',
      '  display: flex; align-items: center; justify-content: center; transition: transform .15s ease-out; }',
      '.bubble:hover { transform: scale(1.05); }',
      '.bubble:active { transform: scale(.95); }',
      '.bubble svg { width: 26px; height: 26px; fill: none; stroke: currentColor; stroke-width: 2; }',
      '.badge { position: absolute; top: -2px; ' + (side === 'left' ? 'left' : 'right') + ': -2px; min-width: 20px; height: 20px;',
      '  padding: 0 5px; box-sizing: border-box; border-radius: 9999px; background: #dc2626; color: #fff;',
      '  font-size: 12px; font-weight: 700; line-height: 20px; text-align: center; display: none; }',
      '.badge.on { display: block; }',
    ].join('\n');

    wrap = document.createElement('div');
    wrap.className = 'wrap';

    panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'liner-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', title);

    var bar = document.createElement('div');
    bar.className = 'bar';
    var heading = document.createElement('span');
    heading.textContent = title;
    var close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close chat');
    // Escaped, and the whole file kept to ASCII: a script with no declared
    // charset is read in the *host page's* encoding, and on a page served as
    // windows-1252 a literal multiplication sign arrives as two letters.
    close.textContent = '\u00d7';
    bar.appendChild(heading);
    bar.appendChild(close);
    panel.appendChild(bar);

    bubble = document.createElement('button');
    bubble.type = 'button';
    bubble.className = 'bubble';
    bubble.setAttribute('aria-label', label);
    bubble.setAttribute('aria-expanded', 'false');
    bubble.setAttribute('aria-controls', 'liner-panel');
    bubble.title = label;
    bubble.innerHTML =
      '<svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.6 8.6 0 0 1-3.8-.9L3 21l1.9-5A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z"/>' +
      '</svg>';
    badge = document.createElement('span');
    badge.className = 'badge';
    badge.setAttribute('aria-hidden', 'true');
    bubble.appendChild(badge);

    wrap.appendChild(panel);
    wrap.appendChild(bubble);
    root.appendChild(style);
    root.appendChild(wrap);

    bubble.addEventListener('click', function () { toggle(!isOpen, true); });
    close.addEventListener('click', function () { toggle(false, true); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && isOpen) toggle(false, true);
    });

    if (document.body) document.body.appendChild(host);
    else document.addEventListener('DOMContentLoaded', function () { document.body.appendChild(host); });
    if (wanted) setTimeout(function () { toggle(true, true); }, 0);
  }

  function phone() {
    return !!(window.matchMedia && window.matchMedia('(max-width: 639px)').matches);
  }

  function toggle(next, byBuyer) {
    isOpen = next;
    panel.classList.toggle('open', isOpen);
    wrap.classList.toggle('open', isOpen);
    bubble.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    session.set(KEY + 'open', isOpen ? '1' : '0');
    lockScroll(isOpen);
    if (isOpen) {
      setBadge(0);
      // Mounted on the first open and kept: one torn down on close reloads
      // the document and rebuilds the thread on every reopen.
      if (!frame) mountFrame();
      if (byBuyer) track('chat_open', {});
    }
    post({ type: 'visibility', open: isOpen });
  }

  /** On a phone the chat is the whole screen, so the page under it must not
   *  scroll when the buyer drags the conversation. Put back exactly as it was. */
  function lockScroll(on) {
    var html = document.documentElement;
    if (on && phone() && !scrollLock) {
      scrollLock = { html: html.style.overflow, body: document.body ? document.body.style.overflow : '' };
      html.style.overflow = 'hidden';
      if (document.body) document.body.style.overflow = 'hidden';
    } else if (!on && scrollLock) {
      html.style.overflow = scrollLock.html;
      if (document.body) document.body.style.overflow = scrollLock.body;
      scrollLock = null;
    }
  }

  function mountFrame() {
    // Read again now: an `async` tag can run before the listing has rendered,
    // and by the time somebody clicks, it has.
    page = readPage();
    frame = document.createElement('iframe');
    frame.className = 'frame';
    frame.title = 'Chat with ' + text(config.name, 'us');
    // The frame is ours, but the page around it is theirs: it may open a new
    // tab (a finance application, a Carfax) and it may not navigate their
    // page away from under the buyer.
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox');
    frame.src = base + (config.frame || '/widget/' + dealer) +
      '?parent=' + encodeURIComponent(window.location.origin) + '&v=' + VERSION;
    panel.appendChild(frame);
  }

  function setBadge(count) {
    if (!badge) return;
    badge.textContent = count > 9 ? '9+' : String(count || '');
    badge.classList.toggle('on', count > 0);
    bubble.setAttribute('aria-label', text((config.launcher || {}).label, 'Chat with us') +
      (count > 0 ? ' (' + count + ' new)' : ''));
  }

  // ---- talking to the frame --------------------------------------------------

  function post(message) {
    if (!frame || !frame.contentWindow) return;
    message.liner = 1;
    // To our origin only: a frame that has been navigated somewhere else must
    // not receive the buyer's conversation id.
    frame.contentWindow.postMessage(message, base);
  }

  window.addEventListener('message', function (e) {
    if (!frame || e.source !== frame.contentWindow || e.origin !== base) return;
    var m = e.data;
    if (!m || m.liner !== 1 || typeof m.type !== 'string') return;
    if (m.type === 'hello') {
      post({ type: 'init', conversationId: conversation(), page: page, open: isOpen });
    } else if (m.type === 'session') {
      remember(m.conversationId);
    } else if (m.type === 'event') {
      // The id first, so a once-per-conversation event is keyed on the right
      // conversation even if it overtook the `session` message.
      if (m.conversationId) remember(m.conversationId);
      track(m.name, m.data || {});
    } else if (m.type === 'unread') {
      if (!isOpen) setBadge(parseInt(m.count, 10) || 0);
    } else if (m.type === 'close') {
      toggle(false, true);
    }
  });

  var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function conversation() {
    var raw = local.get(KEY + 'conversation');
    if (!raw) return null;
    try {
      var saved = JSON.parse(raw);
      var age = Date.now() - (saved.at || 0);
      if (!UUID.test(saved.id || '') || age > CONVERSATION_DAYS * 864e5) return null;
      return saved.id;
    } catch (err) {
      return null;
    }
  }

  function remember(id) {
    if (typeof id !== 'string' || !UUID.test(id)) return;
    local.set(KEY + 'conversation', JSON.stringify({ id: id, at: Date.now() }));
  }

  // ---- Google Tag Manager ----------------------------------------------------

  /** Events the chat may raise, and the ones counted once per conversation --
   *  a lead is one lead however many times the buyer corrects their number. */
  var EVENTS = { chat_open: 1, chat_start: 1, lead: 1, appointment: 1, credit_app: 1 };
  var ONCE = { chat_start: 1, lead: 1, appointment: 1 };

  /**
   * Each moment by the name the car industry's own analytics standard gives
   * it (the Automotive Standards Council's GA4 events), which a dealer's
   * agency already knows how to count and to import into Google Ads as a
   * conversion. `event_owner` says the event is Liner's, so it never passes
   * for the site's own forms.
   */
  var ASC = {
    chat_open: [['asc_comm_engagement', { comm_type: 'chat', comm_status: 'start' }]],
    chat_start: [['asc_comm_engagement', { comm_type: 'chat', comm_status: 'engage' }]],
    lead: [
      ['asc_comm_submission', { comm_type: 'chat', comm_status: 'crm_update' }],
      ['asc_comm_submission_sales', { comm_type: 'chat', comm_status: 'crm_update' }],
    ],
    appointment: [['asc_comm_submission_sales_appt', { comm_type: 'chat', comm_status: 'crm_update' }]],
    credit_app: [['asc_cta_interaction', {
      element_text: 'start the application', element_type: 'credit_application',
      event_action: 'click', event_action_result: 'open',
    }]],
  };

  /**
   * Pushed to the dealer's own `dataLayer`, where their Tag Manager container
   * turns it into a GA4 event or an ad platform's conversion. **Nothing
   * personal, ever**: no name, no email, no phone number, no words the buyer
   * typed -- Google's terms forbid it in Analytics, and every tag in their
   * container can read the data layer. The car is fine: it is on the page.
   *
   * Every push carries every key, blank where there is nothing to say,
   * because Tag Manager's data layer *remembers* a key between pushes: an
   * appointment pushed without a VIN would otherwise go out carrying the VIN
   * of whichever car a previous event mentioned.
   */
  function track(name, data) {
    if (!config || !config.gtm || !EVENTS[name]) return;
    if (ONCE[name]) {
      var once = KEY + 'sent.' + name + '.' + (conversation() || 'none');
      if (local.get(once)) return;
      local.set(once, '1');
    }
    var car = data.car || {};
    var vin = vinOf(car.vin || data.vin) || (page && page.vin) || '';
    var layer = (window.dataLayer = window.dataLayer || []);
    var style = config.events || 'asc';
    if (style !== 'liner') {
      // The site's own ASC data layer, where its platform keeps one.
      var site = window.asc_datalayer || {};
      var steps = ASC[name] || [];
      for (var i = 0; i < steps.length; i++) {
        var push = {
          event: steps[i][0],
          event_owner: 'liner',
          affiliation: short(site.affiliation),
          page_type: short(site.page_type) || (vin ? 'item' : ''),
          department: 'sales',
          comm_type: '', comm_status: '',
          element_text: '', element_type: '', event_action: '', event_action_result: '',
          item_id: vin,
          item_year: short(car.year),
          item_make: short(car.make),
          item_model: short(car.model),
          item_variant: short(car.trim),
        };
        for (var key in steps[i][1]) push[key] = steps[i][1][key];
        layer.push(push);
      }
    }
    if (style !== 'asc') {
      layer.push({
        event: 'liner_' + name,
        liner_dealer: dealer,
        liner_vin: vin,
        liner_vehicle: text(data.vehicle, ''),
        liner_page_type: vin ? 'vehicle' : 'other',
      });
    }
  }

  /** A value GA4 will keep: lowercase, as the standard asks, and within its
   *  hundred-character limit. */
  function short(value) {
    return String(value == null ? '' : value).toLowerCase().slice(0, 100);
  }

  // ---- the page the buyer is on ----------------------------------------------

  function readPage() {
    return {
      url: String(window.location.href).split('#')[0].slice(0, 2000),
      title: String(document.title || '').slice(0, 300),
      vin: findVin(),
    };
  }

  /** Dealer sites that change page without a full load (pushState) are
   *  followed by watching the address rather than by patching their history
   *  functions -- this is somebody else's page. The page is re-read after a
   *  moment, so the new listing has rendered. */
  function watchNavigation() {
    var last = window.location.href;
    setInterval(function () {
      if (window.location.href === last) return;
      last = window.location.href;
      setTimeout(function () {
        page = readPage();
        post({ type: 'page', page: page });
      }, 800);
    }, 1000);
  }

  var VIN = /\b[A-HJ-NPR-Z0-9]{17}\b/gi;

  function vinOf(value) {
    var v = String(value || '').trim().toUpperCase();
    return /^[A-HJ-NPR-Z0-9]{17}$/.test(v) ? v : '';
  }

  /**
   * The VIN of the car this page is about, or ''. **Only when the page is
   * about exactly one car**: a list of twenty cars has twenty VINs, and
   * guessing one of them would have Liner talking about a car the buyer never
   * clicked. A '' is not the end of it -- the page's address goes to Liner
   * as well, and Liner knows every car's own page on the dealer's site, which
   * is the better evidence where a platform has one. In order of how much the
   * page means it:
   *
   *  1. Something put there for us: `window.LinerWidget.vin`, a
   *     `<meta name="liner:vin">`, or a `data-liner-vin` attribute.
   *  2. A platform's own once-per-page marker. Get My Auto writes
   *     `<script id="at-vehicle-<VIN>">` on a car's page; the element's id is
   *     read rather than what the script sets, because after an in-page
   *     navigation the script may never have run.
   *  3. Structured data -- schema.org `vehicleIdentificationNumber` -- **for
   *     the car whose own address is this page**. A car page commonly
   *     carries several (the car, then three "similar vehicles"), and the
   *     first is not reliably the one being looked at.
   *  4. `data-vin` attributes, but only one distinct VIN among them: on a car
   *     page they are as often the similar vehicles' as the car's.
   *  5. The address, then the visible text -- and from these two, only a VIN
   *     whose check digit is right, because seventeen letters and digits in a
   *     URL or a paragraph are as likely to be a tracking id.
   */
  function findVin() {
    try {
      var explicit = vinOf(window.LinerWidget && window.LinerWidget.vin) ||
        vinOf(attrOf('meta[name="liner:vin"]', 'content')) ||
        vinOf(attrOf('[data-liner-vin]', 'data-liner-vin'));
      if (explicit) return explicit;

      var found = {};
      var marks = document.querySelectorAll('script[id^="at-vehicle-"]');
      for (var m = 0; m < marks.length && m < 20; m++) add(found, marks[m].id.slice(11), false);
      var one = only(found);
      if (one) return one;

      var cars = [];
      var scripts = document.querySelectorAll('script[type="application/ld+json"]');
      for (var i = 0; i < scripts.length && i < 50; i++) {
        try { collect(JSON.parse(scripts[i].textContent || 'null'), cars, 0); } catch (err) { /* not JSON */ }
      }
      var items = document.querySelectorAll('[itemprop="vehicleIdentificationNumber"]');
      for (var j = 0; j < items.length && j < 50; j++) {
        cars.push({ vin: items[j].getAttribute('content') || items[j].textContent, url: '' });
      }
      var here = pathOf(window.location.href);
      var mine = {};
      found = {};
      for (var c = 0; c < cars.length; c++) {
        add(found, cars[c].vin, false);
        if (cars[c].url && pathOf(cars[c].url) === here) add(mine, cars[c].vin, false);
      }
      one = only(mine);
      if (one) return one;
      // Exactly one car described is that car, whatever address it gives.
      // Several and none of them this page's is a page we cannot name one
      // car on from here: '' and Liner reads the address instead.
      one = only(found);
      if (one !== null) return one;

      found = {};
      var tagged = document.querySelectorAll('[data-vin]');
      for (var k = 0; k < tagged.length && k < 200; k++) add(found, tagged[k].getAttribute('data-vin'), false);
      one = only(found);
      if (one !== null) return one;

      found = {};
      scan(decodeURIComponent(window.location.pathname + ' ' + window.location.search), found);
      one = only(found);
      if (one !== null) return one;

      found = {};
      scan(document.body ? String(document.body.innerText || '').slice(0, 200000) : '', found);
      one = only(found);
      return one || '';
    } catch (err) {
      return '';
    }
  }

  /** Every object carrying a VIN, with the address it says it is about. */
  function collect(node, cars, depth) {
    if (!node || depth > 6 || cars.length > 200) return;
    if (Object.prototype.toString.call(node) === '[object Array]') {
      for (var i = 0; i < node.length && i < 100; i++) collect(node[i], cars, depth + 1);
      return;
    }
    if (typeof node !== 'object') return;
    if (node.vehicleIdentificationNumber) {
      cars.push({
        vin: node.vehicleIdentificationNumber,
        url: String(node.url || node['@id'] || (node.offers && node.offers.url) || ''),
      });
    }
    for (var key in node) {
      if (Object.prototype.hasOwnProperty.call(node, key) && typeof node[key] === 'object') {
        collect(node[key], cars, depth + 1);
      }
    }
  }

  function pathOf(url) {
    try {
      return new URL(url, window.location.href).pathname.replace(/\/+$/, '').toLowerCase();
    } catch (err) {
      return '';
    }
  }

  function scan(textValue, found) {
    var match;
    VIN.lastIndex = 0;
    while ((match = VIN.exec(textValue))) add(found, match[0], true);
  }

  function add(found, value, needCheckDigit) {
    var v = vinOf(value);
    if (v && (!needCheckDigit || checkDigit(v))) found[v] = true;
  }

  /** The one key, null when there were several (a list page), '' for none. */
  function only(found) {
    var keys = [];
    for (var k in found) if (Object.prototype.hasOwnProperty.call(found, k)) keys.push(k);
    if (keys.length === 1) return keys[0];
    return keys.length > 1 ? '' : null;
  }

  var VALUES = { A: 1, B: 2, C: 3, D: 4, E: 5, F: 6, G: 7, H: 8, J: 1, K: 2, L: 3, M: 4, N: 5,
    P: 7, R: 9, S: 2, T: 3, U: 4, V: 5, W: 6, X: 7, Y: 8, Z: 9 };
  var WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2];

  /** The ninth character of a North American VIN is a check digit over the
   *  other sixteen. Every car sold new in the US since 1981 has one. */
  function checkDigit(vin) {
    var sum = 0;
    for (var i = 0; i < 17; i++) {
      var c = vin.charAt(i);
      var value = c >= '0' && c <= '9' ? +c : VALUES[c];
      if (value === undefined) return false;
      sum += value * WEIGHTS[i];
    }
    var r = sum % 11;
    return vin.charAt(8) === (r === 10 ? 'X' : String(r));
  }

  // ---- another chat on the page ----------------------------------------------

  /**
   * Chat products we know by their fingerprints: where their script comes
   * from, the global they leave behind, the element they draw. **Reported,
   * never removed**: another vendor's widget is part of somebody's contract,
   * and a script that deleted it would be a far worse surprise than two
   * bubbles. A buyer offered two chats answers in one of them, and the lead
   * may land in the other system -- which is worth a dealer knowing on the day
   * the tag goes live rather than the month after.
   */
  //
  // Each row is [name, script or frame hosts, globals, elements], taken from
  // the vendors' own code as it runs on live dealer sites, the public tracker
  // databases and the ad-block lists that catalogue them. **Only what means
  // a chat**: Capital One's loader also draws the pre-qualification buttons
  // on every card, and Elfsight's platform script also draws reviews and
  // banners -- matching either would report a chat on a site with none,
  // which is Alsbou's site, so neither is here. Their chat's own frame is.
  var OTHERS = [
    ['Capital One Chat Concierge', ['chat.autodriven.com'], [],
      ['iframe#c1-leads-chat', 'iframe[title="Chat Concierge"]', '.capital-one-chat-embedded', 'chat-placeholder-bootstrapper']],
    ['Gubagoo', ['gubagoo.io', 'gubagootracking.com'], [], []],
    ['CarNow', ['carnow.com'], [], []],
    ['ActivEngage', ['activengage.com'], [], []],
    ['LivePerson', ['liveperson.net', 'lpsnmedia.net', 'contactatonce.com'], ['lpTag'], ['.LPMcontainer']],
    ['Podium', ['connect.podium.com'], [], ['iframe#podium-bubble', '#podium-prompt', 'script#podium-widget']],
    ['Kenect', ['kenect.com'], [], []],
    ['Matador', ['matador.ai'], [], []],
    ['Fullpath', ['autoleadstar.com', 'fullpath.com'], [], []],
    ['Impel chat', ['chat-cdn.impel.ai'], [], []],
    ['Dealer Inspire Conversations', ['conversations.dealerinspire.com'], [], []],
    ['CarChat24', ['carchat24.com'], [], []],
    ['Edmunds CarCode', ['carcodesms.com'], [], []],
    ['DriveCentric', ['genius.drivecentric.com', 'drivecentric.com/external/genius'], [], []],
    ['Birdeye', ['webchat.birdeye.com', 'birdeye.com/embed', 'widget-v7-cdn.birdeye.com'], [], []],
    ['Intercom', ['widget.intercom.io', 'js.intercomcdn.com'], ['Intercom'],
      ['#intercom-container', '.intercom-lightweight-app', '.intercom-launcher']],
    ['Drift', ['js.driftt.com'], ['drift', 'driftt'], ['#drift-widget', '#drift-frame-controller']],
    ['Zendesk', ['static.zdassets.com', 'zopim.com'], ['zE', '$zopim'], ['script#ze-snippet']],
    ['LiveChat', ['cdn.livechatinc.com', 'secure.livechatinc.com'], ['LiveChatWidget', 'LC_API'], ['#chat-widget-container']],
    ['Tawk.to', ['embed.tawk.to'], ['Tawk_API'], []],
    ['Tidio', ['code.tidio.co', 'tidiochat.com'], ['tidioChatApi'], ['#tidio-chat']],
    ['HubSpot chat', ['js.usemessages.com', 'app.hubspot.com/conversations-visitor'], ['HubSpotConversations'],
      ['#hubspot-messages-iframe-container']],
    ['Facebook Messenger', ['xfbml.customerchat'], [], ['.fb-customerchat']],
    ['Crisp', ['client.crisp.chat'], ['$crisp'], ['.crisp-client']],
    ['Olark', ['static.olark.com'], ['olark'], ['#olark-wrapper']],
    ['Freshchat', ['wchat.freshchat.com', 'wchat.eu.freshchat.com'], ['fcWidget'], ['#fc_frame']],
    ['Salesforce chat', ['/embeddedservice/', 'esw.min.js'], [], []],
    ['Zoho SalesIQ', ['salesiq.zoho', 'js.zohocdn.com/salesiq'], [], []],
    ['Jivo', ['code.jivosite.com'], [], []],
    ['Chatra', ['call.chatra.io'], [], []],
    ['Qualified', ['js.qualified.com'], [], []],
    ['Ada', ['static.ada.support'], [], []],
    ['Chatwoot', [], ['$chatwoot'], []],
    ['Help Scout', ['beacon-v2.helpscout.net'], [], []],
    ['Front chat', ['chat-assets.frontapp.com'], ['FrontChat'], []],
    ['Userlike', ['userlike-cdn-widgets'], [], []],
  ];

  /** Vendors seen in what the page has loaded so far, however it loaded it.
   *  An observer rather than one look at the timing buffer: Tag Manager adds
   *  scripts well after this runs, and the buffer stops recording at 250
   *  entries -- a dealer's homepage passes that before its chat arrives. */
  var heard = {};
  var ownOrigin = base.toLowerCase();

  function hear(url) {
    url = String(url || '').toLowerCase();
    if (!url || url.indexOf(ownOrigin) === 0) return;
    for (var i = 0; i < OTHERS.length; i++) {
      for (var j = 0; j < OTHERS[i][1].length; j++) {
        if (url.indexOf(OTHERS[i][1][j]) !== -1) { heard[OTHERS[i][0]] = true; break; }
      }
    }
  }

  try {
    if (window.PerformanceObserver) {
      new PerformanceObserver(function (list) {
        var entries = list.getEntries();
        for (var i = 0; i < entries.length; i++) hear(entries[i].name);
      }).observe({ type: 'resource', buffered: true });
    }
  } catch (err) { /* an older browser: the look in others() still runs */ }

  function others() {
    var found = [];
    var i, j;
    var tags = document.querySelectorAll('script[src], iframe[src]');
    for (i = 0; i < tags.length && i < 400; i++) hear(tags[i].src);
    try {
      var loaded = window.performance && performance.getEntriesByType ? performance.getEntriesByType('resource') : [];
      for (i = 0; i < loaded.length && i < 600; i++) hear(loaded[i].name);
    } catch (err) { /* no timing API */ }

    for (i = 0; i < OTHERS.length; i++) {
      var entry = OTHERS[i];
      var hit = !!heard[entry[0]];
      for (j = 0; !hit && j < entry[2].length; j++) {
        try { if (window[entry[2][j]] !== undefined) hit = true; } catch (err) { /* a getter that throws */ }
      }
      for (j = 0; !hit && j < entry[3].length; j++) {
        try { if (document.querySelector(entry[3][j])) hit = true; } catch (err) { /* a selector this browser cannot parse */ }
      }
      if (hit) found.push(entry[0]);
    }
    return found;
  }

  /**
   * Tell Liner the tag is live here, and what it found. Once a few seconds in
   * and once more after the slow widgets have loaded; a beacon, so a buyer
   * leaving the page does not cancel it and nothing waits on it. Only from a
   * page this dealership listed -- Liner refuses anything else.
   */
  function report() {
    // One of our own pages -- the storefront, a rehearsal -- is not an
    // install on anybody's site, and Liner would refuse the report anyway.
    if (window.location.origin === base) return;
    var found = others();
    if (found.length) {
      console.warn(NAME + ' another chat is running on this page as well: ' + found.join(', ') +
        '. Buyers may answer in either one; the Liner setup page shows this too.');
    }
    var body = JSON.stringify({
      version: VERSION,
      page: String(window.location.href).split('#')[0].slice(0, 2000),
      others: found,
      duplicate: !!self.duplicate,
      gtm: !!window.google_tag_manager,
    });
    var signature = body.replace(/"page":"[^"]*"/, '');
    if (session.get(KEY + 'reported') === signature) return;
    session.set(KEY + 'reported', signature);
    var url = base + '/' + dealer + '/api/widget/install-report';
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(url, new Blob([body], { type: 'text/plain' }))) return;
    } catch (err) { /* fall through */ }
    try {
      fetch(url, { method: 'POST', body: body, mode: 'no-cors', keepalive: true, credentials: 'omit' });
    } catch (err) { /* nothing more to do */ }
  }

  // ---- small helpers -------------------------------------------------------

  function lastTag() {
    var tags = document.querySelectorAll('script[src*="embed.js"]');
    return tags.length ? tags[tags.length - 1] : null;
  }

  /** `/alsbou/embed.js` -> `alsbou`, the shape the first loader was pasted as. */
  function prefixOf(path) {
    var match = /^\/([a-z0-9-]+)\/embed\.js$/i.exec(path || '');
    return match ? match[1] : '';
  }

  function clean(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 60);
  }

  /** Only a colour, because it lands in a stylesheet on somebody else's page:
   *  anything else falls back rather than being interpolated into CSS, where
   *  `red;}body{display:none` is a real input. */
  function colour(value, fallback) {
    return /^#[0-9a-fA-F]{3,8}$/.test(String(value || '')) ? value : fallback;
  }

  function text(value, fallback) {
    var t = String(value || '').replace(/\s+/g, ' ').trim();
    return t ? t.slice(0, 80) : fallback;
  }

  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  function attrOf(selector, name) {
    var el = document.querySelector(selector);
    return el ? el.getAttribute(name) : '';
  }

  /** Storage that may not exist (a private window, blocked site data) and
   *  must never throw into somebody else's page because of it. */
  function storage(kind) {
    var memory = {};
    var store = null;
    try {
      store = window[kind];
      store.setItem('liner.probe', '1');
      store.removeItem('liner.probe');
    } catch (err) {
      store = null;
    }
    return {
      get: function (k) {
        try { return store ? store.getItem(k) : memory[k] || null; } catch (err) { return memory[k] || null; }
      },
      set: function (k, v) {
        memory[k] = v;
        try { if (store) store.setItem(k, v); } catch (err) { /* full, or blocked */ }
      },
    };
  }
})();
