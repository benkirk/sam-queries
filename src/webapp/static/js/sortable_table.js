/* Client-side table sorting.
 *
 * Single-tbody mode (default):
 *   <table>
 *     <thead>
 *       <th class="sortable-header" data-sort="text|numeric|date">Label</th>
 *       <th class="sortable-header sort-desc" data-sort="numeric">Default-sorted column</th>
 *       <th class="sortable-header text-center" data-sort="numeric"
 *           data-sort-attr="sort-usage">Spans-via-attr</th>
 *     </thead>
 *     <tbody>
 *       <tr>
 *         <td data-sort-value="alice">…</td>
 *         <td colspan="6" data-sort-usage="42">…</td>
 *       </tr>
 *     </tbody>
 *   </table>
 *
 * Multi-tbody mode (opt-in via ``tbody.sortable-group``):
 *   The unit of reordering is each ``<tbody class="sortable-group">``,
 *   not its child rows. Sort key is extracted from each group's FIRST
 *   ``<tr>`` (using the same cell-level data-sort-value rules above).
 *   Lets a table keep a non-sortable Total tbody up top, then drag
 *   each user's row + adjacent lazy-subtree placeholder around as a
 *   single block.
 *
 * Initial display order comes from the server. Adding `sort-desc` /
 * `sort-asc` to a header just renders the arrow indicator; nothing is
 * re-sorted until a user clicks. Click toggles asc/desc; data-sort-attr
 * lets a single colspan cell carry sort keys for several columns.
 *
 * Secondary sort handle (two sort keys in ONE column):
 *   <th class="sortable-header" data-sort="text">
 *     <span class="sort-handle" data-sort-attr="sort-first"
 *           title="Sort by first name"></span>Name</th>
 *   ...
 *   <td data-sort-value="last, first" data-sort-first="first last">…</td>
 *   The handle renders its own arrow (components.css) and sorts by its
 *   data-sort-attr key; clicking the rest of the header sorts by the
 *   cell's data-sort-value as usual.
 */
(function () {
    'use strict';

    /** Extract the sort key from a row at colIndex / sortAttr. */
    function extractKey(row, colIndex, sortAttr) {
        if (!row) return '';
        if (sortAttr) {
            var cell = row.querySelector('[data-' + sortAttr + ']');
            return cell ? (cell.getAttribute('data-' + sortAttr) || '') : '';
        }
        var cellAt = row.children[colIndex];
        return cellAt ? (cellAt.dataset.sortValue || '') : '';
    }

    /** Compare two raw sort-key strings under sortType + direction. */
    function compareKeys(aVal, bVal, sortType, isAsc) {
        if (sortType === 'numeric') {
            aVal = parseFloat(aVal) || 0;
            bVal = parseFloat(bVal) || 0;
            return isAsc ? aVal - bVal : bVal - aVal;
        }
        aVal = String(aVal || '').toLowerCase();
        bVal = String(bVal || '').toLowerCase();
        return isAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    }

    /** Sort `table` and move the arrow indicator to `indicatorEl`
     *  (the clicked th, or a .sort-handle inside one). */
    function applySort(table, indicatorEl, colIndex, sortType, sortAttr) {
        var isAsc = !indicatorEl.classList.contains('sort-asc');
        table.querySelectorAll('th, th .sort-handle').forEach(function (h) {
            h.classList.remove('sort-asc', 'sort-desc');
        });
        indicatorEl.classList.add(isAsc ? 'sort-asc' : 'sort-desc');

        var groups = Array.from(
            table.querySelectorAll('tbody.sortable-group')
        );
        if (groups.length) {
            // Multi-tbody mode: reorder the tbody nodes
            // themselves. Sort key comes from each group's
            // first <tr>. parent.appendChild moves the node
            // (it doesn't clone) so unmarked tbodies — the
            // Total row, the empty-state row — stay in place
            // as long as they were rendered before the
            // sortable group block.
            var parent = groups[0].parentNode;
            groups.sort(function (a, b) {
                return compareKeys(
                    extractKey(a.querySelector('tr'), colIndex, sortAttr),
                    extractKey(b.querySelector('tr'), colIndex, sortAttr),
                    sortType, isAsc
                );
            });
            groups.forEach(function (g) { parent.appendChild(g); });
            return;
        }

        // Single-tbody mode (existing behavior).
        var tbody = table.querySelector('tbody');
        var rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort(function (a, b) {
            return compareKeys(
                extractKey(a, colIndex, sortAttr),
                extractKey(b, colIndex, sortAttr),
                sortType, isAsc
            );
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
    }

    function bindTable(table) {
        if (table.dataset.sortableBound === '1') return;
        table.dataset.sortableBound = '1';

        table.querySelectorAll('th.sortable-header').forEach(function (th) {
            var colIndex = Array.from(th.parentNode.children).indexOf(th);

            th.addEventListener('click', function () {
                applySort(table, th, colIndex, th.dataset.sort, th.dataset.sortAttr);
            });

            // Secondary handles: own key via data-sort-attr, own arrow.
            th.querySelectorAll('.sort-handle[data-sort-attr]').forEach(function (handle) {
                handle.addEventListener('click', function (event) {
                    event.stopPropagation();
                    applySort(table, handle, colIndex,
                              handle.dataset.sort || th.dataset.sort,
                              handle.dataset.sortAttr);
                });
            });
        });
    }

    function bindWithin(root) {
        (root || document).querySelectorAll('table').forEach(function (table) {
            if (table.querySelector('th.sortable-header')) bindTable(table);
        });
    }

    // For content injected outside htmx (e.g. lazy-loading.js fetch)
    window.bindSortableTables = bindWithin;

    document.addEventListener('DOMContentLoaded', function () { bindWithin(document); });
    document.body && document.body.addEventListener('htmx:afterSwap', function (e) {
        bindWithin(e.detail.target);
    });
    // body may not exist yet at script-eval time; rebind on first DOM load too.
    document.addEventListener('htmx:afterSwap', function (e) { bindWithin(e.detail.target); });
})();
