/* Modal-support behaviors extracted from inline fragment scripts (CSP:
 * script-src 'self'): system-status outage create/edit modals
 * (status/fragments/outage_modals.html) and the allocation-update
 * refresh hook (user/fragments/allocation_modals.html).
 */
(function () {
    'use strict';

    /* A scaffold with a footer slot (modal_scaffold footer_id=): a body swap empties
     * the footer the previous body sent unless the new one re-sends its own out of
     * band. WARNING: mark before, clear after. Emptying it in beforeSwap would detach
     * a footer button that made the request (Back); htmx resolves out-of-band targets
     * from the requesting element's root, so the new title and footer would drop.
     * An out-of-band replacement is a fresh node without the mark. */
    function footerOf(target) {
        var id = target && target.dataset ? target.dataset.footerId : null;
        return id ? document.getElementById(id) : null;
    }
    document.body.addEventListener('htmx:beforeSwap', function (e) {
        var footer = e.detail.shouldSwap ? footerOf(e.detail.target) : null;
        if (footer) { footer.dataset.stale = '1'; }
    });
    document.body.addEventListener('htmx:afterSwap', function (e) {
        var footer = footerOf(e.detail.target);
        if (footer && footer.dataset.stale) {
            footer.replaceChildren();
            delete footer.dataset.stale;
        }
    });

    /* Outage modals
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
     * (status/base_status.html outage rows). */
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

    /* Allocation modals
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
            var baseUrl = adminCard.getAttribute('data-reload-url');
            if (projEl && baseUrl) {
                htmx.ajax('GET', baseUrl + projEl.dataset.projcode,
                          {target: adminCard, swap: 'innerHTML'});
            }
        }
    });

    /* Pinch-zoom scrollbar-compensation fix
     * Bootstrap sizes its "hidden scrollbar" pad as
     * abs(window.innerWidth - documentElement.clientWidth). Under pinch-zoom
     * innerWidth tracks the *visual* viewport while clientWidth stays at
     * layout width, so a 3x zoom on a 390px phone reads as a 260px
     * scrollbar and the opening modal is padded down to a few characters
     * wide (chart-link modals on the status pages were the visible victim).
     * A zoomed viewport has no classic scrollbar to compensate for — strip
     * the bogus padding as the modal opens. rAF: Bootstrap applies the
     * padding synchronously right after dispatching show.bs.modal. Its
     * hide-time restore still works — it resets to the saved original. */
    document.addEventListener('show.bs.modal', function (e) {
        if (!window.visualViewport || window.visualViewport.scale <= 1.01) { return; }
        var modal = e.target;
        requestAnimationFrame(function () {
            modal.style.paddingRight = '';
            modal.style.paddingLeft = '';
            document.body.style.paddingRight = '';
        });
    });
})();
