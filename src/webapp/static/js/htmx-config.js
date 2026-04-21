// Global HTMX configuration — error handling and network failure feedback.
// Loaded in base.html after htmx.org so these listeners are always active.

document.body.addEventListener('htmx:responseError', function(evt) {
    const xhr = evt.detail.xhr;
    const status = xhr.status;
    const msg = (status === 403) ? 'You do not have permission to perform this action.'
              : (status === 404) ? 'The requested resource was not found.'
              : (status >= 500)  ? 'A server error occurred. Please try again.'
              :                    'Request failed (' + status + ').';
    const toastEl = document.getElementById('htmxErrorToast');
    if (toastEl) {
        document.getElementById('htmxErrorToastBody').textContent = msg;
        bootstrap.Toast.getOrCreateInstance(toastEl).show();
    } else {
        alert(msg);
    }
});

document.body.addEventListener('htmx:sendError', function() {
    const toastEl = document.getElementById('htmxErrorToast');
    const msg = 'Network error: could not reach the server.';
    if (toastEl) {
        document.getElementById('htmxErrorToastBody').textContent = msg;
        bootstrap.Toast.getOrCreateInstance(toastEl).show();
    } else {
        alert(msg);
    }
});

// ── Modal management via HX-Trigger ──────────────────────────────────────────

// Close any currently visible Bootstrap modal
document.body.addEventListener('closeActiveModal', function() {
    document.querySelectorAll('.modal.show').forEach(function(el) {
        var m = bootstrap.Modal.getInstance(el);
        if (m) m.hide();
    });
});

// Close a specific Bootstrap modal by ID
document.body.addEventListener('closeModal', function(evt) {
    var el = document.getElementById(evt.detail.value);
    if (el) { var m = bootstrap.Modal.getInstance(el); if (m) m.hide(); }
});

// Reload an admin card section after a modal save, respecting the active_only checkbox.
// URL is read from the section's hx-get attribute — no hardcoded paths.
function _reloadAdminCard(sectionId, checkboxId) {
    var s = document.getElementById(sectionId); if (!s) return;
    var url = (s.getAttribute('hx-get') || '').split('?')[0]; if (!url) return;
    var cb = document.getElementById(checkboxId);
    // 300ms matches Bootstrap's modal close animation so the reload lands
    // after the modal has fully animated out of view.
    setTimeout(function() {
        htmx.ajax('GET', (cb && cb.checked) ? url + '?active_only=1' : url,
                  {target: '#' + sectionId, swap: 'innerHTML'});
    }, 300);
}

document.body.addEventListener('reloadFacilitiesCard', function() {
    _reloadAdminCard('facilitiesSection', 'facilitiesCardActiveOnly');
});
document.body.addEventListener('reloadOrganizationsCard', function() {
    _reloadAdminCard('organizationsSection', 'organizationsCardActiveOnly');
});
document.body.addEventListener('reloadResourcesCard', function() {
    _reloadAdminCard('resourcesSection', 'resourcesCardActiveOnly');
});

// Load the newly-created project card into #projectCardContainer so the admin
// can immediately see the result without re-searching.
document.body.addEventListener('loadNewProject', function(evt) {
    var projcode = evt.detail.value;
    var container = document.getElementById('projectCardContainer');
    var baseUrl = container ? container.getAttribute('data-reload-url') : null;
    if (baseUrl && projcode) {
        setTimeout(function() {
            htmx.ajax('GET', baseUrl + projcode,
                      {target: '#projectCardContainer', swap: 'innerHTML'});
        }, 300);
    }
});

// Update the Project Details modal title from the projcode in the loaded response.
// The project_details_modal route fires this via HX-Trigger on every response.
document.body.addEventListener('setModalTitle', function(evt) {
    var el = document.getElementById('projectDetailsModalTitle');
    if (el) el.textContent = evt.detail.value;
});

// Reload the user card after an exemption change.
// Base URL is read from a data-reload-url attribute on the container.
document.body.addEventListener('reloadUserCard', function(evt) {
    var username = evt.detail.value;
    var container = document.getElementById('userCardContainer');
    var baseUrl = container ? container.getAttribute('data-reload-url') : null;
    if (baseUrl && username) {
        setTimeout(function() {
            htmx.ajax('GET', baseUrl + username,
                      {target: '#userCardContainer', swap: 'innerHTML'});
        }, 300);
    }
});

