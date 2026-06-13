/* Form-fragment behaviors extracted from inline <script> blocks and
 * on*=/hx-on:: attributes across the admin/user form fragments (CSP:
 * script-src 'self').
 *
 * Form fragments load and re-load via htmx, so initialization runs under
 * htmx.onLoad gated on per-fragment marker elements; one-shot reactions
 * to htmx lifecycle events use delegated listeners (htmx events bubble
 * to document.body).
 */
(function () {
    'use strict';

    function has(root, selector) {
        return (root.matches && root.matches(selector)) || root.querySelector(selector);
    }

    /* ── Search-select result buttons (user/group/project searches) ──
     * Replaces hx-on::after-request attributes (htmx evaluates those via
     * Function(), which needs 'unsafe-eval'). After the button's request,
     * clear the result list and the search input. */
    document.body.addEventListener('htmx:afterRequest', function (e) {
        var el = e.detail.elt;
        if (!el.dataset || !el.dataset.clearResults) { return; }
        var results = document.getElementById(el.dataset.clearResults);
        if (results) { results.innerHTML = ''; }
        var input = document.getElementById(el.dataset.clearInput);
        if (input) { input.value = ''; }
    });

    /* ── Single-option auto-select after cascading dropdown loads ──
     * (create-project and create-allocation-type forms). htmx:afterSwap
     * bubbles; e.target is the swapped select. */
    function autoSelectSingleOption(select) {
        var opts = Array.from(select.options).filter(function (o) { return o.value !== ''; });
        if (opts.length === 1) {
            select.value = opts[0].value;
            return true;
        }
        return false;
    }

    document.body.addEventListener('htmx:afterSwap', function (e) {
        switch (e.target.id) {
            case 'createAllocTypePanel':
            case 'createProjectAllocType':
                autoSelectSingleOption(e.target);
                break;
            case 'createProjectPanel':
                if (autoSelectSingleOption(e.target)) {
                    htmx.trigger(e.target, 'change');   /* cascade → alloc types */
                }
                break;
            case 'projcodePreview': {
                /* keep hidden projcode in sync with the auto-preview */
                var mode = document.querySelector('[name="projcode_mode"]:checked');
                if (mode && mode.value === 'auto') {
                    var val = e.target.textContent.trim();
                    document.getElementById('projcodeHidden').value =
                        (val && val !== '—') ? val : '';
                }
                break;
            }
        }
    });

    /* ── Create Project form: projcode mode toggle ── */

    function applyProjcodeMode(mode) {
        var autoSection   = document.getElementById('projcodeAutoSection');
        var manualSection = document.getElementById('projcodeManualSection');
        var manualInput   = document.getElementById('projcodeManualInput');
        var hiddenCode    = document.getElementById('projcodeHidden');
        var previewEl     = document.getElementById('projcodePreview');
        if (mode === 'manual') {
            autoSection.style.display   = 'none';
            manualSection.style.display = '';
            hiddenCode.value = manualInput.value.toUpperCase();
        } else {
            autoSection.style.display   = '';
            manualSection.style.display = 'none';
            var preview = previewEl.textContent.trim();
            hiddenCode.value = preview !== '—' ? preview : '';
        }
    }

    registerAction('projcode-mode', function (radio) {
        applyProjcodeMode(radio.value);
    });

    /* Uppercase the manual projcode as typed and keep the hidden field
     * in sync. */
    registerAction('projcode-manual-sync', function (input) {
        input.value = input.value.toUpperCase();
        document.getElementById('projcodeHidden').value = input.value;
    });

    /* ── Create Mnemonic Code form ──
     * Populate description from the selected dropdown option's
     * data-description; reset the other dropdown so only one source is
     * active at a time. */
    registerAction('mc-fill-description', function (selectEl) {
        var opt = selectEl.options[selectEl.selectedIndex];
        var desc = opt.dataset.description || '';
        if (desc) {
            document.getElementById('createMcDescription').value = desc;
            var otherId = selectEl.dataset.source === 'institution'
                ? 'createMcOrganization' : 'createMcInstitution';
            document.getElementById(otherId).value = '';
        }
    });

    /* ── Edit Allocation form: break-inheritance unlock checkbox ── */
    registerAction('alloc-break-inheritance', function (checkbox) {
        var unlock = checkbox.checked;
        document.getElementById('break_inheritance').value = unlock ? 'true' : 'false';
        ['editAllocAmount', 'editAllocStart', 'editAllocEnd'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) { el.disabled = !unlock; }
        });
    });

    /* ── Project linked-elements card: Add-form collapse panels ── */
    registerAction('le-toggle', function (el) {
        var panel = document.getElementById(el.dataset.targetId);
        if (panel) { panel.style.display = panel.style.display === 'none' ? '' : 'none'; }
    });

    /* ── Add Member form: user search select/clear ── */
    registerAction('member-select-user', function (el) {
        var d = el.dataset;
        document.getElementById('htmxSelectedUsername').value = d.username;
        document.getElementById('htmxSelectedUserName').textContent =
            d.displayName + ' (' + d.username + ')';
        document.getElementById('htmxSelectedUserEmail').textContent = d.email || '';
        document.getElementById('htmxSelectedUserDisplay').style.display = 'block';
        document.getElementById('htmxUserSearchResults').innerHTML = '';
        document.getElementById('htmxUserSearch').value = '';
        document.getElementById('htmxAddMemberSubmitBtn').disabled = false;
    });

    registerAction('member-clear-user', function () {
        document.getElementById('htmxSelectedUsername').value = '';
        document.getElementById('htmxSelectedUserDisplay').style.display = 'none';
        document.getElementById('htmxAddMemberSubmitBtn').disabled = true;
        document.getElementById('htmxUserSearch').focus();
    });

    /* ── Project tree: open the project-details modal ──
     * The button also carries data-stop-propagation (actions.js fires the
     * action from the element-level listener in that case). */
    registerAction('project-details-modal', function (el) {
        htmx.ajax('GET', el.dataset.url,
                  {target: '#projectDetailsModalBody', swap: 'innerHTML'});
        bootstrap.Modal.getOrCreateInstance(
            document.getElementById('projectDetailsModal')).show();
    });

    /* ── Project card: project-tree active-only toggle ── */
    registerAction('tree-toggle-active-only', function (checkbox) {
        var container = document.getElementById('tree-container-' + checkbox.dataset.cardId);
        var url = checkbox.checked
            ? checkbox.dataset.baseUrl + '?active_only=1' : checkbox.dataset.baseUrl;
        if (container && container.dataset.loaded === 'true') {
            htmx.ajax('GET', url, { target: container, swap: 'innerHTML' });
        } else if (container) {
            container.dataset.loadUrl = url;  /* update before lazy-load fires */
        }
    });

    /* ── edit_project page: HX-Trigger reload hooks ── */

    /* After a successful project details save, the success fragment is
     * shown in #editDetailsContainer. Reload the page so the form, header
     * title, and "Inactive" badge reflect the new values. Brief delay so
     * the user sees the green checkmark first. */
    document.body.addEventListener('reloadEditProjectDetails', function () {
        setTimeout(function () { window.location.reload(); }, 800);
    });

    /* Reload Allocation Tree after add/edit allocation success. Preserve
     * the current "Active at" date so a historical view is not reset to
     * today. The fragment URL rides data-tree-url on the container. */
    document.body.addEventListener('reloadAllocationTree', function () {
        var container = document.getElementById('allocationTreeContainer');
        if (!container) { return; }
        var url = container.dataset.treeUrl;
        var activeAtInput = document.getElementById('alloc-active-at');
        if (activeAtInput && activeAtInput.value) {
            url += '?active_at=' + encodeURIComponent(activeAtInput.value);
        }
        htmx.ajax('GET', url, {
            target: '#allocationTreeContainer',
            swap: 'innerHTML',
            indicator: '#allocTreeSpinner, #allocationTreeContainer'
        });
    });

    /* ── Per-swap initialization ── */

    function initCreateProjectForm() {
        /* re-apply mode on form re-render with validation errors */
        var currentMode = document.querySelector('[name="projcode_mode"]:checked');
        if (currentMode) { applyProjcodeMode(currentMode.value); }
    }

    function initExchangeForm(root) {
        var container = document.getElementById('exchangeAllocationFormContainer');
        if (!container) { return; }
        var fromSel = container.querySelector('#exchangeFromProject');
        var toSel   = container.querySelector('#exchangeToProject');
        var amtInp  = container.querySelector('#exchangeAmount');
        var fromP   = container.querySelector('#exchangeFromPreview');
        var toP     = container.querySelector('#exchangeToPreview');
        if (!fromSel || !toSel || !amtInp || !fromP || !toP) { return; }

        function fmt(n) {
            if (!isFinite(n)) { return ''; }
            return n.toLocaleString('en-US');
        }

        function pickedData(sel) {
            var opt = sel.options[sel.selectedIndex];
            if (!opt || !opt.value) { return null; }
            return {
                amount: parseFloat(opt.dataset.amount) || 0,
                used: parseFloat(opt.dataset.used) || 0,
                projcode: opt.dataset.projcode || ''
            };
        }

        /* Disable the currently-picked value of ``sourceSel`` in
         * ``targetSel`` so the user can't pick the same project on both
         * sides. If the target already has that value selected, clear it. */
        function syncDisabled(sourceSel, targetSel) {
            var pick = sourceSel.value;
            for (var i = 0; i < targetSel.options.length; i++) {
                var o = targetSel.options[i];
                if (!o.value) { continue; } /* skip placeholder */
                o.disabled = (o.value === pick);
            }
            if (targetSel.value && targetSel.value === pick) {
                targetSel.value = '';
            }
        }

        function render() {
            var from = pickedData(fromSel);
            var to   = pickedData(toSel);
            var amt  = parseFloat(amtInp.value) || 0;

            if (from) {
                var newFrom = from.amount - amt;
                var remaining = from.amount - from.used;
                var flag = '';
                if (amt > 0 && amt > remaining) {
                    flag = ' <span class="text-danger fw-semibold">⚠ below used (' + fmt(from.used) + ')</span>';
                }
                fromP.innerHTML =
                    '<strong>' + from.projcode + '</strong>: ' +
                    fmt(from.amount) + ' − ' + fmt(amt) + ' = <strong>' + fmt(newFrom) + '</strong>' +
                    ' <span class="text-muted">(used: ' + fmt(from.used) + ')</span>' + flag;
            } else {
                fromP.textContent = '';
            }

            if (to) {
                var newTo = to.amount + amt;
                toP.innerHTML =
                    '<strong>' + to.projcode + '</strong>: ' +
                    fmt(to.amount) + ' + ' + fmt(amt) + ' = <strong>' + fmt(newTo) + '</strong>';
            } else {
                toP.textContent = '';
            }

            if (from && to && fromSel.value === toSel.value) {
                toP.innerHTML = '<span class="text-danger">FROM and TO must differ.</span>';
            }
        }

        fromSel.addEventListener('change', function () {
            syncDisabled(fromSel, toSel);
            render();
        });
        toSel.addEventListener('change', function () {
            syncDisabled(toSel, fromSel);
            render();
        });
        amtInp.addEventListener('input', render);
        /* Prime: if the form re-rendered after a validation error with a
         * FROM already picked, apply the disabled state up-front. */
        syncDisabled(fromSel, toSel);
        syncDisabled(toSel, fromSel);
        render();
    }

    function initMnemonicPrefill(root) {
        /* If a prefill description was passed (e.g. from clicking a
         * missing-mnemonic badge), set it and try to pre-select the
         * matching institution in the dropdown. */
        var block = has(root, '#createMcPrefill');
        if (!block) { return; }
        var prefill = JSON.parse(block.textContent);
        if (!prefill) { return; }
        document.getElementById('createMcDescription').value = prefill;
        var instSel = document.getElementById('createMcInstitution');
        for (var i = 0; i < instSel.options.length; i++) {
            if (instSel.options[i].dataset.description === prefill) {
                instSel.selectedIndex = i;
                break;
            }
        }
    }

    htmx.onLoad(function (root) {
        if (has(root, '#projcodeHidden')) { initCreateProjectForm(); }
        if (has(root, '#exchangeFromProject')) { initExchangeForm(root); }
        initMnemonicPrefill(root);

        if (has(root, '.facility-collapse-icon')) {
            SamCollapseChevron.attach('#facilities-pane', '.facility-collapse-icon');
        }
        if (has(root, '.pd-res-collapse-icon')) {
            SamCollapseChevron.attach('#projectDirectoriesSection', '.pd-res-collapse-icon');
        }
    });
})();
