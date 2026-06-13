/* Date / time range picker behavior, extracted from the inline scripts
 * in fragments/date_range_picker.html and time_range_picker.html (CSP:
 * script-src 'self').
 *
 * Component contract:
 *   .drp root  — data-action-url, data-start (YYYY-MM-DD), optional
 *                data-epoch; a <script type="application/json"
 *                class="drp-hidden"> data block with extra query params;
 *                buttons with data-action="drp-days|drp-epoch|
 *                drp-toggle-custom"; a .drp-custom panel.
 *   .trp root  — data-action-url + .trp-hidden data block; buttons with
 *                data-action="trp-hours" data-hours="N". Active state is
 *                server-rendered (hours == N), no client marking needed.
 *
 * Multiple pickers per page work via closest('.drp') scoping — the old
 * uid-suffixed window-global functions are gone.
 */
(function () {
    'use strict';

    function fmtDate(d) {
        return d.toISOString().slice(0, 10);
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

    registerAction('trp-hours', function (btn) {
        var root = btn.closest('.trp');
        var params = hiddenParams(root, '.trp-hidden');
        params.set('hours', btn.dataset.hours);
        navigate(root, params);
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
                if (fmtDate(d) === curStart) {
                    btn.classList.remove('btn-outline-secondary');
                    btn.classList.add('btn-secondary', 'active');
                }
            });

            if (root.dataset.epoch && curStart === root.dataset.epoch) {
                root.querySelectorAll('[data-action="drp-epoch"]').forEach(function (btn) {
                    btn.classList.remove('btn-outline-secondary');
                    btn.classList.add('btn-secondary', 'active');
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
