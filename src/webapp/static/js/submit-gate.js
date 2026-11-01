/* The submit gate and the busy button, for every page that hosts a request dialog:
 * dashboards/base.html and the visitor's register/request_draft.html. A form's
 * [data-gate-submit] stays disabled until every [data-gate-check] is met (a checkbox checked
 * or a file input holding a file); data-gate-blocked keeps a button the server disabled;
 * [data-gate-hint] names what is missing from each unmet check's data-gate-label. The
 * /register gate's own vocabulary ([data-register-gate], register.js) is a different form.
 * Delegated listeners: fragments arrive by htmx swap, and the CSP allows no inline script. */
(function () {
    'use strict';

    function gateForm(form) {
        var button = form.querySelector('[data-gate-submit]');
        if (!button) { return; }
        var missing = Array.prototype.filter.call(form.querySelectorAll('[data-gate-check]'),
            function (c) { return c.type === 'file' ? !c.files.length : !c.checked; });
        button.disabled = button.hasAttribute('data-gate-blocked') || missing.length > 0;
        var hint = form.querySelector('[data-gate-hint]');
        if (hint) {
            var needs = missing.map(function (c) { return c.dataset.gateLabel; }).filter(Boolean);
            hint.textContent = needs.length ? 'To submit, ' + needs.join(' and ') + '.' : '';
        }
    }

    document.addEventListener('change', function (e) {
        if (!e.target.matches || !e.target.matches('[data-gate-check]')) { return; }
        var form = e.target.closest('form');
        if (form) { gateForm(form); }
    });

    /* A [data-busy] submit button shows a spinner, and the form's [data-gate-hint] its text,
     * while the request runs. It is disabled at beforeSend, once htmx has read its value: a
     * "find button" hx-disabled-elt disables only the FIRST match, which is often Back. */
    document.addEventListener('submit', function (e) {
        var button = e.submitter;
        if (!button || !button.hasAttribute('data-busy')) { return; }
        button.classList.add('is-busy');
        var hint = e.target.querySelector('[data-gate-hint]');
        if (hint) { hint.textContent = button.dataset.busy; }
    }, true);

    document.body.addEventListener('htmx:beforeSend', function (e) {
        var form = e.detail.elt && e.detail.elt.closest && e.detail.elt.closest('form');
        if (!form) { return; }
        form.querySelectorAll('.is-busy').forEach(function (b) { b.disabled = true; });
    });

    /* A failed request on a page with no error toast (the register pages) says so in the
     * hint; the dashboards' htmx-config.js shows its toast. */
    document.body.addEventListener('htmx:afterRequest', function (e) {
        var form = e.detail.elt && e.detail.elt.closest && e.detail.elt.closest('form');
        if (!form) { return; }
        var busy = form.querySelectorAll('.is-busy');
        busy.forEach(function (b) {
            b.classList.remove('is-busy');
            b.disabled = false;
        });
        gateForm(form);
        var hint = form.querySelector('[data-gate-hint]');
        if (hint && busy.length && e.detail.failed && !document.getElementById('htmxErrorToast')) {
            hint.textContent = 'The request did not go through. Try again, or write to help@ucar.edu.';
        }
    });

    htmx.onLoad(function (root) {
        root.querySelectorAll('[data-gate-submit]').forEach(function (b) {
            var form = b.closest('form');
            if (form) { gateForm(form); }
        });
    });
})();
