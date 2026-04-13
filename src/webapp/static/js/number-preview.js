/**
 * Number Preview
 *
 * Opt-in comma-formatting for numeric form inputs, without touching the
 * input value itself. Any `<input data-number-preview>` gets a live
 * preview of its value formatted with thousands separators, rendered into
 * a sibling `<span class="number-preview">`.
 *
 * Usage (in a Jinja fragment):
 *
 *   <input type="number" name="amount" data-number-preview ...>
 *   <div class="form-text">
 *     <span class="number-preview text-primary fw-semibold"></span>
 *     Core-hours for HPC; TB for storage.
 *   </div>
 *
 * The `<input>` stays `type="number"`, so native spinner, mobile numeric
 * keyboard, step, min/max, and backend validation all keep working. The
 * value submitted is the plain number — no stripping or interception.
 *
 * Attribute lookup is delegated on `document`, so dynamically-inserted
 * inputs (modal re-renders, htmx swaps) work automatically.
 */
(function () {
    'use strict';

    function format(v) {
        if (v === '' || v == null) return '';
        var n = Number(v);
        if (!isFinite(n)) return '';
        return n.toLocaleString('en-US');
    }

    function updatePreview(input) {
        // Look for the preview span within the same field container.
        // Falls back to the parent element so layouts can wrap loosely.
        var container = input.closest('.mb-3') || input.closest('.form-group') || input.parentElement;
        if (!container) return;
        var preview = container.querySelector('.number-preview');
        if (!preview) return;
        var formatted = format(input.value);
        preview.textContent = formatted ? '= ' + formatted : '';
    }

    function primeAll(root) {
        (root || document).querySelectorAll('input[data-number-preview]').forEach(updatePreview);
    }

    // Live update on every keystroke / spinner click
    document.addEventListener('input', function (e) {
        if (e.target.matches && e.target.matches('input[data-number-preview]')) {
            updatePreview(e.target);
        }
    });

    // Initial page load
    document.addEventListener('DOMContentLoaded', function () { primeAll(document); });

    // htmx swaps (modal content re-renders, fragment reloads)
    document.addEventListener('htmx:afterSettle', function (evt) {
        primeAll(evt.detail && evt.detail.elt);
    });
})();
