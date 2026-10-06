/* Date range picker behavior, extracted from the inline script in
 * fragments/date_range_picker.html (CSP: script-src 'self'). The time
 * range picker needs none: its presets are htmx requests.
 *
 * Component contract:
 *   .drp root  — data-action-url, data-start (YYYY-MM-DD), optional
 *                data-epoch; a <script type="application/json"
 *                class="drp-hidden"> data block with extra query params;
 *                buttons with data-action="drp-days|drp-epoch|
 *                drp-toggle-custom"; a .drp-custom panel.
 *
 * Multiple pickers per page work via closest('.drp') scoping — the old
 * uid-suffixed window-global functions are gone.
 */
(function () {
    'use strict';

    /* Local calendar date. NOT toISOString(): that is UTC, which names
     * tomorrow after ~17:00 Mountain and breaks the preset highlight. */
    function fmtDate(d) {
        return [d.getFullYear(), d.getMonth() + 1, d.getDate()]
            .map(function (n) { return String(n).padStart(2, '0'); }).join('-');
    }

    function hiddenParams(root, selector) {
        var block = root.querySelector(selector);
        return new URLSearchParams(block ? JSON.parse(block.textContent) : {});
    }

    function navigate(root, params) {
        window.location.href = root.dataset.actionUrl + '?' + params.toString();
    }

    registerAction('drp-days', function (btn) {
        var root  = btn.closest('.drp');
        var end   = new Date();
        var start = new Date();
        start.setDate(start.getDate() - parseInt(btn.dataset.days, 10));
        var params = hiddenParams(root, '.drp-hidden');
        params.set('start_date', fmtDate(start));
        params.set('end_date', fmtDate(end));
        navigate(root, params);
    });

    registerAction('drp-epoch', function (btn) {
        var root = btn.closest('.drp');
        var params = hiddenParams(root, '.drp-hidden');
        params.set('start_date', root.dataset.epoch);
        params.set('end_date', fmtDate(new Date()));
        navigate(root, params);
    });

    registerAction('drp-toggle-custom', function (btn) {
        var root   = btn.closest('.drp');
        var panel  = root.querySelector('.drp-custom');
        var hidden = panel.classList.toggle('d-none');
        btn.classList.toggle('active', !hidden);
        btn.classList.toggle('btn-outline-primary', hidden);
        btn.classList.toggle('btn-primary', !hidden);
    });

    /* Highlight the date-picker preset matching the current range.
     * Runs per swapped subtree via htmx.onLoad so pickers arriving in
     * HTMX fragments get marked too (init-on-swap pattern). */
    function findRoots(scope) {
        var list = Array.prototype.slice.call(scope.querySelectorAll('.drp'));
        if (scope.matches && scope.matches('.drp')) { list.unshift(scope); }
        return list;
    }

    function markActive(scope) {
        findRoots(scope).forEach(function (root) {
            var curStart = root.dataset.start;
            var today = new Date();
            today.setHours(0, 0, 0, 0);

            root.querySelectorAll('[data-action="drp-days"]').forEach(function (btn) {
                var d = new Date(today);
                d.setDate(d.getDate() - parseInt(btn.dataset.days, 10));
                if (fmtDate(d) === curStart) { btn.classList.add('active'); }
            });

            if (root.dataset.epoch && curStart === root.dataset.epoch) {
                root.querySelectorAll('[data-action="drp-epoch"]').forEach(function (btn) {
                    btn.classList.add('active');
                });
            }
        });
    }

    if (window.htmx) {
        htmx.onLoad(markActive);
    } else {
        document.addEventListener('DOMContentLoaded', function () {
            markActive(document.body);
        });
    }
})();
