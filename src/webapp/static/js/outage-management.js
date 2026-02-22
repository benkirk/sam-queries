/**
 * Outage Management Functions
 * Handles creating, editing, resolving, and deleting system outages.
 *
 * API v1 endpoints:
 *   POST   /api/v1/status/outage             - Create outage
 *   PATCH  /api/v1/status/outages/<id>       - Update outage
 *   DELETE /api/v1/status/outages/<id>       - Delete outage
 */

(function () {
    'use strict';

    const OUTAGE_BASE = `${window.SAMUtils.API_BASE}/status`;

    // =========================================================================
    // Helpers
    // =========================================================================

    /**
     * Convert a datetime string (ISO or null) to the value expected by
     * <input type="datetime-local"> (YYYY-MM-DDTHH:MM).
     */
    function toDatetimeLocal(isoStr) {
        if (!isoStr) return '';
        // Trim seconds and timezone to match datetime-local format
        return isoStr.substring(0, 16);
    }

    /**
     * Show a simple Bootstrap alert above the outage banner.
     * Fades out after 4 seconds.
     */
    function showOutageAlert(message, type) {
        const alertHtml = `
            <div class="alert alert-${type} alert-dismissible fade show outage-mgmt-alert" role="alert">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>`;
        // Insert before the outage banner (first .alert in page content), or at top of content
        const $content = $('.tab-content').first();
        $content.before(alertHtml);
        setTimeout(() => { $('.outage-mgmt-alert').alert('close'); }, 4000);
    }

    /**
     * Reload the page after a brief delay so the user can see the alert.
     */
    function reloadAfterDelay(ms) {
        setTimeout(() => window.location.reload(), ms || 1200);
    }

    // =========================================================================
    // Open modal helpers (called from onclick attributes in the template)
    // =========================================================================

    /**
     * Populate and show the Edit Outage modal.
     *
     * @param {number} id                   - outage_id
     * @param {string} status               - current status string
     * @param {string} severity             - current severity string
     * @param {string|null} description     - current description or null
     * @param {string|null} estimatedResolution - ISO string or null
     * @param {string} title                - current title
     */
    function openEditOutageModal(id, status, severity, description, estimatedResolution, title) {
        document.getElementById('editOutageId').value = id;
        document.getElementById('editTitle').value = title || '';
        document.getElementById('editStatus').value = status || 'investigating';
        document.getElementById('editSeverity').value = severity || 'minor';
        document.getElementById('editDescription').value = description || '';
        document.getElementById('editEstimatedResolution').value = toDatetimeLocal(estimatedResolution);
        bootstrap.Modal.getOrCreateInstance(document.getElementById('editOutageModal')).show();
    }

    /**
     * Populate and show the Delete Outage confirmation modal.
     *
     * @param {number} id    - outage_id
     * @param {string} title - outage title shown to user for confirmation
     */
    function openDeleteOutageModal(id, title) {
        document.getElementById('deleteOutageId').value = id;
        document.getElementById('deleteOutageTitle').textContent = title;
        bootstrap.Modal.getOrCreateInstance(document.getElementById('deleteOutageModal')).show();
    }

    /**
     * Quick-resolve an outage without opening the edit modal.
     * PATCHes status → 'resolved' immediately.
     *
     * @param {number} id - outage_id
     */
    function resolveOutage(id) {
        fetch(`${OUTAGE_BASE}/outages/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'resolved' })
        })
        .then(response => response.json().then(data => ({ ok: response.ok, data })))
        .then(({ ok, data }) => {
            if (!ok) throw new Error(data.error || 'Failed to resolve outage');
            showOutageAlert('<i class="fas fa-check"></i> Outage resolved.', 'success');
            reloadAfterDelay();
        })
        .catch(error => {
            console.error('Error resolving outage:', error);
            showOutageAlert(`<i class="fas fa-times"></i> Error: ${error.message}`, 'danger');
        });
    }

    // =========================================================================
    // Form submit handlers
    // =========================================================================

    /** Format a Date as the YYYY-MM-DDTHH:MM string datetime-local inputs expect. */
    function toLocalDatetimeInputValue(date) {
        const pad = n => String(n).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    document.addEventListener('DOMContentLoaded', function () {

        // Pre-fill Start Time with current local time each time the create modal opens
        const createModal = document.getElementById('createOutageModal');
        if (createModal) {
            createModal.addEventListener('show.bs.modal', function () {
                document.getElementById('createStartTime').value = toLocalDatetimeInputValue(new Date());
            });
        }

        // ------------------------------------------------------------------
        // Create Outage form
        // ------------------------------------------------------------------
        const createForm = document.getElementById('createOutageForm');
        if (createForm) {
            createForm.addEventListener('submit', function (e) {
                e.preventDefault();
                const btn = document.getElementById('createOutageSubmitBtn');
                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Reporting...';

                const fd = new FormData(this);
                const payload = {
                    system_name: fd.get('system_name'),
                    title: fd.get('title'),
                    severity: fd.get('severity'),
                };
                const component = fd.get('component');
                if (component) payload.component = component;
                const description = fd.get('description');
                if (description) payload.description = description;
                const startTime = fd.get('start_time');
                if (startTime) payload.start_time = new Date(startTime).toISOString();
                const estRes = fd.get('estimated_resolution');
                if (estRes) payload.estimated_resolution = new Date(estRes).toISOString();

                fetch(`${OUTAGE_BASE}/outage`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                })
                .then(response => response.json().then(data => ({ ok: response.ok, data })))
                .then(({ ok, data }) => {
                    if (!ok) throw new Error(data.error || 'Failed to create outage');
                    bootstrap.Modal.getInstance(document.getElementById('createOutageModal'))?.hide();
                    showOutageAlert('<i class="fas fa-exclamation-circle"></i> Outage reported.', 'warning');
                    reloadAfterDelay();
                })
                .catch(error => {
                    console.error('Error creating outage:', error);
                    showOutageAlert(`<i class="fas fa-times"></i> Error: ${error.message}`, 'danger');
                })
                .finally(() => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-exclamation-circle"></i> Report Outage';
                });
            });
        }

        // ------------------------------------------------------------------
        // Edit Outage form
        // ------------------------------------------------------------------
        const editForm = document.getElementById('editOutageForm');
        if (editForm) {
            editForm.addEventListener('submit', function (e) {
                e.preventDefault();
                const btn = document.getElementById('editOutageSubmitBtn');
                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

                const fd = new FormData(this);
                const outageId = fd.get('outage_id');
                const payload = {
                    title: fd.get('title'),
                    status: fd.get('status'),
                    severity: fd.get('severity'),
                    description: fd.get('description') || null,
                };
                const estRes = fd.get('estimated_resolution');
                payload.estimated_resolution = estRes ? new Date(estRes).toISOString() : null;

                fetch(`${OUTAGE_BASE}/outages/${outageId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                })
                .then(response => response.json().then(data => ({ ok: response.ok, data })))
                .then(({ ok, data }) => {
                    if (!ok) throw new Error(data.error || 'Failed to update outage');
                    bootstrap.Modal.getInstance(document.getElementById('editOutageModal'))?.hide();
                    showOutageAlert('<i class="fas fa-check"></i> Outage updated.', 'success');
                    reloadAfterDelay();
                })
                .catch(error => {
                    console.error('Error updating outage:', error);
                    showOutageAlert(`<i class="fas fa-times"></i> Error: ${error.message}`, 'danger');
                })
                .finally(() => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-save"></i> Save Changes';
                });
            });
        }

        // ------------------------------------------------------------------
        // Confirm Delete button
        // ------------------------------------------------------------------
        const confirmDeleteBtn = document.getElementById('confirmDeleteOutageBtn');
        if (confirmDeleteBtn) {
            confirmDeleteBtn.addEventListener('click', function () {
                const outageId = document.getElementById('deleteOutageId').value;
                this.disabled = true;
                this.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting...';

                fetch(`${OUTAGE_BASE}/outages/${outageId}`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' }
                })
                .then(response => response.json().then(data => ({ ok: response.ok, data })))
                .then(({ ok, data }) => {
                    if (!ok) throw new Error(data.error || 'Failed to delete outage');
                    bootstrap.Modal.getInstance(document.getElementById('deleteOutageModal'))?.hide();
                    showOutageAlert('<i class="fas fa-trash"></i> Outage deleted.', 'success');
                    reloadAfterDelay();
                })
                .catch(error => {
                    console.error('Error deleting outage:', error);
                    showOutageAlert(`<i class="fas fa-times"></i> Error: ${error.message}`, 'danger');
                })
                .finally(() => {
                    this.disabled = false;
                    this.innerHTML = '<i class="fas fa-trash"></i> Delete';
                });
            });
        }
    });

    // Expose globally for onclick= template attributes
    window.openEditOutageModal = openEditOutageModal;
    window.openDeleteOutageModal = openDeleteOutageModal;
    window.resolveOutage = resolveOutage;

})();
