/* Tell the server which chart layout to render.
 *
 * Charts are matplotlib SVGs rendered server-side at a fixed figure size, so
 * the server has to know the viewport -- no stylesheet can fix a 9pt label
 * scaled to 2px on a phone. Measurements: docs/plans/implemented/MOBILE_CHARTS.md.
 *
 * TWO channels, and both are required. A cookie, read by every route, AND an
 * htmx parameter on every fragment request:
 *
 *   - 9 of the 18 chart call sites render in a full-page GET, so
 *     `htmx:configRequest` never fires for them;
 *   - the cookie cannot be set before the page that sets it. CSP here is
 *     nonce-free by design (utils/csp.py), which rules out an inline head
 *     script, so this file runs at end of body -- after the server already
 *     chose a layout.
 *
 * Cost of that: a first-ever visit on a phone gets desktop-sized charts on one
 * page. They fit; they are merely small.
 *
 * There is deliberately NO `matchMedia` change listener. A drag-resize across
 * the breakpoint would fire a burst of chart renders, each a matplotlib figure.
 */
(function () {
    'use strict';

    /* Bootstrap's `md` breakpoint, matching dashboard-init.js's
     * collapseFilterPanels. One definition of "phone" across the app. */
    var MOBILE_QUERY = '(max-width: 767.98px)';

    /* Bootstrap's `xl`. The upper edge of the tablet band was measured, not
     * picked: on these pages the chart's card is the viewport less ~144px, so
     * the desktop figure's smallest label lands at 6.0px at 768, 8.1px at
     * 1024, 9.7px at 1200 and 10.4px at 1280. Desktop stops being the problem
     * around 1110-1200, and `xl` is the breakpoint there. Going up to `xxl`
     * instead would hand every 1280 laptop a figure sized for a 640px card. */
    var TABLET_QUERY = '(max-width: 1199.98px)';

    var COOKIE = 'sam_layout';
    var PARAM = 'layout';

    /* Narrowest match wins — below 768 both queries are true. */
    function currentLayout() {
        if (!window.matchMedia) { return 'desktop'; }
        if (window.matchMedia(MOBILE_QUERY).matches) { return 'mobile'; }
        if (window.matchMedia(TABLET_QUERY).matches) { return 'tablet'; }
        return 'desktop';
    }

    /* Session cookie (no Max-Age): the viewport is a property of this visit,
     * not a preference to remember. SameSite=Lax so it survives ordinary
     * navigation without riding cross-site requests. Not HttpOnly — the whole
     * point is that script writes it. It carries no user data. */
    function writeCookie(value) {
        try {
            document.cookie = COOKIE + '=' + value + ';path=/;SameSite=Lax';
        } catch (_) {}
    }

    writeCookie(currentLayout());

    document.addEventListener('htmx:configRequest', function (event) {
        var detail = event.detail;
        if (!detail || !detail.parameters) return;

        var layout = currentLayout();
        writeCookie(layout);
        detail.parameters[PARAM] = layout;

        /* htmx appends `parameters` to `path` for GET requests, so a `layout`
         * already encoded in an hx-get URL would produce a duplicate query key
         * and `request.args.get` would return the FIRST — silently defeating
         * this override. Same hazard, and same fix, as `injectSaved` in
         * nav-view-persistence.js; it bites here too because the status
         * history pages carry ?layout= forward in their own links. */
        var path = detail.path;
        if (path && path.indexOf('?') !== -1) {
            var parts = path.split('?');
            var qs;
            try { qs = new URLSearchParams(parts[1]); } catch (_) { return; }
            if (!qs.has(PARAM)) return;
            qs.delete(PARAM);
            var rest = qs.toString();
            detail.path = rest ? parts[0] + '?' + rest : parts[0];
        }
    });
})();
