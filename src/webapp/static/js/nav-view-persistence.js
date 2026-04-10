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

})();
