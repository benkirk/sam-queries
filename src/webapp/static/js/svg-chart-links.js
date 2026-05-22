/**
 * SVG chart link interceptor (modals + in-page drill-downs)
 *
 * matplotlib chart artists in this app embed `<a xlink:href="...">`
 * wrappers around legend swatches/labels and (where wired) individual
 * bars via Artist.set_url(). The href is a sentinel — never a real
 * route the browser should navigate to. This delegate-listener routes
 * each sentinel to its corresponding in-page UX:
 *
 *   - Legend href starts with /user/project-details-modal/ or
 *     /admin/user/ → open the matching HTMX-driven Bootstrap modal.
 *   - Bar href starts with #day-bar-YYYY-MM-DD (Usage Trend chart on
 *     the user-dashboard resource-details page) → expand the day row
 *     in the Historical Usage table below, auto-opening the parent
 *     month row if needed (3-level mode for >45-day spans).
 *
 * Safe on pages where the target containers aren't included — each
 * branch checks for its targets and silently no-ops.
 */
(function () {
    'use strict';

    var MODAL_ROUTES = {
        '/user/project-details-modal/': {
            container: 'projectDetailsModal',
            body:      'projectDetailsModalBody',
        },
        '/admin/user/': {
            container: 'userDetailsModal',
            body:      'userDetailsModalBody',
        },
    };

    var BAR_DAY_PREFIX = '#day-bar-';

    function openDayRow(isoDate) {
        var row = document.querySelector('tr[data-date="' + isoDate + '"]');
        if (!row || !window.bootstrap) return;

        // 3-level mode: parent month <tbody> must be expanded first
        // so the day <tr> is rendered before we try to open it.
        var monthSel = row.getAttribute('data-month-target');
        if (monthSel) {
            var monthEl = document.querySelector(monthSel);
            if (monthEl) {
                // Track whether the month was already open. If a bar-click is
                // what opened it, we shouldn't persist that state across
                // reloads — the day row inside has `data-no-persist`, so
                // restoring the month alone (with an empty day) would be
                // confusing. nav-view-persistence's `show.bs.collapse` listener
                // writes to localStorage synchronously inside .show(), so we
                // can clean up immediately after.
                var monthWasOpen = monthEl.classList.contains('show');
                bootstrap.Collapse.getOrCreateInstance(monthEl, {toggle: false}).show();
                if (!monthWasOpen && monthEl.id) {
                    try { localStorage.removeItem('collapse:' + monthEl.id); } catch (_) {}
                }
            }
        }

        // Day-detail collapse sits in the next <tr> after the day
        // header. data-bs-target on the header points at it directly.
        var dayTargetSel = row.getAttribute('data-bs-target');
        if (!dayTargetSel) return;
        var dayEl = document.querySelector(dayTargetSel);
        if (!dayEl) return;
        bootstrap.Collapse.getOrCreateInstance(dayEl, {toggle: false}).show();

        // Scroll the day header into view so the user lands on the right row.
        // Defer one tick so the collapse-animation has started before we scroll.
        setTimeout(function () {
            row.scrollIntoView({behavior: 'smooth', block: 'center'});
        }, 60);
    }

    document.addEventListener('click', function (e) {
        var a = e.target.closest && e.target.closest('svg a');
        if (!a) return;
        var href = a.getAttribute('href') || a.getAttribute('xlink:href');
        if (!href) return;

        // Bar → Historical-Usage day-row drill
        if (href.indexOf(BAR_DAY_PREFIX) === 0) {
            var iso = href.slice(BAR_DAY_PREFIX.length);
            if (!iso) return;
            e.preventDefault();
            openDayRow(iso);
            return;
        }

        // Legend → HTMX-driven modal
        for (var prefix in MODAL_ROUTES) {
            if (href.indexOf(prefix) === -1) continue;
            var cfg = MODAL_ROUTES[prefix];
            var modalEl = document.getElementById(cfg.container);
            if (!modalEl || !window.htmx || !window.bootstrap) return;
            e.preventDefault();
            htmx.ajax('GET', href, {target: '#' + cfg.body, swap: 'innerHTML'});
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
            return;
        }
    }, false);
})();
