/**
 * Navigation & View State Persistence
 *
 * Covers UI state stored client-side:
 *
 *  1. Tab selections  — key: "tab:<tablistId>"    value: "#pane-id"
 *  2. Collapse state  — key: "collapse:<id>"      value: "1"
 *  3. Chart selectors — key: "chart:<id>"         value: JSON params
 *  4. Scroll position — key: "nav:scroll:<path>"  (sessionStorage, one-shot)
 *
 * Tab / collapse handling works for both initial page load and after
 * HTMX swaps or Bootstrap modal close events.
 */
(function () {
    'use strict';

    // ── Tab persistence ──────────────────────────────────────────────────────

    var STORAGE_PREFIX = 'tab:';

    /** Return the pane selector ("#id") for a tab trigger element. */
    function getPaneSelector(trigger) {
        return trigger.dataset.bsTarget || trigger.getAttribute('href') || null;
    }

    /** Persist the active tab for a tablist whenever a tab is shown. */
    function saveActiveTab(event) {
        var trigger = event.target;
        var tablist = trigger.closest('[role="tablist"]');
        if (!tablist || !tablist.id) return;
        var paneSelector = getPaneSelector(trigger);
        if (paneSelector && paneSelector.startsWith('#')) {
            try {
                localStorage.setItem(STORAGE_PREFIX + tablist.id, paneSelector);
            } catch (_) {}  // private browsing / storage full — fail silently
        }
    }

    /** Restore saved tab selections within a root element (document or swapped fragment). */
    function restoreTabs(root) {
        root.querySelectorAll('[role="tablist"][id]').forEach(function (tablist) {
            var saved = null;
            try { saved = localStorage.getItem(STORAGE_PREFIX + tablist.id); } catch (_) {}
            if (!saved) return;

            var trigger = tablist.querySelector(
                '[data-bs-target="' + saved + '"], [href="' + saved + '"]'
            );
            if (!trigger) return;  // saved tab no longer in DOM — leave default active

            if (!trigger.classList.contains('active')) {
                try { new bootstrap.Tab(trigger).show(); } catch (_) {}
            }
        });
    }

    // 1. On page load: restore all tab groups in initial DOM
    document.addEventListener('DOMContentLoaded', function () {
        restoreTabs(document);
    });

    // 2. Save tab selections as they change
    document.addEventListener('shown.bs.tab', saveActiveTab);

    // 3. After HTMX swaps: restore tabs in the newly-settled fragment
    document.addEventListener('htmx:afterSettle', function (event) {
        restoreTabs(event.detail.elt);
    });

    // 4. After modal close: restore tabs reset by Bootstrap
    document.addEventListener('hidden.bs.modal', function (event) {
        restoreTabs(event.target);
    });

    // ── Collapse (expanded row) persistence ──────────────────────────────────

    var COLLAPSE_PREFIX = 'collapse:';

    // Collapses with `data-no-persist` opt out of restore-on-reload — for
    // drawers whose contents are expensive to fetch (e.g. the jobs plugin
    // drill-down), where re-firing the underlying hx-trigger on every
    // page load is the wrong default. Honored by all three handlers so
    // existing localStorage entries stay dormant and no new ones are
    // written.

    /** Save expanded state when a collapse opens. */
    function saveCollapse(event) {
        var el = event.target;
        if (!el.id || el.hasAttribute('data-no-persist')) return;
        try { localStorage.setItem(COLLAPSE_PREFIX + el.id, '1'); } catch (_) {}
    }

    /** Clear saved state when a collapse closes. */
    function clearCollapse(event) {
        var el = event.target;
        if (!el.id || el.hasAttribute('data-no-persist')) return;
        try { localStorage.removeItem(COLLAPSE_PREFIX + el.id); } catch (_) {}
    }

    /** Restore saved collapse state within a root element. */
    function restoreCollapses(root) {
        root.querySelectorAll('.collapse[id]').forEach(function (el) {
            if (el.hasAttribute('data-no-persist')) return;
            var saved = null;
            try { saved = localStorage.getItem(COLLAPSE_PREFIX + el.id); } catch (_) {}
            if (!saved) return;
            if (!el.classList.contains('show')) {
                try { new bootstrap.Collapse(el, { toggle: false }).show(); } catch (_) {}
            }
        });
    }

    // 5. On page load: restore all collapse state in initial DOM
    document.addEventListener('DOMContentLoaded', function () {
        restoreCollapses(document);
    });

    // 6. Save / clear as collapses open and close
    document.addEventListener('show.bs.collapse',  saveCollapse);
    document.addEventListener('hide.bs.collapse',  clearCollapse);

    // 7. After HTMX swap: restore within the newly-settled fragment
    document.addEventListener('htmx:afterSettle', function (event) {
        restoreCollapses(event.detail.elt);
    });

    // 8. After modal close: restore within the modal
    document.addEventListener('hidden.bs.modal', function (event) {
        restoreCollapses(event.target);
    });

    // (The referrer-based `.back-link` machinery that used to live here was
    // removed when detail pages moved to server-derived breadcrumbs —
    // see dashboards/fragments/breadcrumbs.html and webapp/utils/nav.py.)

    // ── Chart selector persistence ───────────────────────────────────────────
    //
    // Chart fragments with multiple HTMX btn-group selectors (group_by,
    // state, metric, rank_by, …) lose their selection on a full page
    // reload — the loader's hx-get defaults run again. We persist the
    // live URL params to localStorage, keyed by the chart's stable dom
    // id, and replay them on the next configRequest.
    //
    // Which params to persist is declared by the template via
    // `data-chart-persist-keys` (whitespace-separated). Co-locating the
    // key list with the buttons that own it avoids JS-side hardcoding,
    // so adding a new selector is a one-file change. Server-side
    // validation in the route is the safety net for stale combinations.

    var CHART_PREFIX = 'chart:';

    function chartPersistKeys(elt) {
        var raw = elt.dataset.chartPersistKeys;
        if (!raw) return [];
        return raw.split(/\s+/).filter(Boolean);
    }

    /** Read saved selections and override hx-get parameters. Only applies to
     *  the *loader* element itself — i.e. the wrapper div with hx-trigger="load"
     *  that carries `data-chart-persist-id` directly. Selector buttons inside
     *  the chart fragment have an ancestor with the marker, but `elt.matches`
     *  ensures we don't clobber the user's deliberate click. */
    document.addEventListener('htmx:configRequest', function (event) {
        var elt = event.detail.elt;
        if (!elt || !elt.matches || !elt.matches('[data-chart-persist-id]')) return;

        var keys = chartPersistKeys(elt);
        if (keys.length === 0) return;

        var id = elt.dataset.chartPersistId;
        var raw = null;
        try { raw = localStorage.getItem(CHART_PREFIX + id); } catch (_) { return; }
        if (!raw) return;

        var saved;
        try { saved = JSON.parse(raw); } catch (_) { return; }
        if (!saved || typeof saved !== 'object') return;

        keys.forEach(function (k) {
            if (k in saved) event.detail.parameters[k] = saved[k];
        });

        // htmx appends `parameters` to `path` for GET requests, so any keys
        // already encoded in the loader's hx-get URL would produce duplicate
        // query keys (?metric=jobs&metric=cores). Werkzeug's request.args.get
        // returns the FIRST value, which silently defeats the override. Strip
        // the persisted keys from `path` so our parameters become authoritative.
        var path = event.detail.path;
        if (path && path.indexOf('?') !== -1) {
            var parts = path.split('?');
            var qs;
            try { qs = new URLSearchParams(parts[1]); } catch (_) { return; }
            keys.forEach(function (k) { qs.delete(k); });
            var rest = qs.toString();
            event.detail.path = rest ? parts[0] + '?' + rest : parts[0];
        }
    });

    /** After a chart fragment swaps in, capture the resolved request URL —
     *  this is the source of truth for which selector combination is now
     *  showing. Avoids reading button DOM classes. */
    document.addEventListener('htmx:afterSettle', function (event) {
        var elt = event.detail.elt;
        if (!elt) return;

        var chartEl = (elt.matches && elt.matches('[data-chart-persist-id]'))
            ? elt
            : (elt.querySelector ? elt.querySelector('[data-chart-persist-id]') : null);
        if (!chartEl) return;

        var keys = chartPersistKeys(chartEl);
        if (keys.length === 0) return;

        var url = event.detail.xhr && event.detail.xhr.responseURL;
        if (!url) return;

        var qs;
        try { qs = new URL(url, window.location.origin).searchParams; } catch (_) { return; }

        var saved = {};
        keys.forEach(function (k) {
            if (qs.has(k)) saved[k] = qs.get(k);
        });
        if (Object.keys(saved).length === 0) return;

        try {
            localStorage.setItem(CHART_PREFIX + chartEl.dataset.chartPersistId,
                                 JSON.stringify(saved));
        } catch (_) {}
    });

    // ── Scroll preservation across full-page navigation ──────────────────────
    //
    // Time-filter clicks (?hours=N) trigger `window.location.href = ...` or
    // a plain link, both of which reload the page and reset scroll to the
    // top — hiding the chart the user was just inspecting. We snapshot
    // scrollY on click (capture phase, before the inline onclick fires) and
    // restore it on the next DOMContentLoaded for the same pathname.

    var SCROLL_PREFIX = 'nav:scroll:';
    var SCROLL_TTL_MS = 5000;

    document.addEventListener('click', function (e) {
        var trigger = e.target.closest && e.target.closest('[data-scroll-preserve]');
        if (!trigger) return;
        try {
            sessionStorage.setItem(
                SCROLL_PREFIX + window.location.pathname,
                JSON.stringify({ y: window.scrollY, ts: Date.now() })
            );
        } catch (_) {}
    }, true);  // capture phase, runs before inline onclick handlers

    document.addEventListener('DOMContentLoaded', function () {
        var key = SCROLL_PREFIX + window.location.pathname;
        var raw = null;
        try {
            raw = sessionStorage.getItem(key);
            sessionStorage.removeItem(key);  // clear immediately — one-shot
        } catch (_) { return; }
        if (!raw) return;

        var saved;
        try { saved = JSON.parse(raw); } catch (_) { return; }
        if (!saved || typeof saved.y !== 'number') return;
        if (Date.now() - saved.ts > SCROLL_TTL_MS) return;

        window.scrollTo({ top: saved.y, behavior: 'instant' });
    });

})();
