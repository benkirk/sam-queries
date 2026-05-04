/* Client-side table sorting.
 *
 * Markup contract:
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
 * Initial display order comes from the server. Adding `sort-desc` /
 * `sort-asc` to a header just renders the arrow indicator; nothing is
 * re-sorted until a user clicks. Click toggles asc/desc; data-sort-attr
 * lets a single colspan cell carry sort keys for several columns.
 */
(function () {
    'use strict';

    function bindTable(table) {
        if (table.dataset.sortableBound === '1') return;
        table.dataset.sortableBound = '1';

        table.querySelectorAll('th.sortable-header').forEach(function (th) {
            th.addEventListener('click', function () {
                var colIndex = Array.from(th.parentNode.children).indexOf(th);
                var sortType = th.dataset.sort;
                var sortAttr = th.dataset.sortAttr;
                var isAsc = !th.classList.contains('sort-asc');
                table.querySelectorAll('th').forEach(function (h) {
                    h.classList.remove('sort-asc', 'sort-desc');
                });
                th.classList.add(isAsc ? 'sort-asc' : 'sort-desc');

                var tbody = table.querySelector('tbody');
                var rows = Array.from(tbody.querySelectorAll('tr'));
                rows.sort(function (a, b) {
                    var aVal, bVal;
                    if (sortAttr) {
                        var aCell = a.querySelector('[data-' + sortAttr + ']');
                        var bCell = b.querySelector('[data-' + sortAttr + ']');
                        aVal = aCell ? (aCell.getAttribute('data-' + sortAttr) || '') : '';
                        bVal = bCell ? (bCell.getAttribute('data-' + sortAttr) || '') : '';
                    } else {
                        aVal = a.children[colIndex] ? a.children[colIndex].dataset.sortValue : '';
                        bVal = b.children[colIndex] ? b.children[colIndex].dataset.sortValue : '';
                    }
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

    function bindWithin(root) {
        (root || document).querySelectorAll('table').forEach(function (table) {
            if (table.querySelector('th.sortable-header')) bindTable(table);
        });
    }

    document.addEventListener('DOMContentLoaded', function () { bindWithin(document); });
    document.body && document.body.addEventListener('htmx:afterSwap', function (e) {
        bindWithin(e.detail.target);
    });
    // body may not exist yet at script-eval time; rebind on first DOM load too.
    document.addEventListener('htmx:afterSwap', function (e) { bindWithin(e.detail.target); });
})();
