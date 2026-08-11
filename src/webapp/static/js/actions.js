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

    /* ── Reveal a freshly-loaded card without scrolling past its header ──
     *
     * Two things made the old `scrollIntoView({block: 'start'})` land in
     * the middle of the card:
     *
     *   1. It ran on htmx:afterSwap, *before* the sibling afterRequest
     *      handler in form-helpers.js empties the search-results list.
     *      That list sits above the card, so the page shrank by its height
     *      after the scroll target was computed — the more matches were
     *      listed, the deeper past the card title you ended up.
     *   2. block:'start' pins the card's top edge to the viewport's top
     *      edge, leaving the title flush against it with no margin.
     *
     * So: defer a frame (post-clear, post-layout), aim slightly above the
     * card, and no-op when its header is already comfortably on screen so
     * an in-place reload never yanks the page around. */
    var CARD_REVEAL_OFFSET = 16;

    window.revealCard = function (el) {
        if (!el) { return; }
        requestAnimationFrame(function () {
            var top = el.getBoundingClientRect().top;
            /* Header already visible in the top half — leave it alone. */
            if (top >= 0 && top <= window.innerHeight / 2) { return; }
            window.scrollTo({
                top: Math.max(0, window.scrollY + top - CARD_REVEAL_OFFSET),
                behavior: 'smooth'
            });
        });
    };

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

    /* Open the shared #userDetailsModal (content loaded by the hx-get on
     * the same element). Used instead of data-bs-toggle by user rows that
     * can sit inside ANOTHER modal (e.g. the group-members modal stacked
     * over an open user modal): a Bootstrap toggle would close the open
     * user modal rather than re-show it. Hides the hosting modal first. */
    window.registerAction('show-user-details', function (el, evt) {
        evt.preventDefault();
        var host = el.closest('.modal.show');
        if (host && host.id !== 'userDetailsModal') {
            bootstrap.Modal.getOrCreateInstance(host).hide();
        }
        var target = document.getElementById('userDetailsModal');
        if (target) {
            bootstrap.Modal.getOrCreateInstance(target).show();
        }
    });

    /* Generalisation of the above for every other detail modal: the target
     * shell is named by data-modal-id, content still comes from the
     * element's own hx-get. Needed for the same reason — a link inside an
     * already-open modal (a contract on a project card shown in
     * #projectDetailsModal, a contract row in the NSF-program modal) must
     * stack rather than have Bootstrap's toggle close its host. Works
     * unchanged outside a modal, where closest('.modal.show') is null.
     *
     * 'show-user-details' predates this and keeps its own registration
     * because its callers pass no data-modal-id; it could fold in later. */
    window.registerAction('show-detail-modal', function (el, evt) {
        evt.preventDefault();
        var id = el.dataset.modalId;
        if (!id) { return; }
        var host = el.closest('.modal.show');
        if (host && host.id !== id) {
            bootstrap.Modal.getOrCreateInstance(host).hide();
        }
        var target = document.getElementById(id);
        if (target) {
            bootstrap.Modal.getOrCreateInstance(target).show();
        }
    });

    /* Filter-bar "Reset" buttons: reset the form, then re-submit it so
     * the htmx fragment reloads with defaults. */
    window.registerAction('form-reset-submit', function (el) {
        var form = document.getElementById(el.dataset.formId);
        form.reset();
        htmx.trigger(form, 'submit');
    });

    /* Jobs-explorer facet chips: write the chip's value into the named
     * field of the filter panel form, then re-submit it (the panel's
     * hx-trigger="submit" refetches the table + OOB chip strip). An
     * empty data-value clears the filter — the active chip doubles as
     * its own clear button. A <select> target (the QoS dropdown) gets
     * the option appended if the catalog doesn't already list it, so a
     * facet value can never silently fail to apply. */
    window.registerAction('set-filter-submit', function (el) {
        var form = document.getElementById(el.dataset.formId);
        if (!form) { return; }
        var field = form.elements[el.dataset.field];
        if (!field) { return; }
        var value = el.dataset.value || '';
        if (field.tagName === 'SELECT' && value &&
                !Array.prototype.some.call(field.options, function (o) {
                    return o.value === value;
                })) {
            var opt = document.createElement('option');
            opt.value = value;
            opt.textContent = value;
            field.appendChild(opt);
        }
        field.value = value;
        /* data-clear-fields: blank these siblings before submitting. A window
         * pill sets `days`, but an explicit start/end range OUTRANKS `days`
         * server-side — so without clearing them the pill would submit and
         * appear to do nothing. Names, comma-separated; missing ones ignored. */
        (el.dataset.clearFields || '').split(',').forEach(function (name) {
            var other = form.elements[name.trim()];
            if (other) { other.value = ''; }
        });
        htmx.trigger(form, 'submit');
    });

    /* Reveal the custom date inputs beside a set of window pills. Plain
     * d-none toggle rather than a Bootstrap collapse: this sits inside a
     * filter form, and Bootstrap's collapse data-api runs in the CAPTURE
     * phase on document, which is what makes it hostile to controls nested
     * near buttons (see dashboards/fragments/collapse.html). */
    window.registerAction('toggle-custom-window', function (el) {
        var panel = document.querySelector(el.dataset.target);
        if (!panel) { return; }
        var hidden = panel.classList.toggle('d-none');
        el.setAttribute('aria-expanded', hidden ? 'false' : 'true');
        if (!hidden) {
            var first = panel.querySelector('input');
            if (first) { first.focus(); }
        }
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
