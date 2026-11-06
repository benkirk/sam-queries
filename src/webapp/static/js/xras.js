/* Allocations > XRAS: the request dialog's inline amount editor
 * (xras_bits.amount_editor), and its prev/next through the Remediations rows.
 * The figure is the button; it swaps for a small form and back. Save posts
 * through htmx, which re-renders the whole dialog. */
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

/* Prev/next: a request opened from the Remediations table steps through the rows
 * that table shows (its filters, sort, and open groups), in its order. Body swaps
 * only, never a modal toggle (#464). */
(function () {
    'use strict';

    var CURRENT = 'is-queue-current';

    function modal() { return document.getElementById('xrasModal'); }
    function nav() { return document.querySelector('#xrasModalFooter [data-xras-queue-nav]'); }

    function rows() {
        return Array.prototype.filter.call(
            document.querySelectorAll('[data-xras-queue] [data-request-number]'),
            function (el) { return !el.closest('.collapse:not(.show)'); });
    }

    function indexOf(links, number) {
        for (var i = 0; i < links.length; i++) {
            if (links[i].dataset.requestNumber === number) { return i; }
        }
        return -1;
    }

    /* Opened from a table row: queued. Any other opener of this modal: not. */
    document.addEventListener('click', function (e) {
        var opener = e.target.closest && e.target.closest('[data-bs-target="#xrasModal"]');
        var m = modal();
        if (!opener || !m) { return; }
        if (opener.closest('[data-xras-queue]') && opener.dataset.requestNumber) {
            m.dataset.queue = '1';
        } else {
            delete m.dataset.queue;
            delete m.dataset.queueIndex;
        }
    }, true);

    function sync() {
        var m = modal();
        var n = nav();
        if (!m || !n) { return; }
        var links = m.dataset.queue ? rows() : [];
        /* Before the early return: an outside opener or a one-row table drops the old mark. */
        document.querySelectorAll('.' + CURRENT).forEach(function (tr) { tr.classList.remove(CURRENT); });
        n.hidden = links.length < 2;
        if (n.hidden) { return; }
        var i = indexOf(links, n.dataset.requestNumber);
        if (i >= 0) {
            m.dataset.queueIndex = String(i);
            var tr = links[i].closest('tr');
            if (tr) {
                tr.classList.add(CURRENT);
                /* Scroll only behind an open dialog: a card re-render after Done must not jump the page. */
                if (m.classList.contains('show')) { tr.scrollIntoView({block: 'nearest'}); }
            }
        }
        /* Gone from the table (a write moved it on): step from where it was. */
        var at = i >= 0 ? i : Number(m.dataset.queueIndex || 0);
        var prev = i >= 0 ? i - 1 : at - 1;
        var next = i >= 0 ? i + 1 : at;
        n.querySelector('[data-step="-1"]').disabled = prev < 0;
        n.querySelector('[data-step="1"]').disabled = next >= links.length;
        n.querySelector('[data-xras-queue-pos]').textContent =
            (i >= 0 ? (i + 1) : '–') + ' of ' + links.length;
    }

    function step(delta) {
        var m = modal();
        var n = nav();
        if (!m || !n || n.hidden) { return; }
        var links = rows();
        var i = indexOf(links, n.dataset.requestNumber);
        var at = Number(m.dataset.queueIndex || 0);
        var target = links[i >= 0 ? i + delta : (delta > 0 ? at : at - 1)];
        if (!target) { return; }
        htmx.ajax('GET', target.getAttribute('hx-get'),
                  {target: '#xrasModalBody', swap: 'innerHTML'});
    }

    registerAction('xras-queue-step', function (btn) { step(Number(btn.dataset.step)); });

    /* The dialog swapped, or the card re-rendered under it after a write. */
    document.body.addEventListener('htmx:afterSettle', sync);

    /* Arrow keys step, unless focus is in a field, a menu, or another dialog is up. */
    document.addEventListener('keydown', function (e) {
        if ((e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')
                || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) { return; }
        var m = modal();
        if (!m || !m.classList.contains('show')
                || document.querySelectorAll('.modal.show').length > 1) { return; }
        if (e.target.closest && e.target.closest(
                'input, select, textarea, [contenteditable], .dropdown-menu, [data-xras-amount]')) { return; }
        var n = nav();
        if (!n || n.hidden) { return; }
        e.preventDefault();
        step(e.key === 'ArrowRight' ? 1 : -1);
    });
})();
