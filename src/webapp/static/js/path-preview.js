/**
 * Path Preview
 *
 * Live preview for the (disk-root dropdown + sub-path text-input) pair
 * used by ProjectDirectory create/edit forms. Assembles the final stored
 * directory_name with duplicate slashes collapsed, mirroring the
 * server-side _assemble_directory_name().
 *
 * Usage (in a Jinja fragment):
 *
 *   <div class="input-group" data-path-preview-group>
 *     <select name="root_directory_id" data-path-preview-root>
 *       <option value="">-- root --</option>
 *       <option value="1" data-root-path="/glade/campaign">/glade/campaign</option>
 *     </select>
 *     <input type="text" name="directory_suffix" data-path-preview-suffix>
 *   </div>
 *   <div class="form-text">
 *     Final path: <code class="path-preview text-primary"></code>
 *   </div>
 *
 * The `data-root-path` attribute on each <option> carries the root path
 * string (the form `value` is the FK id, not the path). The preview span
 * is found by climbing to the nearest `.mb-3` / `.form-group` / parent
 * and looking for `.path-preview`.
 *
 * Listens with delegation on `input` and `change`, so dynamically
 * inserted groups (htmx swaps, modal re-renders) work automatically.
 */
(function () {
    'use strict';

    function dedupeSlashes(s) {
        return s.replace(/\/+/g, '/');
    }

    function compute(group) {
        var sel = group.querySelector('[data-path-preview-root]');
        var inp = group.querySelector('[data-path-preview-suffix]');
        if (!sel) return '';
        var opt = sel.options[sel.selectedIndex];
        var root = (opt && opt.dataset && opt.dataset.rootPath) || '';
        if (!root) return '';
        var suffix = inp ? (inp.value || '').trim() : '';
        var combined = suffix
            ? root.replace(/\/+$/, '') + '/' + suffix.replace(/^\/+/, '')
            : (root.replace(/\/+$/, '') || '/');
        return dedupeSlashes(combined);
    }

    function updatePreview(group) {
        var container = group.closest('.mb-3') || group.closest('.form-group') || group.parentElement;
        if (!container) return;
        var target = container.querySelector('.path-preview');
        if (!target) return;
        var path = compute(group);
        target.textContent = path || '—';
    }

    function primeAll(root) {
        (root || document).querySelectorAll('[data-path-preview-group]').forEach(updatePreview);
    }

    function handle(e) {
        var g = e.target && e.target.closest && e.target.closest('[data-path-preview-group]');
        if (g) updatePreview(g);
    }
    document.addEventListener('input', handle);
    document.addEventListener('change', handle);

    document.addEventListener('DOMContentLoaded', function () { primeAll(document); });
    document.addEventListener('htmx:afterSettle', function (evt) {
        primeAll(evt.detail && evt.detail.elt);
    });
})();
