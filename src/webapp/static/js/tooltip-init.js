/*
 * tooltip-init.js — global activation of Bootstrap tooltips & popovers.
 *
 * The app emits help affordances declaratively via the help.html macros
 * (data-bs-toggle="tooltip" / "popover"). Bootstrap does not auto-initialize
 * these, so we do it here: once on load, and again for any fragment HTMX swaps
 * into the page. Instances are disposed before HTMX removes their trigger so no
 * orphaned popup lingers in <body>.
 *
 * Self-hosted (CSP script-src 'self'); no inline script required.
 */
(function () {
  'use strict';

  function initIn(root) {
    if (!root || !window.bootstrap) return;
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
      if (!bootstrap.Tooltip.getInstance(el)) new bootstrap.Tooltip(el);
    });
    root.querySelectorAll('[data-bs-toggle="popover"]').forEach(function (el) {
      if (!bootstrap.Popover.getInstance(el)) new bootstrap.Popover(el);
    });
  }

  function disposeIn(el) {
    if (!el || !window.bootstrap) return;
    var tip = bootstrap.Tooltip.getInstance(el);
    if (tip) tip.dispose();
    var pop = bootstrap.Popover.getInstance(el);
    if (pop) pop.dispose();
  }

  document.addEventListener('DOMContentLoaded', function () {
    initIn(document);
  });

  // Fragments swapped in by HTMX (rolling-rate, card bodies, modals, ...).
  document.body.addEventListener('htmx:afterSwap', function (evt) {
    initIn(evt.target);
  });

  // Dispose before HTMX removes a node, and its descendants, to avoid orphans.
  document.body.addEventListener('htmx:beforeCleanupElement', function (evt) {
    var el = evt.target;
    if (!el || !el.matches) return;
    if (el.matches('[data-bs-toggle="tooltip"], [data-bs-toggle="popover"]')) {
      disposeIn(el);
    }
    if (el.querySelectorAll) {
      el.querySelectorAll('[data-bs-toggle="tooltip"], [data-bs-toggle="popover"]')
        .forEach(disposeIn);
    }
  });
})();
