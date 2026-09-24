/* Progressive enhancement for /register. Everything here is enforced
   server-side (webapp/register/blueprint.py) or is a reading aid; with JS off
   every button stays enabled and the POST is validated.
   External file, delegated wiring: the CSP is nonce-free (no inline handlers). */
(function () {
    function each(list, fn) { Array.prototype.forEach.call(list, fn); }

    /* The gate: "Accept and continue" waits for the terms box. */
    function wireGate(form) {
        var button = form.querySelector('[data-gate-submit]');
        var boxes = form.querySelectorAll('[data-gate-check]');
        if (!button || !boxes.length) { return; }
        function sync() {
            button.disabled = !Array.prototype.every.call(boxes, function (b) { return b.checked; });
        }
        each(boxes, function (b) { b.addEventListener('change', sync); });
        holdUntilScrolled(form, boxes, sync);
        sync();
    }

    /* The terms box stays disabled until the end of the terms has been on
       screen. A reading aid, not a control: the server only checks accept=1.
       root=null clips by the panel's own scroll box and the page's alike. */
    function holdUntilScrolled(form, boxes, sync) {
        var end = form.querySelector('[data-eula-scroll] [data-eula-end]');
        if (!end || !('IntersectionObserver' in window)) { return; }
        var hint = form.querySelector('[data-eula-hint]');
        each(boxes, function (b) { b.disabled = true; });
        if (hint) { hint.hidden = false; }
        var observer = new IntersectionObserver(function (entries) {
            if (!entries.some(function (e) { return e.isIntersecting; })) { return; }
            observer.disconnect();
            each(boxes, function (b) { b.disabled = false; });
            if (hint) { hint.hidden = true; }
            sync();
        });
        observer.observe(end);
    }

    /* The form: the submit waits for the human-check widget's callback
       (named in templates/register/_human_check.html). */
    var humanPassed = false;
    function syncHumanCheck() {
        if (!document.querySelector('[data-human-check]')) { return; }
        each(document.querySelectorAll('[data-human-check-submit]'), function (b) {
            b.disabled = !humanPassed;
        });
    }
    window.samHumanCheckPassed = function () { humanPassed = true; syncHumanCheck(); };
    window.samHumanCheckReset = function () { humanPassed = false; syncHumanCheck(); };

    document.addEventListener('DOMContentLoaded', function () {
        each(document.querySelectorAll('[data-register-gate]'), wireGate);
        syncHumanCheck();
    });
})();
