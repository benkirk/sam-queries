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
     * clear the result list and the search input, then scroll the card the
     * click just loaded into view — revealCard() runs a frame later, so it
     * measures the page *after* the result list above it is gone. */
    document.body.addEventListener('htmx:afterRequest', function (e) {
        var el = e.detail.elt;
        if (!el.dataset || !el.dataset.clearResults) { return; }
        var results = document.getElementById(el.dataset.clearResults);
        if (results) { results.innerHTML = ''; }
        var input = document.getElementById(el.dataset.clearInput);
        if (input) { input.value = ''; }
        var target = el.getAttribute('hx-target');
        if (target) { revealCard(document.querySelector(target)); }
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
                /* Facility changed → the auto-preview's prefix/counter did
                 * too. Refresh it if a mnemonic is already chosen. */
                var mnemonicSel = document.getElementById('projcodeMnemonic');
                if (mnemonicSel && mnemonicSel.value) {
                    htmx.trigger(mnemonicSel, 'change');
                }
                break;
            case 'createProjectCascadeRow':
                /* Parent-prefill replaced the whole cascade — the facility
                 * (and thus the projcode prefix) may have changed, so
                 * refresh the auto preview if a mnemonic is chosen. */
                var mnemonicSel2 = document.getElementById('projcodeMnemonic');
                if (mnemonicSel2 && mnemonicSel2.value) {
                    htmx.trigger(mnemonicSel2, 'change');
                }
                break;
            case 'projcodePreview': {
                /* keep hidden projcode in sync with the auto-preview */
                var mode = document.querySelector('[name="projcode_mode"]:checked');
                if (mode && mode.value === 'auto') {
                    var codeEl = e.target.querySelector('#projcodePreviewCode');
                    var val = codeEl ? codeEl.textContent.trim() : '';
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
        var mnemonicSel   = document.getElementById('projcodeMnemonic');
        if (mode === 'manual') {
            autoSection.style.display   = 'none';
            manualSection.style.display = '';
            hiddenCode.value = manualInput.value.toUpperCase();
            /* re-check availability of whatever is typed (or clear to —) */
            htmx.trigger(manualInput, 'input');
        } else {
            autoSection.style.display   = '';
            manualSection.style.display = 'none';
            hiddenCode.value = '';
            /* re-fetch the auto preview; its afterSwap handler re-syncs
             * the hidden field */
            if (mnemonicSel && mnemonicSel.value) {
                htmx.trigger(mnemonicSel, 'change');
            }
        }
    }

    registerAction('projcode-mode', function (radio) {
        applyProjcodeMode(radio.value);
    });

    /* ── Create Contract form: entry-mode toggle ──
     * Unlike projcode_mode, the two contract modes share every real input;
     * the only difference is whether the award-lookup affordance is shown.
     * Nothing to sync, so this just toggles visibility. */

    function applyContractMode(mode) {
        var lookupRow = document.getElementById('contractLookupRow');
        if (lookupRow) { lookupRow.style.display = (mode === 'lookup') ? '' : 'none'; }
    }

    registerAction('contract-mode', function (radio) {
        applyContractMode(radio.value);
    });

    function initCreateContractForm() {
        /* re-apply mode after a prefill swap or an error re-render */
        var current = document.querySelector('[name="contract_mode"]:checked');
        if (current) { applyContractMode(current.value); }
    }

    /* ── Create Contract form: "Find an award" ──
     *
     * The Search button and the input's Enter key are two ways into one
     * request. The input owns the hx-get (it carries the `q` the server
     * reads), so the button dispatches a custom event the input listens for
     * rather than issuing its own. */
    /* Enter-to-search for button-triggered search boxes.
     *
     * `data-enter-trigger="<event>"` on an input means "Enter here fires this
     * body-level htmx event", i.e. the same path its Search button uses.
     *
     * Deliberately NOT htmx's own `hx-trigger="keyup[key=='Enter']"`: htmx
     * compiles trigger filters with Function(), which `script-src 'self'`
     * forbids (webapp/utils/csp.py). It raises htmx:evalDisallowedError and
     * then **fails open** — every keystroke fires a request, silently turning
     * a deliberate button-triggered search into a typeahead. Measured with
     * Playwright: typing "turbulence" issued requests for `q=t` and
     * `q=turbulence` against a route that queries two public APIs.
     *
     * preventDefault is load-bearing for the create-modal instance, whose
     * input sits inside the Create Contract form: a bare Enter would submit
     * the form instead of searching. */
    document.addEventListener('keydown', function (evt) {
        if (evt.key !== 'Enter') { return; }
        var input = evt.target.closest && evt.target.closest('[data-enter-trigger]');
        if (!input) { return; }
        evt.preventDefault();
        htmx.trigger(document.body, input.dataset.enterTrigger);
    });

    registerAction('search-award', function () {
        var input = document.getElementById('createContractAwardSearch');
        if (input) { htmx.trigger(document.body, 'search-award'); }
    });

    /* Same shape for "Find Candidate Contracts" on /admin/contracts: the
     * input owns the hx-get (it carries `q`), so the button dispatches the
     * event the input listens for rather than issuing its own request. */
    registerAction('find-candidates', function () {
        var input = document.getElementById('candidateSearchInput');
        if (input) { htmx.trigger(document.body, 'find-candidates'); }
    });

    /* "Use" on an award search result.
     *
     * Writes the two parent-form lookup inputs, then fires the existing Fetch
     * button. Deliberately not an hx-vals chain straight to the lookup: the
     * operator has to SEE the number that was selected, and the eventual POST
     * reads that field — a chain would re-render #createContractFields while
     * leaving Contract Number visibly empty and posting nothing.
     *
     * Source is set for NSF only, and only when the server resolved an NSF
     * row. `contract_source_id` is a <select>, so guard on the option
     * existing rather than assigning a value the list does not carry. */
    registerAction('use-award', function (btn) {
        var number = document.getElementById('createContractNumber');
        if (number) { number.value = btn.dataset.awardNumber || ''; }

        var sourceId = btn.dataset.sourceId;
        var source = document.getElementById('createContractSource');
        if (source && sourceId &&
            source.querySelector('option[value="' + sourceId + '"]')) {
            source.value = sourceId;
        }

        var fetchBtn = document.getElementById('contractFetchAward');
        if (fetchBtn) { htmx.trigger(fetchBtn, 'click'); }
    });

    /* "search for them" on an unresolved award-source PI/monitor: seed the
     * picker's search box and let its own hx-trigger fire. A suggestion,
     * never a selection — the operator still has to click a result. */
    registerAction('search-suggested-person', function (btn) {
        var input = document.getElementById(btn.dataset.searchInput);
        if (!input) { return; }
        input.value = btn.dataset.searchTerm || '';
        input.focus();
        htmx.trigger(input, 'input');
    });

    /* ── Create Project form: lead-hint apply buttons ── */

    /* "use <CODE>" — select the suggested mnemonic and refresh the
     * auto-generate preview. Accepting a mnemonic suggestion implies the
     * auto-generate path, so flip the mode radio too if needed. */
    registerAction('apply-mnemonic', function (btn) {
        var sel = document.getElementById('projcodeMnemonic');
        if (!sel) { return; }
        var autoRadio = document.getElementById('projcodeModeAuto');
        if (autoRadio && !autoRadio.checked) {
            autoRadio.checked = true;
            applyProjcodeMode('auto');
        }
        sel.value = btn.dataset.mnemonicId;
        htmx.trigger(sel, 'change');
        /* The org hint's suggestion is now applied — re-fetch it so the
         * server's "already selected" rule clears the stale offer. */
        var orgWrap = document.getElementById('createProjectOrgWrap');
        if (orgWrap && document.getElementById('createProjectOrg_id')?.value) {
            htmx.trigger(orgWrap, 'fk:selected');
        }
    });

    /* "use as Organization" — pre-fill the Organization fk-picker the same
     * way a search-result click would (hidden id + badge + selected row),
     * including the fk:selected event so dependent hints (org → mnemonic
     * suggestion) fire exactly as for a manual pick. */
    registerAction('apply-org', function (btn) {
        var idEl    = document.getElementById('createProjectOrg_id');
        var badgeEl = document.getElementById('createProjectOrg_badge');
        var selEl   = document.getElementById('createProjectOrg_selected');
        if (!idEl) { return; }
        idEl.value = btn.dataset.orgId;
        if (badgeEl) { badgeEl.textContent = btn.dataset.orgLabel; }
        if (selEl)   { selEl.style.display = ''; }
        var picker = idEl.closest('.fk-picker');
        if (picker) {
            picker.dispatchEvent(new CustomEvent('fk:selected', {
                bubbles: true,
                detail: {id: btn.dataset.orgId, label: btn.dataset.orgLabel}
            }));
        }
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
        if (has(root, '#contractLookupRow')) { initCreateContractForm(); }
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
