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
