/* Allocations > XRAS: the request dialog's inline amount editor
 * (xras_bits.amount_editor). The figure is the button; it swaps for a small
 * form and back. Save posts through htmx, which re-renders the whole dialog. */
(function () {
    'use strict';

    function cellOf(el) { return el.closest('[data-xras-amount]'); }

    function close(cell) {
        var input = cell.querySelector('input[name="amount"]');
        input.value = input.defaultValue;
        cell.classList.remove('is-editing');
        cell.querySelector('.xras-amount-show').focus();
    }

    registerAction('xras-amount-edit', function (btn) {
        var cell = cellOf(btn);
        document.querySelectorAll('[data-xras-amount].is-editing').forEach(function (open) {
            if (open !== cell) { close(open); }
        });
        cell.classList.add('is-editing');
        var input = cell.querySelector('input[name="amount"]');
        input.focus();
        input.select();
    });

    registerAction('xras-amount-cancel', function (btn) { close(cellOf(btn)); });

    /* Esc cancels the edit, not the dialog: capture on document runs before
     * Bootstrap's keydown on the modal. */
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !e.target.closest) { return; }
        var cell = e.target.closest('[data-xras-amount].is-editing');
        if (!cell) { return; }
        e.preventDefault();
        e.stopPropagation();
        close(cell);
    }, true);
})();
