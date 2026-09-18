// Copy-to-clipboard: any element carrying data-copy copies that value and
// toasts. CSP-safe (external file, no inline handler); one delegated listener
// serves every copy button. Feedback rides the same showToast event as the
// HX-Trigger channel (htmx-config.js). The app's first shared copy control.
document.body.addEventListener('click', function (evt) {
    var btn = evt.target.closest('[data-copy]');
    if (!btn) return;
    evt.preventDefault();
    var text = btn.getAttribute('data-copy') || '';

    function toast(ok) {
        document.body.dispatchEvent(new CustomEvent('showToast', { detail: {
            message: ok ? (btn.getAttribute('data-copy-done') || 'Copied to clipboard.')
                        : 'Could not copy — select the text and copy manually.',
            variant: ok ? 'success' : 'warning' } }));
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
            function () { toast(true); },
            function () { toast(false); });
    } else {
        toast(false);
    }
});
