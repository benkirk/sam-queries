/* Modal-support behaviors extracted from inline fragment scripts (CSP:
 * script-src 'self'): system-status outage create/edit modals
 * (status/fragments/outage_modals.html) and the allocation-update
 * refresh hook (user/fragments/allocation_modals.html).
 */
(function () {
    'use strict';

    /* ── Outage modals ──
     * Time-zone strategy: datetime-local inputs are TZ-blind. The browser
     * TZ is captured into a hidden `tz` field at modal-show time so the
     * server can convert the submitted naive datetime into naive-UTC for
     * storage. When pre-filling the edit form, the server emits UTC ISO
     * with a 'Z' suffix; JS converts to the operator's local naive ISO
     * (the format datetime-local inputs require). */

    function pad(n) { return String(n).padStart(2, '0'); }

    /* Format a Date as a naive local ISO suitable for <input type="datetime-local">. */
    function toLocalNaiveISO(d) {
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
               'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    function browserTzName() {
        try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; }
        catch (e) { return ''; }
    }

    /* Pre-fill create start time + capture browser TZ (delegated:
     * Bootstrap modal events bubble). */
    document.addEventListener('show.bs.modal', function (e) {
        if (e.target.id !== 'createOutageModal') { return; }
        document.getElementById('createStartTime').value = toLocalNaiveISO(new Date());
        document.getElementById('createTz').value = browserTzName();
    });

    /* Populate the edit form from the clicked button's data attributes
     * (status/dashboard.html outage rows). */
    registerAction('outage-edit', function (btn) {
        var d = btn.dataset;
        document.getElementById('editOutageId').value = d.outageId;
        document.getElementById('editTitle').value = d.outageTitle || '';
        document.getElementById('editStatus').value = d.status || 'investigating';
        document.getElementById('editSeverity').value = d.severity || 'minor';
        document.getElementById('editDescription').value = d.description || '';
        document.getElementById('editTz').value = browserTzName();

        /* Server emits estimated_resolution as a UTC ISO with 'Z' suffix.
         * Parse that as a real instant, then format as operator-local naive. */
        var resInput = document.getElementById('editEstimatedResolution');
        if (d.estimatedResolution) {
            var when = new Date(d.estimatedResolution);
            resInput.value = isNaN(when.getTime()) ? '' : toLocalNaiveISO(when);
        } else {
            resInput.value = '';
        }

        /* Set the form's hx-post URL dynamically based on the outage ID. */
        var form = document.getElementById('editOutageForm');
        form.setAttribute('hx-post', '/status/htmx/outage/' + d.outageId + '/edit');
        htmx.process(form);
        bootstrap.Modal.getOrCreateInstance(document.getElementById('editOutageModal')).show();
    });

    /* ── Allocation modals ──
     * Refresh the project details modal (if open) after an allocation is
     * updated; the htmx_edit_allocation route sends HX-Trigger:
     * allocationUpdated on success. For the admin inline project card,
     * reload that container instead. */
    document.body.addEventListener('allocationUpdated', function () {
        var modal = document.getElementById('projectDetailsModal');
        if (modal && modal.classList.contains('show')) {
            var body = document.getElementById('projectDetailsModalBody');
            if (body.getAttribute('hx-get')) {
                htmx.trigger(body, 'allocationUpdated');
            } else {
                window.location.reload();
            }
            return;
        }
        var adminCard = document.getElementById('projectCardContainer');
        if (adminCard && adminCard.innerHTML.trim()) {
            var projEl = adminCard.querySelector('[data-projcode]');
            if (projEl) {
                htmx.ajax('GET', '/admin/project/' + projEl.dataset.projcode,
                          {target: adminCard, swap: 'innerHTML'});
            } else {
                window.location.reload();
            }
        }
    });
})();
