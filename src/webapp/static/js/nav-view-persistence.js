/**
 * Navigation & View State Persistence
 *
 * Covers three areas of UI state, all stored in localStorage:
 *
 *  1. Tab selections  — key: "tab:<tablistId>"    value: "#pane-id"
 *  2. Collapse state  — key: "collapse:<id>"      value: "1"
 *  3. Back-navigation — key: "nav:back"           value: URL string
 *
 * Tab / collapse handling works for both initial page load and after
 * HTMX swaps or Bootstrap modal close events.
 *
 * Back-navigation: detail pages that include a `.back-link` element
 * have their back-button href (and label) updated dynamically based on
 * where the user came from, with localStorage as a refresh-resilient
 * fallback when `document.referrer` is unavailable.
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

    /** Save expanded state when a collapse opens. */
    function saveCollapse(event) {
        var id = event.target.id;
        if (!id) return;
        try { localStorage.setItem(COLLAPSE_PREFIX + id, '1'); } catch (_) {}
    }

    /** Clear saved state when a collapse closes. */
    function clearCollapse(event) {
        var id = event.target.id;
        if (!id) return;
        try { localStorage.removeItem(COLLAPSE_PREFIX + id); } catch (_) {}
    }

    /** Restore saved collapse state within a root element. */
    function restoreCollapses(root) {
        root.querySelectorAll('.collapse[id]').forEach(function (el) {
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

    // ── Back-link navigation ─────────────────────────────────────────────────

    var NAV_BACK_KEY = 'nav:back';

    /**
     * Map URL path prefixes (longest-first) to human-readable labels for the
     * back button.  Add entries here as new source pages are introduced.
     */
    var NAV_LABELS = [
        ['/user/allocations', 'Allocations'],
        ['/user/',            'Dashboard'],
        ['/status/',          'Status Dashboard'],
        ['/search',           'Search Results'],
        ['/admin/',           'Admin'],
    ];

    function navLabel(url) {
        try {
            var path = new URL(url).pathname;
            for (var i = 0; i < NAV_LABELS.length; i++) {
                if (path.startsWith(NAV_LABELS[i][0])) return NAV_LABELS[i][1];
            }
        } catch (_) {}
        return null;
    }

    function isSameOrigin(url) {
        try { return new URL(url).origin === window.location.origin; } catch (_) { return false; }
    }

    /** Update every `.back-link a` on the page with the resolved back URL. */
    function updateBackLinks(backUrl) {
        document.querySelectorAll('.back-link a').forEach(function (link) {
            link.href = backUrl;
            var label = navLabel(backUrl);
            if (label) {
                var textNode = link.querySelector('.back-link-text');
                if (textNode) textNode.textContent = 'Back to ' + label;
            }
        });
    }

    // 9. On page load: resolve back URL and update any .back-link elements
    document.addEventListener('DOMContentLoaded', function () {
        if (!document.querySelector('.back-link')) return;  // only detail pages

        var referrer = document.referrer;
        var current  = window.location.href;
        var backUrl  = null;

        if (referrer && isSameOrigin(referrer) && referrer !== current) {
            // Fresh navigation: live referrer is valid — save it for refresh resilience
            backUrl = referrer;
            try { localStorage.setItem(NAV_BACK_KEY, backUrl); } catch (_) {}
        } else {
            // Refresh or direct URL: fall back to whatever we saved last time
            try { backUrl = localStorage.getItem(NAV_BACK_KEY); } catch (_) {}
        }

        if (backUrl) updateBackLinks(backUrl);
    });

    // 10. Clear stored back URL when the user actually clicks the back link
    //     so a subsequent re-visit uses a fresh referrer rather than stale state
    document.addEventListener('click', function (e) {
        if (e.target.closest('.back-link a')) {
            try { localStorage.removeItem(NAV_BACK_KEY); } catch (_) {}
        }
    });

    // ── Chart selector persistence ───────────────────────────────────────────
    //
    // The "User / project load over time" status chart has three button-group
    // selectors (group_by, state, metric) inside the swap target. Each click
    // is an HTMX GET that re-renders the chart fragment; a full page reload,
    // however, re-runs the loader's hardcoded defaults and the user's
    // selections vanish. We persist the live URL params to localStorage,
    // keyed by the chart's stable server-computed dom id, and replay them on
    // the next configRequest.

    var CHART_PREFIX = 'chart:';
    var CHART_KEYS = ['group_by', 'state', 'metric'];

    /** Defensive clamp so a stale saved value can't ask for invalid combos. */
    function clampChartParams(params) {
        if (params.metric === 'nodes' && params.state && params.state !== 'running') {
            params.metric = 'cores';
        }
    }

    /** Read saved selections and override hx-get parameters. Runs for every
     *  request whose source element (or an ancestor) carries
     *  `data-chart-persist-id`, including the loader's hx-trigger="load". */
    document.addEventListener('htmx:configRequest', function (event) {
        var elt = event.detail.elt;
        var marker = elt && elt.closest ? elt.closest('[data-chart-persist-id]') : null;
        if (!marker) return;

        var id = marker.dataset.chartPersistId;
        var raw = null;
        try { raw = localStorage.getItem(CHART_PREFIX + id); } catch (_) { return; }
        if (!raw) return;

        var saved;
        try { saved = JSON.parse(raw); } catch (_) { return; }
        if (!saved || typeof saved !== 'object') return;

        // Merge into a working object so we can clamp before assigning.
        var merged = {};
        CHART_KEYS.forEach(function (k) {
            merged[k] = (k in saved) ? saved[k] : event.detail.parameters[k];
        });
        clampChartParams(merged);

        CHART_KEYS.forEach(function (k) {
            if (merged[k] !== undefined) event.detail.parameters[k] = merged[k];
        });
    });

    /** After a chart fragment swaps in, capture the resolved request URL —
     *  this is the source of truth for which {group_by, state, metric}
     *  combination is now showing. Avoids reading button DOM classes. */
    document.addEventListener('htmx:afterSettle', function (event) {
        var elt = event.detail.elt;
        if (!elt) return;

        var chartEl = (elt.matches && elt.matches('[data-chart-persist-id]'))
            ? elt
            : (elt.querySelector ? elt.querySelector('[data-chart-persist-id]') : null);
        if (!chartEl) return;

        var url = event.detail.xhr && event.detail.xhr.responseURL;
        if (!url) return;

        var qs;
        try { qs = new URL(url, window.location.origin).searchParams; } catch (_) { return; }

        var saved = {};
        CHART_KEYS.forEach(function (k) {
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
