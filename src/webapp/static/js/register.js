/* Progressive enhancement for the registration gate: keep "Accept and continue"
   disabled until both the terms and the human-check boxes are ticked. The gate
   is enforced server-side (webapp/register/blueprint.py) regardless, so this is
   nicety only — with JS off the button stays enabled and the POST is validated.
   External file, delegated wiring: the CSP is nonce-free (no inline handlers). */
(function () {
    function wire(form) {
        var button = form.querySelector('[data-gate-submit]');
        var boxes = form.querySelectorAll('[data-gate-check]');
        if (!button || !boxes.length) { return; }
        function sync() {
            var ok = Array.prototype.every.call(boxes, function (b) { return b.checked; });
            button.disabled = !ok;
        }
        Array.prototype.forEach.call(boxes, function (b) {
            b.addEventListener('change', sync);
        });
        sync();
    }
    document.addEventListener('DOMContentLoaded', function () {
        Array.prototype.forEach.call(
            document.querySelectorAll('[data-register-gate]'), wire);
    });
})();
