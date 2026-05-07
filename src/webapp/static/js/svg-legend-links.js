/**
 * SVG legend link → modal interceptor
 *
 * matplotlib chart legends in this app embed `<a xlink:href="...">`
 * wrappers around legend swatches/labels via Artist.set_url(). Those
 * URLs are sentinel paths (e.g. /project-details-modal/<pc>,
 * /admin/user/<u>) that, if followed normally, would full-page-navigate.
 * Instead, we delegate-listen for clicks on those anchors and dispatch
 * the existing HTMX-driven Bootstrap modal flow used elsewhere in the
 * app (see e.g. templates/dashboards/allocations/partials/project_table.html).
 *
 * Safe on pages where the target modal containers aren't included —
 * the handler checks for them and silently no-ops.
 */
(function () {
    'use strict';

    var ROUTES = {
        '/user/project-details-modal/': {
            container: 'projectDetailsModal',
            body:      'projectDetailsModalBody',
        },
        '/admin/user/': {
            container: 'userDetailsModal',
            body:      'userDetailsModalBody',
        },
    };

    document.addEventListener('click', function (e) {
        var a = e.target.closest && e.target.closest('svg a');
        if (!a) return;
        var href = a.getAttribute('href') || a.getAttribute('xlink:href');
        if (!href) return;

        for (var prefix in ROUTES) {
            if (href.indexOf(prefix) === -1) continue;
            var cfg = ROUTES[prefix];
            var modalEl = document.getElementById(cfg.container);
            if (!modalEl || !window.htmx || !window.bootstrap) return;
            e.preventDefault();
            htmx.ajax('GET', href, {target: '#' + cfg.body, swap: 'innerHTML'});
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
            return;
        }
    }, false);
})();
