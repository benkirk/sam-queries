/* Admin card fragment behaviors (organization card, institutions table,
 * resources card), extracted from the fragments' inline <script> blocks
 * (CSP: script-src 'self').
 *
 * These fragments are lazy-loaded and re-swapped by htmx — the
 * organization card and institutions table are additionally cached
 * per-user in Redis — so all initialization runs under htmx.onLoad,
 * scoped to the swapped subtree and gated on fragment marker elements.
 */
(function () {
    'use strict';

    function has(root, selector) {
        return (root.matches && root.matches(selector)) || root.querySelector(selector);
    }

    /* Organization tree expand/collapse (organization_card.html) */

    function collapseOrgDescendants(parentOrgId) {
        document.querySelectorAll('#orgs-tbody tr[data-parent-id="' + parentOrgId + '"]')
            .forEach(function (row) {
                row.classList.add('org-tree-collapsed');
                row.style.display = 'none';
                var chevron = row.querySelector('.org-tree-chevron');
                if (chevron) { chevron.style.transform = ''; }
                collapseOrgDescendants(row.dataset.orgId);
            });
    }

    registerAction('org-toggle-children', function (iconEl, event) {
        event.stopPropagation();
        var orgId = iconEl.dataset.orgId;
        var isExpanding = iconEl.style.transform !== 'rotate(90deg)';
        iconEl.style.transform = isExpanding ? 'rotate(90deg)' : '';
        document.querySelectorAll('#orgs-tbody tr[data-parent-id="' + orgId + '"]')
            .forEach(function (row) {
                if (isExpanding) {
                    row.classList.remove('org-tree-collapsed');
                    row.style.display = '';
                } else {
                    row.classList.add('org-tree-collapsed');
                    row.style.display = 'none';
                    collapseOrgDescendants(row.dataset.orgId);
                    var childChevron = row.querySelector('.org-tree-chevron');
                    if (childChevron) { childChevron.style.transform = ''; }
                }
            });
    });

    /* Institutions table (institutions_table.html)
     * Institution expand rows use a plain JS display toggle (not Bootstrap
     * collapse, since they're <tr>s nested inside the type tbody so they
     * hide/show naturally with the type). Persist their state under the same
     * `collapse:<id>` localStorage key used by nav-view-persistence.js, so
     * HTMX re-renders (filter/toggle changes) don't lose user expansions. */
    var COLLAPSE_PREFIX = 'collapse:';

    function setExpanded(row, userRow, expanded) {
        userRow.style.display = expanded ? '' : 'none';
        var icon = row.querySelector('.inst-expand-icon');
        if (icon) { icon.style.transform = expanded ? 'rotate(90deg)' : ''; }
    }

    function restoreRow(row) {
        var targetId = row.dataset.instTarget;
        var userRow = document.getElementById(targetId);
        if (!userRow) { return; }
        var saved = null;
        try { saved = localStorage.getItem(COLLAPSE_PREFIX + targetId); } catch (_) {}
        if (saved) { setExpanded(row, userRow, true); }
    }

    function initInstitutions(root) {
        root.querySelectorAll('.inst-expand-trigger').forEach(function (row) {
            var targetId = row.dataset.instTarget;
            var userRow = document.getElementById(targetId);
            if (!userRow) { return; }

            restoreRow(row);

            row.addEventListener('click', function () {
                var willExpand = userRow.style.display === 'none';
                setExpanded(row, userRow, willExpand);
                try {
                    if (willExpand) { localStorage.setItem(COLLAPSE_PREFIX + targetId, '1'); }
                    else            { localStorage.removeItem(COLLAPSE_PREFIX + targetId); }
                } catch (_) {}
            });
        });

        /* After a Search submit, nav-view-persistence.js calls
         * bootstrap.Collapse.show() on the parent tbody to restore
         * InstitutionType expansion. Its ~350ms transition's completion
         * callback clobbers child <tr style="display:"> back to none.
         * Re-restore after the transition completes (shown.bs.collapse
         * fires exactly then) so we win the race. Guard against
         * double-binding: this init runs on every HTMX swap, so flag the
         * institutions-pane container (which survives swaps). */
        var pane = document.getElementById('institutions-pane');
        if (pane && !pane.dataset.instCollapseBound) {
            pane.dataset.instCollapseBound = '1';
            pane.addEventListener('shown.bs.collapse', function (e) {
                var tbody = e.target;
                if (!tbody || !tbody.id || tbody.id.indexOf('inst-type-') !== 0) { return; }
                tbody.querySelectorAll('.inst-expand-trigger').forEach(restoreRow);
            });
        }
    }

    /* Per-swap initialization, gated on fragment markers */

    /* Queue cleanup preview (queue_cleanup_preview_htmx.html)
     *
     * Keeps the submit button's count in sync with the ticked checkboxes and
     * disables it at zero, plus the select all/none shortcuts. Bound per swap:
     * the preview fragment is replaced wholesale on every step-1 submit. */

    function initQueueCleanup(form) {
        if (form.samCleanupBound) { return; }
        form.samCleanupBound = true;

        var boxes = form.querySelectorAll('input[name="queue_ids"]');
        var count = form.querySelector('[data-cleanup-count]');
        var submit = form.querySelector('#queueCleanupSubmit');
        if (!count || !submit) { return; }

        function sync() {
            var n = form.querySelectorAll('input[name="queue_ids"]:checked').length;
            count.textContent = n;
            submit.disabled = (n === 0);
        }

        boxes.forEach(function (b) { b.addEventListener('change', sync); });
        form.querySelectorAll('[data-cleanup-select]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var on = btn.dataset.cleanupSelect === 'all';
                boxes.forEach(function (b) { b.checked = on; });
                sync();
            });
        });
        sync();
    }

    htmx.onLoad(function (root) {
        var cleanupForm = (root.matches && root.matches('#queueCleanupForm'))
            ? root : root.querySelector('#queueCleanupForm');
        if (cleanupForm) { initQueueCleanup(cleanupForm); }

        if (has(root, '#institutions-table')) {
            initInstitutions(root);
        }
    });
})();
