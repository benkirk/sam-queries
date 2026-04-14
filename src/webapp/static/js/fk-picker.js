// Generic FK-picker widget.
//
// Replaces the per-form click handlers that copy a selected search result
// into a hidden input + visible badge. One delegated click listener handles
// every picker on the page, so adding a new picker only costs the markup —
// no JS changes.
//
// Markup contract — wrap a picker like this:
//
//   <div class="mb-3 fk-picker">
//       <label class="form-label">Primary Sysadmin</label>
//
//       <div class="fk-picker-selected mb-1" style="display:none;">
//           <span class="badge bg-info py-1 px-2 fk-picker-badge"></span>
//           <button type="button" class="btn btn-xs btn-outline-secondary ms-1 fk-picker-clear">
//               <i class="fas fa-times"></i>
//           </button>
//       </div>
//
//       <input type="hidden" name="prim_sys_admin_user_id" class="fk-picker-id" value="">
//
//       <input type="text" class="form-control fk-picker-search"
//              placeholder="Search…"
//              hx-get="/admin/htmx/search-users?context=fk"
//              hx-target="next .fk-picker-results"
//              hx-swap="innerHTML"
//              hx-trigger="input changed delay:300ms"
//              name="q">
//       <div class="list-group mt-1 fk-picker-results"
//            style="max-height:180px;overflow-y:auto;"></div>
//   </div>
//
// Search result templates emit:
//   <div class="fk-search-result" data-fk-id="123" data-fk-label="Alice (alice)">…</div>
//
// Custom events fired (bubbling) for forms that need to react:
//   fk:selected   — detail: {id, label}
//   fk:cleared    — detail: {}

(function () {
    function reset(picker) {
        var idEl = picker.querySelector('.fk-picker-id');
        var badgeEl = picker.querySelector('.fk-picker-badge');
        var selectedEl = picker.querySelector('.fk-picker-selected');
        if (idEl) idEl.value = '';
        if (badgeEl) badgeEl.textContent = '';
        if (selectedEl) selectedEl.style.display = 'none';
    }

    document.body.addEventListener('click', function (e) {
        // ── Selection ──────────────────────────────────────────────────
        var item = e.target.closest('.fk-search-result');
        if (item) {
            var picker = item.closest('.fk-picker');
            if (!picker) return;

            var idValue = item.dataset.fkId;
            var labelValue = item.dataset.fkLabel;
            if (idValue === undefined || labelValue === undefined) {
                // Search result template hasn't been migrated to data-fk-id /
                // data-fk-label yet — bail out so legacy inline handlers can run.
                return;
            }

            var idEl = picker.querySelector('.fk-picker-id');
            var badgeEl = picker.querySelector('.fk-picker-badge');
            var selectedEl = picker.querySelector('.fk-picker-selected');
            var resultsEl = picker.querySelector('.fk-picker-results');
            var searchEl = picker.querySelector('.fk-picker-search');

            if (idEl) idEl.value = idValue;
            if (badgeEl) badgeEl.textContent = labelValue;
            if (selectedEl) selectedEl.style.display = '';
            if (resultsEl) resultsEl.innerHTML = '';
            if (searchEl) searchEl.value = '';

            picker.dispatchEvent(new CustomEvent('fk:selected', {
                bubbles: true,
                detail: {id: idValue, label: labelValue}
            }));
            return;
        }

        // ── Clear ──────────────────────────────────────────────────────
        var clearBtn = e.target.closest('.fk-picker-clear');
        if (clearBtn) {
            var picker2 = clearBtn.closest('.fk-picker');
            if (!picker2) return;
            reset(picker2);
            picker2.dispatchEvent(new CustomEvent('fk:cleared', {bubbles: true}));
        }
    });
})();
