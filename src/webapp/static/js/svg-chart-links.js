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
 *   - Bar href starts with #jh-bar-<index> (job-history histograms) →
 *     open the Bucket-counts <details> and expand that band's row,
 *     which lazy-loads the band's per-job table.
 *   - Bar href starts with #jt-bar-<index> (job-history activity timeline
 *     on the Jobs tab) → expand that period's row in the Period breakdown
 *     table, which lazy-loads its per-owner tier. Its LEGEND reuses the
 *     #job-user- / #job-proj- prefixes below, so a name click lands on the
 *     By User / By Project pane via activateOwningTab().
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
    // Stacked-by-user Usage Trend chart (compute resource-details): a legend
    // username click expands that user's row in the Usage by User card.
    var USAGE_USER_PREFIX = '#usage-user-';

    // Sentinel prefix → the row attribute that identifies the row to expand.
    // Every entry behaves identically (slice the id off the href, expand the
    // matching row within the clicked chart's tab pane), so they are a table
    // rather than six copies of the same branch. The two above are NOT here:
    // they have bespoke openers (month-then-day nesting; username lookup in a
    // client-sortable table).
    //
    //   ah-bar / jh-bucket — histogram bands, INDEX-keyed, so the JS never
    //     parses band labels and a trimmed axis stays consistent with the
    //     table (see _trim_empty_edge_bands).
    //   the rest — pie wedge + legend entities.
    var ROW_SENTINELS = {
        // fs-scans distribution histogram (Access history / File sizes)
        '#ah-bar-':           'data-ah-bucket',
        // fs-scans entity pie (By User / By Group) — disk_scans_entities.html
        '#disk-ent-owner-':   'data-owner-uid',
        '#disk-ent-group-':   'data-group-gid',
        // job-history histogram (Wait Times / Job Sizes / Durations)
        '#jh-bar-':           'data-jh-bucket',
        // job-history activity timeline (Jobs tab) — index-keyed like the
        // histograms, so the JS never parses day/week/month band labels.
        '#jt-bar-':           'data-jt-period',
        // job-history usage pies — jobs_by_user.html / jobs_by_project.html
        '#job-user-':         'data-job-user',
        '#job-proj-':         'data-job-project',
    };

    // Chart sentinel → expand a table row. The row carries the collapse
    // target in data-bs-target (same attribute the row/chevron toggles),
    // keyed by attr: histogram buckets (data-ah-bucket / data-jh-bucket,
    // index-keyed) and pie/legend entities (data-owner-uid, data-job-user,
    // …). Scoped to the clicked chart's tab pane so other panes' identical
    // sentinels never cross-fire.
    // A chart link may target a row in a DIFFERENT tab pane: the stacked
    // Usage Trend chart lives in the resource-details History pane, but its
    // legend usernames address rows in the By User pane. Opening a collapse
    // inside a hidden pane would "work" invisibly, so activate the owning
    // pane first. No-op for same-pane links and for charts outside any tabs.
    function activateOwningTab(el) {
        if (!window.bootstrap || !el.closest) return;
        var pane = el.closest('.tab-pane');
        if (!pane || !pane.id || pane.classList.contains('active')) return;
        var trigger = document.querySelector(
            '[data-bs-toggle="tab"][data-bs-target="#' + pane.id + '"]');
        if (trigger) {
            try { bootstrap.Tab.getOrCreateInstance(trigger).show(); } catch (_) {}
        }
    }

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
        activateOwningTab(row);

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

    // Username → expand that user's row in the Usage by User table. Two
    // charts address it: the By User pie (same pane) and the stacked Usage
    // Trend legend (History pane), hence activateOwningTab. The user row's
    // first <td> carries data-sort-value="<username>"
    // (resource_details.html), scoped to #users-table. Looking up by username
    // (not the render-time uid) is robust to the table's client-side
    // re-sorting. Single-triple users render inline with no collapse target
    // — we just scroll to them.
    function openUserRow(username) {
        if (!window.bootstrap) return;
        var cell = document.querySelector(
            '#users-table td[data-sort-value="' + username + '"]');
        if (!cell) return;
        var row = cell.closest('tr');
        if (!row) return;
        activateOwningTab(row);
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

        // Histogram bar or pie wedge/legend → expand the matching table row,
        // scoped to the clicked chart's tab pane.
        for (var sentinel in ROW_SENTINELS) {
            if (href.indexOf(sentinel) !== 0) continue;
            var rowId = href.slice(sentinel.length);
            if (rowId === '') return;
            e.preventDefault();
            openEntityRow(ROW_SENTINELS[sentinel], rowId, a.closest('.tab-pane'));
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
