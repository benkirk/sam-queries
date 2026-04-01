/**
 * Tab State Persistence
 *
 * Saves and restores Bootstrap 5 tab selections using localStorage.
 * Handles both Variant A (href="#pane") and Variant B (data-bs-target="#pane").
 * Works across page refreshes and after HTMX swaps / modal close.
 *
 * Storage key format: "tab:<tablistId>"  →  value: "#pane-id"
 */
(function () {
    'use strict';

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

})();