// ── Styled confirmation modal ────────────────────────────────────────────────
// Replaces the browser-native confirm() dialog that HTMX uses for hx-confirm.
// Opens a singleton Bootstrap modal (rendered in dashboards/base.html via the
// confirm_modal macro) and routes the user's answer back to HTMX.
//
// Per-trigger customization via data-attributes on the element carrying
// hx-confirm:
//   data-confirm-variant="danger|warning|info"   (default: "danger")
//   data-confirm-label="Deactivate"              (default: "Confirm")
//   data-confirm-title="Deactivate projects"     (default: "Confirm action")
//   data-confirm-body="#someElementId"           (default: plain hx-confirm text)
//
// Non-HTMX callers use window.samConfirm({title, message, variant, label,
// onConfirm}) to reach the same modal (e.g. impersonation form submits).

(function() {
    var VARIANTS = {
        danger:  { header: 'bg-danger text-white',  close: 'btn-close-white', icon: 'fa-exclamation-triangle', btn: 'btn-danger' },
        warning: { header: 'bg-warning',            close: '',                icon: 'fa-exclamation-circle',   btn: 'btn-warning' },
        info:    { header: 'bg-info text-white',    close: 'btn-close-white', icon: 'fa-info-circle',          btn: 'btn-primary' }
    };
    var ALL_HEADER_CLASSES = 'bg-danger bg-warning bg-info text-white';
    var ALL_BTN_CLASSES = 'btn-danger btn-warning btn-primary';
    var ALL_ICON_CLASSES = 'fa-exclamation-triangle fa-exclamation-circle fa-info-circle';

    function _openSamConfirm(opts) {
        var modalEl = document.getElementById('samConfirmModal');
        if (!modalEl) { return false; }  // fall through to caller fallback

        var header = document.getElementById('samConfirmModalHeader');
        var closeBtn = document.getElementById('samConfirmModalClose');
        var icon = document.getElementById('samConfirmModalIcon');
        var titleEl = document.getElementById('samConfirmModalTitle');
        var bodyEl = document.getElementById('samConfirmModalBody');
        var confirmBtn = document.getElementById('samConfirmModalConfirm');

        var v = VARIANTS[opts.variant] || VARIANTS.danger;

        header.classList.remove.apply(header.classList, ALL_HEADER_CLASSES.split(' '));
        v.header.split(' ').forEach(function(c) { header.classList.add(c); });

        closeBtn.classList.remove('btn-close-white');
        if (v.close) { closeBtn.classList.add(v.close); }

        icon.classList.remove.apply(icon.classList, ALL_ICON_CLASSES.split(' '));
        icon.classList.add(v.icon);

        titleEl.textContent = opts.title || 'Confirm action';

        if (opts.bodyHtml) {
            bodyEl.innerHTML = opts.bodyHtml;
        } else {
            bodyEl.textContent = opts.message || '';
        }

        confirmBtn.classList.remove.apply(confirmBtn.classList, ALL_BTN_CLASSES.split(' '));
        confirmBtn.classList.add(v.btn);
        confirmBtn.textContent = opts.label || 'Confirm';

        // Replace the confirm button to drop any prior click handler.
        var fresh = confirmBtn.cloneNode(true);
        confirmBtn.parentNode.replaceChild(fresh, confirmBtn);
        fresh.addEventListener('click', function() {
            bootstrap.Modal.getInstance(modalEl).hide();
            if (typeof opts.onConfirm === 'function') { opts.onConfirm(); }
        });

        bootstrap.Modal.getOrCreateInstance(modalEl).show();
        return true;
    }

    // Intercept HTMX's native confirm flow.
    document.body.addEventListener('htmx:confirm', function(evt) {
        var question = evt.detail.question;
        if (!question) { return; }  // no hx-confirm on this request; let HTMX proceed
        evt.preventDefault();

        var el = evt.detail.elt;
        var bodyHtml = null;
        var bodySelector = el && el.getAttribute('data-confirm-body');
        if (bodySelector) {
            var source = document.querySelector(bodySelector);
            if (source) {
                bodyHtml = source.tagName === 'TEMPLATE' ? source.innerHTML : source.innerHTML;
            }
        }

        var opened = _openSamConfirm({
            title:   el && el.getAttribute('data-confirm-title'),
            message: question,
            bodyHtml: bodyHtml,
            variant: (el && el.getAttribute('data-confirm-variant')) || 'danger',
            label:   el && el.getAttribute('data-confirm-label'),
            onConfirm: function() { evt.detail.issueRequest(true); }
        });

        if (!opened && window.confirm(question)) {
            evt.detail.issueRequest(true);
        }
    });

    window.samConfirm = function(opts) {
        var opened = _openSamConfirm(opts || {});
        if (!opened && window.confirm((opts && opts.message) || 'Are you sure?')) {
            if (opts && typeof opts.onConfirm === 'function') { opts.onConfirm(); }
        }
    };
})();
