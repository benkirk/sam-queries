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
 *   - Bar href starts with #ah-bar-<index> (Access-history histogram on
 *     the disk resource-details page) → expand the matching bucket's
 *     per-user detail row in the table below the chart.
 *   - Pie wedge/legend href starts with #disk-ent-owner-<uid> or
 *     #disk-ent-group-<gid> (disk-scans By User / By Group tab) → expand
 *     that entity's row in the table below (found via data-owner-uid /
 *     data-group-gid), which lazy-loads its directories.
 *   - Legend href starts with #usage-user-<username> (stacked-by-user
 *     Usage Trend chart on the compute resource-details page) → expand
 *     that user's row in the Usage by User card below.
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
    var BAR_AH_PREFIX = '#ah-bar-';
    // Disk-scans entity pie (By User / By Group tab): a wedge/legend click
    // expands the matching entity's table row, found by data-owner-uid /
    // data-group-gid (see disk_scans_entities.html).
    var ENT_OWNER_PREFIX = '#disk-ent-owner-';
    var ENT_GROUP_PREFIX = '#disk-ent-group-';
    // Stacked-by-user Usage Trend chart (compute resource-details): a legend
    // username click expands that user's row in the Usage by User card.
    var USAGE_USER_PREFIX = '#usage-user-';

    // Distribution histogram (Access-history / File-size tabs) → expand the
    // matching bucket's per-user detail row. The bucket <tr> carries
    // data-ah-bucket="<index>" and a data-bs-target pointing at its collapse
    // row (see disk_scans_distribution.html). The lookup is scoped to the
    // tab pane the clicked bar lives in, so the two tabs' identical
    // #ah-bar-<index> anchors never cross-fire.
    function openBucketRow(index, scopeEl) {
        var root = scopeEl || document;
        var row = root.querySelector('tr[data-ah-bucket="' + index + '"]');
        if (!row || !window.bootstrap) return;
        var targetSel = row.getAttribute('data-bs-target');
        if (!targetSel) return;
        var el = document.querySelector(targetSel);
        if (!el) return;
        bootstrap.Collapse.getOrCreateInstance(el, {toggle: false}).show();
        setTimeout(function () {
            row.scrollIntoView({behavior: 'smooth', block: 'center'});
        }, 60);
    }

    // Pie wedge/legend → expand the entity's table row. The row carries the
    // collapse target in data-bs-target (same attribute the chevron toggles),
    // keyed by attr (data-owner-uid / data-group-gid). Scoped to the clicked
    // chart's tab pane so other panes' identical sentinels never cross-fire.
    function openEntityRow(attr, id, scopeEl) {
        var root = scopeEl || document;
        var row = root.querySelector('tr[' + attr + '="' + id + '"]');
        if (!row || !window.bootstrap) return;
        var sel = row.getAttribute('data-bs-target');
        var el = sel && document.querySelector(sel);
        if (!el) return;
        bootstrap.Collapse.getOrCreateInstance(el, {toggle: false}).show();
        setTimeout(function () {
            row.scrollIntoView({behavior: 'smooth', block: 'center'});
        }, 60);
    }

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

    // Legend username → expand that user's row in the Usage by User card.
    // The user row's first <td> carries data-sort-value="<username>"
    // (resource_details.html), scoped to #users-table. Looking up by username
    // (not the render-time uid) is robust to the table's client-side
    // re-sorting. The card body (#collapseUsers) is opened first in case the
    // analyst collapsed it. Single-triple users render inline with no
    // collapse target — we just scroll to them.
    function openUserRow(username) {
        if (!window.bootstrap) return;
        var card = document.getElementById('collapseUsers');
        if (card) {
            bootstrap.Collapse.getOrCreateInstance(card, {toggle: false}).show();
        }
        var cell = document.querySelector(
            '#users-table td[data-sort-value="' + username + '"]');
        if (!cell) return;
        var row = cell.closest('tr');
        if (!row) return;
        var sel = row.getAttribute('data-bs-target');
        if (sel) {
            var el = document.querySelector(sel);
            if (el) bootstrap.Collapse.getOrCreateInstance(el, {toggle: false}).show();
        }
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

        // Bar → distribution bucket per-user detail row (scoped to this pane)
        if (href.indexOf(BAR_AH_PREFIX) === 0) {
            var idx = href.slice(BAR_AH_PREFIX.length);
            if (idx === '') return;
            e.preventDefault();
            openBucketRow(idx, a.closest('.tab-pane'));
            return;
        }

        // Pie wedge/legend → expand the owner row
        if (href.indexOf(ENT_OWNER_PREFIX) === 0) {
            var uid = href.slice(ENT_OWNER_PREFIX.length);
            if (uid === '') return;
            e.preventDefault();
            openEntityRow('data-owner-uid', uid, a.closest('.tab-pane'));
            return;
        }

        // Pie wedge/legend → expand the group row
        if (href.indexOf(ENT_GROUP_PREFIX) === 0) {
            var gid = href.slice(ENT_GROUP_PREFIX.length);
            if (gid === '') return;
            e.preventDefault();
            openEntityRow('data-group-gid', gid, a.closest('.tab-pane'));
            return;
        }

        // Stacked-chart legend → expand the user's Usage-by-User row
        if (href.indexOf(USAGE_USER_PREFIX) === 0) {
            var uname = href.slice(USAGE_USER_PREFIX.length);
            if (uname === '') return;
            e.preventDefault();
            openUserRow(uname);
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
