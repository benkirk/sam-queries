/**
 * Collapse chevron rotation helper.
 *
 * Rotates a Font Awesome chevron icon 90deg when its associated Bootstrap
 * collapse pane expands, and back to 0deg when it collapses. Listens on the
 * Bootstrap collapse events (show.bs.collapse / hide.bs.collapse) attached
 * to each target pane.
 *
 * Usage (inline fragment):
 *
 *   <script>
 *     SamCollapseChevron.attach('#instutitions-pane', '.inst-type-collapse-icon');
 *     SamCollapseChevron.attach('#areas-pane',       '.collapse-icon');
 *   </script>
 *
 * The scope selector limits which triggers are wired up; the icon selector
 * picks the <i> element inside each trigger. Both are evaluated relative
 * to the trigger (iconSelector via querySelector inside the trigger node).
 */
(function () {
    'use strict';

    function attach(scopeSelector, iconSelector) {
        var root = document.querySelector(scopeSelector);
        if (!root) return;
        root.querySelectorAll('[data-bs-toggle="collapse"]').forEach(function (trigger) {
            var target = document.querySelector(trigger.dataset.bsTarget);
            if (!target) return;
            var icon = trigger.querySelector(iconSelector);
            if (!icon) return;
            target.addEventListener('show.bs.collapse', function () {
                icon.style.transform = 'rotate(90deg)';
            });
            target.addEventListener('hide.bs.collapse', function () {
                icon.style.transform = '';
            });
        });
    }

    window.SamCollapseChevron = { attach: attach };
})();
