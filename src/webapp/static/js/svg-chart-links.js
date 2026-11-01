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
 *        #sam/row/<attr>/<value>  -> expand the table row carrying
 *            <attr>="<value>", scoped to the clicked chart's nearest
 *            [data-drill-scope], else its tab pane.
 *            The ATTRIBUTE TRAVELS IN THE HREF: this file no longer keeps a
 *            prefix->attribute table, so adding a drill-down chart is a
 *            zero-JavaScript change — the chart declares the attribute and
 *            that is the whole wiring.
 *        #sam/day/<YYYY-MM-DD>    -> expand the day row in the Historical
 *            Usage table, auto-opening the parent month row if needed
 *            (3-level mode for >45-day spans).
 *        #sam/user/<username>     -> expand that user's row in the Usage by
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

    var QUICK_VIEWS = Object.keys(MODAL_ROUTES).map(function (k) { return MODAL_ROUTES[k].container; });

    // Must match charts/links.py SCHEME.
    var SCHEME = '#sam/';

    /**
     * '#sam/row/data-owner-uid/1234' -> {action: 'row', args: ['data-owner-uid', '1234']}
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

    // Username -> expand that user's row in the Usage by User table. Two
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

    /**
     * Expand modal (#chartExpandModal): a click on a [data-chart-expand] chart off
     * any drill link clicks the host's opener, unless the opener is hidden (phones).
     */
    function expandFromChart(target) {
        var svg = target.closest && target.closest('[data-chart-expand] svg');
        if (!svg) return;
        var opener = svg.closest('[data-chart-expand]')
                        .querySelector('[data-chart-expand-opener]');
        if (opener && opener.offsetParent !== null) opener.click();
    }

    // The open chart survives a quick-view and a page visit. A quick-view opened from
    // it shows it again on close, unless the visitor moved on to another dialog, and
    // ?expand=<opener key> in the address bar reopens it on load (so Back lands on it).
    var EXPAND_PARAM = 'expand';
    var handoff = null;   // 'pending' while the chart hides, 'open' once the quick-view shows

    function expandKey(opener) {
        var s = opener.getAttribute('hx-get') || '', h = 5381;
        for (var i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
        return h.toString(36);
    }

    function setExpandParam(key) {
        try {
            var url = new URL(window.location.href);
            if (key) url.searchParams.set(EXPAND_PARAM, key);
            else url.searchParams.delete(EXPAND_PARAM);
            if (url.href !== window.location.href) history.replaceState(history.state, '', url.href);
        } catch (_) {}
    }

    // Its opener may arrive in a slow lazy panel (job history), so retry on each settle
    // for a while; the visitor's first click or key cancels it, so it never jumps in late.
    var REOPEN_MS = 30000;
    var pendingKey = null;
    var pendingUntil = 0;

    function cancelPending() {
        if (pendingKey) { pendingKey = null; setExpandParam(null); }
    }

    function reopenPending() {
        if (!pendingKey) return;
        if (Date.now() > pendingUntil) { cancelPending(); return; }
        var openers = document.querySelectorAll('[data-chart-expand-opener]');
        for (var i = 0; i < openers.length; i++) {
            var opener = openers[i];
            if (expandKey(opener) === pendingKey && opener.offsetParent !== null) {
                pendingKey = null;
                bootstrap.Modal.getOrCreateInstance(document.getElementById('chartExpandModal')).show(opener);
                htmx.ajax('GET', opener.getAttribute('hx-get'),
                          {source: opener, target: '#chartExpandModalBody', swap: 'innerHTML'});
                return;
            }
        }
    }

    function startReopen(now) {
        var key;
        try { key = new URL(window.location.href).searchParams.get(EXPAND_PARAM); } catch (_) { return; }
        if (!key || document.querySelector('.modal.show')) return;
        pendingKey = key;
        pendingUntil = Date.now() + REOPEN_MS;
        if (now) reopenPending();
        setTimeout(reopenPending, REOPEN_MS + 500);
    }

    // Fetch with htmx.ajax, not opener.click(): a just-swapped opener may not be bound
    // yet, and its click opened an empty dialog (Back to job history did exactly that).
    document.addEventListener('DOMContentLoaded', function () { startReopen(false); });
    window.addEventListener('pageshow', function (e) { if (e.persisted) startReopen(true); });
    document.addEventListener('htmx:load', reopenPending);
    ['pointerdown', 'keydown'].forEach(function (t) {
        document.addEventListener(t, function (e) { if (e.isTrusted) cancelPending(); }, true);
    });

    // Reopening shows the spinner, never the previous chart while the next loads.
    var expandLoading = null;
    function resetExpand() {
        setExpandParam(null);
        if (!expandLoading) return;
        document.getElementById('chartExpandModalBody').innerHTML = expandLoading.body;
        document.getElementById('chartExpandModalTitle').textContent = expandLoading.title;
    }

    document.addEventListener('shown.bs.modal', function (e) {
        if (handoff === 'pending' && QUICK_VIEWS.indexOf(e.target.id) !== -1) handoff = 'open';
    });
    document.addEventListener('show.bs.modal', function (e) {
        if (e.target.id !== 'chartExpandModal') {
            if (handoff === 'open') { handoff = null; resetExpand(); }   // moved on: no return
            return;
        }
        var opener = e.relatedTarget;
        if (opener && opener.hasAttribute && opener.hasAttribute('data-chart-expand-opener')) {
            setExpandParam(expandKey(opener));
        }
        if (expandLoading) return;
        var body = document.getElementById('chartExpandModalBody');
        expandLoading = {body: body.innerHTML,
                         title: document.getElementById('chartExpandModalTitle').textContent};
    });
    document.addEventListener('hidden.bs.modal', function (e) {
        if (e.target.id !== 'chartExpandModal') {
            if (handoff === 'open' && QUICK_VIEWS.indexOf(e.target.id) !== -1) {
                handoff = null;
                bootstrap.Modal.getOrCreateInstance(document.getElementById('chartExpandModal')).show();
            }
            return;
        }
        if (!handoff) resetExpand();   // handed to a quick-view: keep the chart and the marker
    });

    document.addEventListener('click', function (e) {
        var a = e.target.closest && e.target.closest('svg a');
        if (!a) {
            expandFromChart(e.target);
            return;
        }
        var href = a.getAttribute('href') || a.getAttribute('xlink:href');
        if (!href) return;

        var sentinel = parseSentinel(href);
        if (sentinel) {
            var args = sentinel.args;
            switch (sentinel.action) {
            case 'row':
                if (args.length < 2 || args[0] === '' || args[1] === '') return;
                e.preventDefault();
                openEntityRow(args[0], args[1],
                              a.closest('[data-drill-scope]') || a.closest('.tab-pane'));
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

        // Legend -> HTMX-driven modal
        for (var prefix in MODAL_ROUTES) {
            if (href.indexOf(prefix) === -1) continue;
            var cfg = MODAL_ROUTES[prefix];
            var modalEl = document.getElementById(cfg.container);
            if (!modalEl || !window.htmx || !window.bootstrap) return;
            e.preventDefault();
            var open = function () {
                htmx.ajax('GET', href, {target: '#' + cfg.body, swap: 'innerHTML'});
                bootstrap.Modal.getOrCreateInstance(modalEl).show();
            };
            // Bootstrap does not stack modals: close the expand modal first.
            var expand = a.closest('#chartExpandModal');
            if (expand) {
                handoff = 'pending';
                expand.addEventListener('hidden.bs.modal', open, {once: true});
                bootstrap.Modal.getOrCreateInstance(expand).hide();
            } else {
                open();
            }
            return;
        }
    }, false);
})();
