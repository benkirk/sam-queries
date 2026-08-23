/* Tell the server which theme to render.
 *
 * Charts are matplotlib SVGs with baked-in colors, cached in Redis, so no
 * stylesheet can retheme them after the fact -- the mode has to be known
 * server-side. Design: docs/plans/implemented/DARK_MODE.md.
 *
 * ONE channel, unlike layout-axis.js, which needs two. A viewport is
 * *discovered* client-side after the server has answered; a theme is
 * *declared*, by the click below, which then reloads. Cookie and browser can
 * never disagree, so an htmx parameter would only split the chart and
 * rendered-HTML cache key spaces on a value that cannot vary. (`read_theme()`
 * still honors `?theme=dark` for hand-debugging; nothing here sets it.)
 *
 * No pre-paint bootstrap is needed: the server renders `data-bs-theme` onto
 * <html> from the cookie, so the first byte is already correct. That is what
 * makes this flash-free by construction rather than by racing the paint.
 *
 * The click reloads because 16 server-rendered charts have their colors baked
 * into their SVG bytes. Leaving them stale is worse than a brief reload, and
 * re-issuing every chart fragment's htmx request would re-derive knowledge
 * nav-view-persistence.js already owns. Steps 1-2 flip the cookie and the
 * attribute first, so the reload paints the NEW theme rather than flashing the
 * old one on the way out.
 */
(function () {
    'use strict';

    var COOKIE = 'sam_theme';
    var LIGHT = 'light';
    var DARK = 'dark';

    /* One year. Unlike sam_layout (a session cookie — the viewport is a
     * property of this visit), a theme is a preference to remember.
     * SameSite=Lax so it survives ordinary navigation without riding
     * cross-site requests. Not HttpOnly: the whole point is that script
     * writes it. It carries no user data. */
    var MAX_AGE = 60 * 60 * 24 * 365;

    function current() {
        return document.documentElement.getAttribute('data-bs-theme') === DARK
            ? DARK : LIGHT;
    }

    function writeCookie(value) {
        try {
            document.cookie = COOKIE + '=' + value +
                ';path=/;max-age=' + MAX_AGE + ';SameSite=Lax' +
                (window.location.protocol === 'https:' ? ';Secure' : '');
        } catch (_) {}
    }

    /* Keep every toggle on the page in sync — the navbar has a desktop copy
     * and an offcanvas copy, and both are in the DOM at once. */
    function syncControls(theme) {
        var next = theme === DARK ? LIGHT : DARK;
        var label = next === DARK ? 'Switch to dark mode' : 'Switch to light mode';
        var controls = document.querySelectorAll('[data-theme-toggle]');
        Array.prototype.forEach.call(controls, function (el) {
            el.setAttribute('aria-label', label);
            el.setAttribute('title', label);
            el.setAttribute('aria-pressed', theme === DARK ? 'true' : 'false');
            var icon = el.querySelector('[data-theme-icon]');
            if (icon) {
                icon.classList.toggle('fa-moon', next === DARK);
                icon.classList.toggle('fa-sun', next === LIGHT);
            }
            var text = el.querySelector('[data-theme-label]');
            if (text) { text.textContent = next === DARK ? 'Dark mode' : 'Light mode'; }
        });
    }

    syncControls(current());

    /* Delegated: the offcanvas copy is present at load, but delegating costs
     * nothing and matches the house pattern in actions.js. */
    document.addEventListener('click', function (event) {
        var toggle = event.target.closest
            ? event.target.closest('[data-theme-toggle]')
            : null;
        if (!toggle) { return; }
        event.preventDefault();

        var next = current() === DARK ? LIGHT : DARK;

        writeCookie(next);                                      /* 1 */
        document.documentElement.setAttribute('data-bs-theme', next);  /* 2 */
        syncControls(next);

        window.location.reload();                               /* 3 */
    });
})();
