/* Back and Forward walk the view dialogs, the way they walk pages.
 *
 * A shell marked [data-dialog-history] (the project, user and contract quick-views and the
 * expanded chart) gets a history entry naming it and the GET that filled its body. Back,
 * Forward, a reload and a return to the page make the page match the entry: close what is
 * open, reopen what the entry names. Its close button steps back, so a chain (chart, then
 * quick-view, then contract) unwinds a dialog at a time. Form dialogs take no entry: Back
 * leaves the page as it always has, and closing one opened from a view dialog returns to it.
 */
(function () {
    'use strict';

    var KEY = 'samDialog';
    var SHELL = '[data-dialog-history]';
    var opening = 0;     // show() called, shown not yet fired: a handoff is under way
    var syncing = 0;     // our own show/hide in progress: take no history action

    function entry() { return (history.state && history.state[KEY]) || null; }

    function openDialog() { return document.querySelector('.modal.show'); }

    function bodyOf(el) { return el.querySelector('.modal-body[id]'); }

    function stateWith(spec) {
        var state = {};
        state[KEY] = spec;
        return state;
    }

    function done() { setTimeout(function () { syncing = Math.max(0, syncing - 1); }, 0); }

    function reopen(spec) {
        var el = document.getElementById(spec.id);
        var body = el && bodyOf(el);
        if (!body || !spec.url) return;
        syncing++;
        el.addEventListener('shown.bs.modal', done, {once: true});
        bootstrap.Modal.getOrCreateInstance(el).show();
        htmx.ajax('GET', spec.url, {target: '#' + body.id, swap: 'innerHTML'});
    }

    // Make the page match the current entry.
    function sync() {
        var want = entry();
        var open = openDialog();
        if (want && open && open.id === want.id) return;
        if (!open) { if (want) reopen(want); return; }
        syncing++;
        open.addEventListener('hidden.bs.modal', function () {
            done();
            if (want) reopen(want);
        }, {once: true});
        bootstrap.Modal.getOrCreateInstance(open).hide();
    }

    // The GET that filled a view dialog's body is what a reopen replays.
    document.addEventListener('htmx:afterSwap', function (e) {
        var body = e.detail.target;
        var el = body && body.closest && body.closest(SHELL);
        var cfg = e.detail.requestConfig;
        var xhr = e.detail.xhr;
        if (!el || bodyOf(el) !== body || !cfg || String(cfg.verb).toLowerCase() !== 'get'
            || !xhr || xhr.status >= 300) return;
        el.dataset.historyUrl = e.detail.pathInfo.finalRequestPath || e.detail.pathInfo.requestPath;
        var cur = entry();
        if (cur && cur.id === el.id) history.replaceState(stateWith({id: el.id, url: el.dataset.historyUrl}), '');
    });

    document.addEventListener('show.bs.modal', function () { opening++; });

    document.addEventListener('shown.bs.modal', function (e) {
        opening = Math.max(0, opening - 1);
        if (syncing || !e.target.matches(SHELL)) return;
        var spec = {id: e.target.id, url: e.target.dataset.historyUrl || null};
        history.pushState(stateWith(spec), '');
    });

    document.addEventListener('hidden.bs.modal', function (e) {
        if (syncing) return;
        var closed = e.target;
        // Defer a tick: a handoff calls the next dialog's show() around this event.
        setTimeout(function () {
            if (opening || openDialog()) return;
            var cur = entry();
            if (cur && cur.id === closed.id) history.back();   // its close button steps back
            else if (cur) sync();   // a form opened from a view dialog closed: return to it
        }, 0);
    });

    // Registered before htmx binds window.onpopstate (at ready), so a step between our own
    // entries never triggers htmx's page-snapshot restore.
    var last = null;
    window.addEventListener('popstate', function (e) {
        var mine = !!(entry() || last);
        last = entry();
        if (mine) e.stopImmediatePropagation();
        sync();
    });
    window.addEventListener('pageshow', function () {
        last = entry();
        sync();
    });
    document.addEventListener('shown.bs.modal', function () { last = entry(); });
})();
