/* Tell the server which theme to render.
 *
 * The sibling of layout-axis.js, and deliberately the simpler of the two.
 * Both exist because a rendering mode has to be known *server-side*: charts
 * are matplotlib SVGs with baked-in colours, cached in Redis, so no stylesheet
 * can retheme them after the fact.
 *
 * ## One channel, not two
 *
 * layout-axis.js writes a cookie AND injects `?layout=` into every htmx
 * request, because a viewport is *discovered* client-side after the server has
 * already answered — the first page a visitor loads is rendered before the
 * cookie exists.
 *
 * A theme is never discovered. It is *declared*, by the click below, which
 * then reloads. The cookie and the browser can therefore never disagree, so
 * there is nothing for an htmx parameter to correct. Adding one would split
 * the chart and rendered-HTML cache key spaces on a value that cannot vary.
 *
 * (`read_theme()` still honours `?theme=dark` so it can be driven by hand for
 * debugging. Nothing here ever sets it.)
 *
 * ## Why this file does not set the theme before first paint
 *
 * It doesn't have to. The server already rendered `data-bs-theme` onto <html>
 * from the cookie, so the very first byte of HTML is correct. That is what
 * makes this design flash-free *by construction* rather than by racing the
 * paint — and it is why the nonce-free CSP (utils/csp.py: five routes cache
 * rendered HTML in Redis, so a per-request nonce goes stale on a cache hit)
 * never binds here. The classic inline `<head>` bootstrap script that reads
 * localStorage is not available to us, and we do not need it.
 *
 * ## Why the click reloads the page
 *
 * Steps 1-2 below flip the cookie and the attribute, so the CSS retheme is
 * instant. But there are 16 server-rendered charts across the dashboards whose
 * colours are baked into their SVG bytes. The alternatives to a reload are to
 * leave them stale until the next navigation (a light chart on a dark page is
 * worse than a brief reload) or to hunt down and re-issue every chart
 * fragment's htmx request (fragile, and it re-derives knowledge
 * nav-view-persistence.js already owns). A reload re-renders everything
 * server-side in one pass, in the correct theme, from warm Redis entries after
 * the first user through.
 *
 * Steps 1-2 still run first so the reload paints the NEW theme rather than
 * flashing the old one on the way out.
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
