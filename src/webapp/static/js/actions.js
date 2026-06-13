/* Delegated action dispatch — the CSP-safe replacement for inline
 * on*= handler attributes (script-src 'self' forbids them; see
 * webapp/utils/csp.py and tests/unit/test_template_csp_lint.py).
 *
 * Templates declare intent with data attributes:
 *
 *     <button data-action="drp-days" data-days="30">30d</button>
 *     <select data-action-change="quick-fill" ...>
 *     <input  data-action-input="uppercase" ...>
 *     <form   data-action-submit="confirm-first" ...>
 *
 * and a static JS module registers the behavior:
 *
 *     registerAction('drp-days', function (el, evt) { ... });
 *
 * Listeners are delegated on `document`, which is load-bearing, not
 * stylistic: htmx swaps fragments in and out, and per-element bindings
 * made at DOMContentLoaded die with the swapped-out nodes. Delegation
 * survives any swap. Code that must (re-)initialize swapped content
 * (marking active states, wiring tables) belongs in htmx.onLoad(...)
 * instead — see static/js/pickers.js for the pattern.
 */
(function () {
    'use strict';

    var actions = {};

    window.registerAction = function (name, fn) {
        actions[name] = fn;
    };

    function dispatch(attr) {
        return function (evt) {
            var el = evt.target.closest('[' + attr + ']');
            if (!el) { return; }
            var fn = actions[el.getAttribute(attr)];
            if (fn) { fn(el, evt); }
        };
    }

    document.addEventListener('click',  dispatch('data-action'));
    document.addEventListener('change', dispatch('data-action-change'));
    document.addEventListener('input',  dispatch('data-action-input'));
    document.addEventListener('submit', dispatch('data-action-submit'));

    /* ── Generic built-ins ── */

    /* Clickable rows/elements that just navigate
     * (replaces onclick="window.location='...'"). */
    window.registerAction('navigate', function (el) {
        window.location = el.dataset.href;
    });

    /* Uppercase-as-you-type inputs (projcode, mnemonic). */
    window.registerAction('uppercase', function (el) {
        el.value = el.value.toUpperCase();
    });

    /* Filter-bar "Reset" buttons: reset the form, then re-submit it so
     * the htmx fragment reloads with defaults. */
    window.registerAction('form-reset-submit', function (el) {
        var form = document.getElementById(el.dataset.formId);
        form.reset();
        htmx.trigger(form, 'submit');
    });

    /* Confirm-gated plain-form submit (samConfirm is an async Bootstrap
     * modal — always preventDefault, re-submit from onConfirm). Used via
     * <form data-action-submit="confirm-submit" data-confirm-message=...>. */
    window.registerAction('confirm-submit', function (form, evt) {
        evt.preventDefault();
        samConfirm({
            title:   form.dataset.confirmTitle   || 'Confirm action',
            message: form.dataset.confirmMessage || 'Are you sure?',
            variant: form.dataset.confirmVariant || 'warning',
            label:   form.dataset.confirmLabel   || 'Confirm',
            onConfirm: function () { form.submit(); }
        });
    });

    /* data-stop-propagation: element-level stopPropagation() (replaces
     * inline onclick="event.stopPropagation()"). Must be a real element
     * listener — the row clicks it guards against are htmx element-level
     * bindings, which a document-level guard could never intercept.
     * Re-bound per swapped subtree via htmx.onLoad. */
    function bindStopPropagation(scope) {
        var els = Array.prototype.slice.call(
            scope.querySelectorAll('[data-stop-propagation]'));
        if (scope.matches && scope.matches('[data-stop-propagation]')) {
            els.unshift(scope);
        }
        els.forEach(function (el) {
            if (el.samStopBound) { return; }
            el.samStopBound = true;
            el.addEventListener('click', function (evt) {
                evt.stopPropagation();
                /* The document-level dispatcher will never see this click,
                 * so honor a data-action on the same element here. */
                var fn = el.hasAttribute('data-action') &&
                         actions[el.getAttribute('data-action')];
                if (fn) { fn(el, evt); }
            });
        });
    }

    if (window.htmx) {
        htmx.onLoad(bindStopPropagation);
    } else {
        document.addEventListener('DOMContentLoaded', function () {
            bindStopPropagation(document.body);
        });
    }
})();
