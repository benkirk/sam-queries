/* Tell the server which chart layout to render.
 *
 * Charts are matplotlib SVGs rendered server-side at a fixed figure size and
 * then scaled by CSS. That works on a desktop and fails on a phone: measured
 * at 390px, the status dashboard's 18x10in figure renders at 0.224 scale,
 * putting its 9-11pt labels at roughly 2px. No stylesheet fixes that — the
 * server has to render a different figure, so it has to know the viewport.
 *
 * ## Two channels, because charts arrive two ways
 *
 * A cookie, read by every route, and an htmx parameter on every fragment
 * request. Neither alone is enough:
 *
 *   - 9 of the 18 chart call sites render inside a full-page GET — the three
 *     status history pages and the four pies on /allocations/projects. No
 *     htmx request exists for those, so `htmx:configRequest` never fires.
 *   - The cookie cannot be set before the page that sets it. CSP here is
 *     nonce-free by design (four routes cache rendered HTML in Redis, so a
 *     per-request nonce goes stale on a cache hit — see utils/csp.py), which
 *     rules out the classic inline head script. This file is external, so it
 *     runs at end of body, i.e. after the server already chose a layout.
 *
 * Together they cover both: fragments are correct on the very first paint
 * because `hx-trigger="load"` fires after this listener registers, and full
 * pages are correct from the second navigation onward. A first-ever visit on
 * a phone or tablet sees desktop-sized charts on one page. They still fit —
 * nothing overflows at 390px — they are merely small, which is the status quo.
 *
 * PR 3 (app-wide dark mode) wants the same carrier for `theme`, with the
 * added constraint that a theme flash is visible where a chart size is not.
 * Keep the cookie shape reusable.
 *
 * ## No re-render on resize
 *
 * There is deliberately no `matchMedia` change listener. Rotating a phone
 * keeps the charts already on screen until the next fetch or navigation.
 * A viewport-driven re-fetch would be a new pathway — the only precedent in
 * this app, the job-history period pills, is click-driven — and a drag-resize
 * across the breakpoint would fire a burst of chart renders, each of which is
 * a matplotlib figure.
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
