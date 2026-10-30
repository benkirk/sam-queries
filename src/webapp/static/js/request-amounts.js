/* My Requests amounts table (request_bits.amounts_table): a click on an Available row moves it
 * to Selected, enabling its inputs and filling its box with the default; Remove moves it back.
 * An Available row's inputs are disabled, so it posts nothing. CSP: script-src 'self'. */
(function () {
    'use strict';

    function group(table, name) { return table.querySelector('[data-amount-group="' + name + '"]'); }
    function rows(tbody) { return tbody.querySelectorAll('[data-amount-row]'); }

    function refresh(table) {
        var selected = group(table, 'selected'), available = group(table, 'available');
        selected.querySelector('[data-amount-empty]').hidden = rows(selected).length > 0;
        available.hidden = rows(available).length === 0;
    }

    /* Keep XRAS's order within a group. */
    function place(tbody, row) {
        var order = Number(row.dataset.order), before = null;
        rows(tbody).forEach(function (r) {
            if (!before && Number(r.dataset.order) > order) { before = r; }
        });
        tbody.insertBefore(row, before);
    }

    function select(row) {
        var table = row.closest('[data-amounts]');
        row.classList.remove('amount-row-available');
        row.querySelectorAll('input').forEach(function (i) { i.disabled = false; });
        var box = row.querySelector('.amount-input');
        if (box && !box.value) { box.value = box.dataset.suggest || ''; }
        place(group(table, 'selected'), row);
        refresh(table);
        row.classList.remove('amount-row-arrived');
        void row.offsetWidth;               // restart the arrival tint
        row.classList.add('amount-row-arrived');
        if (box) { box.focus(); box.select(); } else { row.querySelector('.amount-remove').focus(); }
    }

    function remove(row) {
        var table = row.closest('[data-amounts]');
        row.classList.remove('amount-row-arrived');
        row.classList.add('amount-row-available');
        row.querySelectorAll('input').forEach(function (i) { i.disabled = true; });
        place(group(table, 'available'), row);
        refresh(table);
        row.querySelector('.amount-pick').focus();
    }

    document.addEventListener('click', function (e) {
        var row = e.target.closest('[data-amounts] [data-amount-row]');
        if (!row) { return; }
        if (e.target.closest('[data-amount-move="remove"]')) {
            remove(row);
        } else if (row.classList.contains('amount-row-available')) {
            select(row);
        }
    });
})();
