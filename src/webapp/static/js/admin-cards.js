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

    /* ── Organization tree expand/collapse (organization_card.html) ── */

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

    /* ── Sortable card tables (organization + resources cards) ──
     * Distinct from sortable_table.js: these sort on data-sort-value
     * cell attributes within multi-tbody card tables. Bound per swap —
     * the <th> nodes are fresh each time, so no double-binding. */
    function attachSorting(table) {
        table.querySelectorAll('th.sortable-header').forEach(function (th) {
            th.addEventListener('click', function () {
                var colIndex = Array.from(th.parentNode.children).indexOf(th);
                var sortType = th.dataset.sort;
                var isAsc = !th.classList.contains('sort-asc');
                table.querySelectorAll('th').forEach(function (h) {
                    h.classList.remove('sort-asc', 'sort-desc');
                });
                th.classList.add(isAsc ? 'sort-asc' : 'sort-desc');
                var tbody = table.querySelector('tbody');
                var rows = Array.from(tbody.querySelectorAll('tr'));
                rows.sort(function (a, b) {
                    var aVal = a.children[colIndex] ? a.children[colIndex].dataset.sortValue : '';
                    var bVal = b.children[colIndex] ? b.children[colIndex].dataset.sortValue : '';
                    if (sortType === 'numeric') {
                        aVal = parseFloat(aVal) || 0;
                        bVal = parseFloat(bVal) || 0;
                        return isAsc ? aVal - bVal : bVal - aVal;
                    }
                    aVal = String(aVal || '').toLowerCase();
                    bVal = String(bVal || '').toLowerCase();
                    return isAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
                });
                rows.forEach(function (r) { tbody.appendChild(r); });
            });
        });
    }

    /* ── Institutions table (institutions_table.html) ──
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
        SamCollapseChevron.attach('#institutions-pane', '.inst-type-collapse-icon');

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

    /* ── Per-swap initialization, gated on fragment markers ── */

    htmx.onLoad(function (root) {
        if (has(root, '#organizationsTabsContent')) {
            document.querySelectorAll('#organizationsTabsContent table').forEach(attachSorting);
            SamCollapseChevron.attach('#areas-pane',     '.collapse-icon');
            SamCollapseChevron.attach('#contracts-pane', '.contract-collapse-icon');
        }

        if (has(root, '.inst-type-collapse-icon') || has(root, '.inst-expand-trigger')) {
            initInstitutions(root);
        }

        if (has(root, '#resourcesTabsContent')) {
            document.querySelectorAll('#resourcesTabsContent table').forEach(attachSorting);
            SamCollapseChevron.attach('#resources-pane', '.res-type-collapse-icon');
            SamCollapseChevron.attach('#resources-pane', '.disk-root-collapse-icon');
            SamCollapseChevron.attach('#queues-pane',    '.queue-res-collapse-icon');
            SamCollapseChevron.attach('#queues-pane',    '.exemption-res-collapse-icon');
        }
    });
})();
