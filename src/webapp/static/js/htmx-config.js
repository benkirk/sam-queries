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
