/**
 * SVG chart link interceptor (modals + in-page drill-downs)
 *
 * matplotlib chart artists in this app embed `<a xlink:href="...">`
 * wrappers around legend swatches/labels and (where wired) individual bars
 * via Artist.set_url(). Two kinds of href arrive here:
 *
 *   1. A **sentinel** — never a route the browser should navigate to —
 *      in the structured form produced by charts/links.py:
 *
 *          #sam/<action>/<segment>/<segment>...
 *
 *      Segments are percent-encoded, so a username containing a slash or a
 *      space cannot break the parse. We split once and dispatch on <action>:
 *
 *        #sam/row/<attr>/<value>  → expand the table row carrying
 *            <attr>="<value>", scoped to the clicked chart's tab pane.
 *            The ATTRIBUTE TRAVELS IN THE HREF: this file no longer keeps a
 *            prefix→attribute table, so adding a drill-down chart is a
 *            zero-JavaScript change — the chart declares the attribute and
 *            that is the whole wiring.
 *        #sam/day/<YYYY-MM-DD>    → expand the day row in the Historical
 *            Usage table, auto-opening the parent month row if needed
 *            (3-level mode for >45-day spans).
 *        #sam/user/<username>     → expand that user's row in the Usage by
 *            User card.
 *
 *      `day` and `user` are separate actions rather than `row` drills
 *      because their lookups are genuinely bespoke — month-then-day nesting,
 *      and a username lookup against a client-sortable table's
 *      data-sort-value rather than a row attribute.
 *
 *   2. A **real URL** into MODAL_ROUTES (project / user quick-view), which
 *      is left as a real URL on purpose: inspectable, degrades gracefully,
 *      and needs no server-side route table here.
 *
 * Note which charts use which. The jobs activity timeline's bars are
 * `#sam/row/data-jt-period/<i>` drills, but its LEGEND deliberately uses
 * MODAL_ROUTES: `row` drills resolve within the clicked chart's tab pane,
 * and that chart sits in the Jobs pane while the By User / By Project rows
 * live in their own lazily-loaded panes — so a row drill there would
 * resolve to nothing at all. The status-dashboard stacked area resolves the
 * same problem the same way.
 *
 * Safe on pages where the target containers aren't included — each branch
 * checks for its targets and silently no-ops.
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

    // Must match charts/links.py SCHEME.
    var SCHEME = '#sam/';

    /**
     * '#sam/row/data-owner-uid/1234' → {action: 'row', args: ['data-owner-uid', '1234']}
     * Returns null for anything that isn't one of ours.
     */
    function parseSentinel(href) {
        if (href.indexOf(SCHEME) !== 0) return null;
        var parts = href.slice(SCHEME.length).split('/');
        var action = parts.shift();
        if (!action || !parts.length) return null;
        var args = [];
        for (var i = 0; i < parts.length; i++) {
            try {
                args.push(decodeURIComponent(parts[i]));
            } catch (_) {
                return null;   // malformed encoding — ignore rather than throw
            }
        }
        return {action: action, args: args};
    }

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

    // Expand a table row. The row carries the collapse target in
    // data-bs-target (same attribute the row/chevron toggles), keyed by the
    // attribute the chart named: histogram buckets (data-ah-bucket /
    // data-jh-bucket / data-jt-period, index-keyed so the JS never parses
    // band labels) and pie/legend entities (data-owner-uid, data-job-user,
    // …). Scoped to the clicked chart's tab pane so other panes' identical
    // values never cross-fire.
    function openEntityRow(attr, id, scopeEl) {
        var root = scopeEl || document;
        var row = root.querySelector('tr[' + attr + '="' + CSS.escape(id) + '"]');
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
            '#users-table td[data-sort-value="' + CSS.escape(username) + '"]');
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

        var sentinel = parseSentinel(href);
        if (sentinel) {
            var args = sentinel.args;
            switch (sentinel.action) {
            case 'row':
                if (args.length < 2 || args[0] === '' || args[1] === '') return;
                e.preventDefault();
                openEntityRow(args[0], args[1], a.closest('.tab-pane'));
                return;
            case 'day':
                if (!args[0]) return;
                e.preventDefault();
                openDayRow(args[0]);
                return;
            case 'user':
                if (!args[0]) return;
                e.preventDefault();
                openUserRow(args[0]);
                return;
            default:
                return;   // unknown action — leave the event alone
            }
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
