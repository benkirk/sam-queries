/* Page-level behaviors for the allocations dashboard
 * (dashboards/allocations/projects.html et al.) and the admin dashboard
 * (dashboards/admin/projects.html et al.), extracted from their inline
 * <script> blocks (CSP: script-src 'self').
 *
 * Loaded from dashboards/base.html on every page; everything below is
 * either a registered action (fires only where the data-action markup
 * exists) or a delegated listener guarded by page-specific element ids,
 * so it is inert elsewhere. NOTE: the allocations dashboard response is
 * cached per-user in Redis (user_aware_cache_key) — behavior must live
 * here, not in the page, so cached HTML stays valid.
 */
(function () {
    'use strict';

    /* ================= Allocations dashboard ================= */

    /* After a successful "Create Adjustment" POST (HX-Trigger event),
     * reload the adjustments table fragment, carrying the current filter
     * form so the view stays consistent with what the user was looking
     * at. The fragment URL rides data-refresh-url on the target. */
    document.body.addEventListener('refreshAdjustmentsTab', function () {
        var target = document.getElementById('alloc-adjustments-fragment');
        if (!target) { return; }
        /* 300ms matches Bootstrap's modal close animation so the reload
         * lands after the modal has fully animated out of view. */
        setTimeout(function () {
            htmx.ajax('GET', target.dataset.refreshUrl,
                      {target: '#alloc-adjustments-fragment', swap: 'innerHTML',
                       source: '#adj-filters'});
        }, 300);
    });

    /* After a replay POST (HX-Trigger event), reload the XRAS action table
     * carrying the current filter form. No setTimeout here — unlike the
     * adjustment create, replay fires from a button in the table itself, so
     * there is no modal animation to wait out. The pending-activation card
     * listens for the same event via hx-trigger and reloads itself. */
    document.body.addEventListener('refreshXrasTab', function () {
        var target = document.getElementById('alloc-xras-fragment');
        if (!target) { return; }
        htmx.ajax('GET', target.dataset.refreshUrl,
                  {target: '#alloc-xras-fragment', swap: 'innerHTML',
                   source: '#xras-filters'});
    });

    /* Expansion follows the resource tab: a facility open under Casper is open
     * under Derecho too, and the Share/Pace pill matches. Facilities missing
     * from the previous pane keep their own state; type rows (lazy project
     * tables) are not mirrored. */
    function mirrorResourcePane(from, to) {
        if (!from || !to || !window.bootstrap) { return; }
        var open = {};
        from.querySelectorAll('tbody[data-alloc-facility]').forEach(function (b) {
            open[b.dataset.allocFacility] = b.classList.contains('show');
        });
        to.querySelectorAll('tbody[data-alloc-facility]').forEach(function (b) {
            var want = open[b.dataset.allocFacility];
            if (want === undefined || want === b.classList.contains('show')) { return; }
            var c = bootstrap.Collapse.getOrCreateInstance(b, {toggle: false});
            if (want) { c.show(); } else { c.hide(); }
        });
        from.querySelectorAll('.alloc-view-pills .nav-link.active').forEach(function (pill) {
            var match = to.querySelector(
                '.alloc-view-pills [data-alloc-view="' + pill.dataset.allocView + '"]');
            if (match && !match.classList.contains('active')) {
                bootstrap.Tab.getOrCreateInstance(match).show();
            }
        });
    }

    /* Allocation calendar: once visible, scroll so the view-at line sits a third
     * of the way across the track. A pane loaded while hidden waits for a tab show. */
    function focusCalendars() {
        document.querySelectorAll('.cal-scroll[data-cal-focus]:not([data-cal-focused])').forEach(function (s) {
            if (!s.offsetParent) { return; }
            var label = s.querySelector('th.cal-label');
            var labelW = label ? label.offsetWidth : 0;
            var track = s.scrollWidth - labelW;
            s.scrollLeft = track * parseFloat(s.dataset.calFocus) / 100 - (s.clientWidth - labelW) / 3;
            s.dataset.calFocused = '1';
        });
    }
    /* The Used | Burn pills re-render the calendar: carry its open type groups (data-no-persist,
     * so the collapse restore skips them) and its scroll across the swap. Registered first, so
     * focusCalendars sees the carried scroll as already focused. */
    var calCarry = {};
    function isCalendar(el) { return el && el.matches && el.matches('.alloc-calendar[id]'); }
    document.body.addEventListener('htmx:beforeSwap', function (e) {
        var old = e.detail.target;
        if (!isCalendar(old)) { return; }
        var s = old.querySelector('.cal-scroll');
        calCarry[old.id] = {
            open: Array.prototype.map.call(old.querySelectorAll('tr.collapse.show[id]'),
                                           function (r) { return r.id; }),
            left: s ? s.scrollLeft : null
        };
    });
    document.body.addEventListener('htmx:afterSettle', function (e) {
        var cal = e.detail.elt;
        var carry = isCalendar(cal) && calCarry[cal.id];
        if (!carry) { return; }
        delete calCarry[cal.id];
        var s = cal.querySelector('.cal-scroll');
        if (s && carry.left !== null) { s.scrollLeft = carry.left; s.dataset.calFocused = '1'; }
        carry.open.forEach(function (id) {
            var row = document.getElementById(id);
            if (row) { bootstrap.Collapse.getOrCreateInstance(row, { toggle: false }).show(); }
        });
    });
    document.body.addEventListener('htmx:afterSettle', focusCalendars);
    document.addEventListener('shown.bs.tab', focusCalendars);

    /* ================= Admin dashboard ================= */

    /* Expirations: load a tab's content via htmx */
    function loadExpirationsView(view) {
        var container = document.getElementById(view + '-container');
        if (!container) { return; }
        var params = new URLSearchParams(
            new FormData(document.getElementById('expirations-filters-form')));
        params.set('view', view);
        htmx.ajax('GET', '/admin/expirations?' + params.toString(),
                  {target: container, swap: 'innerHTML'});
    }

    function activeExpirationsView() {
        var activeTab = document.querySelector('#expirations-tabs .nav-link.active');
        return activeTab ? activeTab.dataset.view : 'upcoming';
    }

    /* Apply Filters — reload the active tab */
    registerAction('expirations-reload', function () {
        loadExpirationsView(activeExpirationsView());
    });

    /* Clear filters — reset the form, then reload the active tab so the
     * expirations view reflects the cleared (default) filters. */
    registerAction('expirations-clear', function () {
        var form = document.getElementById('expirations-filters-form');
        if (form) { form.reset(); }
        loadExpirationsView(activeExpirationsView());
    });

    /* Export CSV of the active tab, carrying the filter form */
    registerAction('expirations-export-csv', function () {
        var params = new URLSearchParams(
            new FormData(document.getElementById('expirations-filters-form')));
        params.set('export_type', activeExpirationsView());
        window.open('/admin/expirations/export?' + params.toString(), '_blank');
    });

    /* Impersonate buttons + project-code links inside dynamically loaded
     * expiration content (delegated; samConfirm is htmx-config.js) */
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.impersonate-user-btn');
        if (btn && btn.dataset.username) {
            var username = btn.dataset.username;
            samConfirm({
                title: 'Impersonate user',
                message: 'Impersonate user ' + username + '?',
                variant: 'warning',
                label: 'Impersonate',
                onConfirm: function () {
                    document.getElementById('selectedUsernameImpersonate').value = username;
                    document.getElementById('impersonateUserForm').submit();
                }
            });
        }

        var link = e.target.closest('.project-code-link');
        if (link && link.dataset.projcode) {
            e.preventDefault();
            var card = document.getElementById('projectCardContainer');
            htmx.ajax('GET', '/admin/project/' + link.dataset.projcode,
                      {target: '#projectCardContainer', swap: 'innerHTML'})
                .then(function () { revealCard(card); });
        }
    });

    /* ================= Delegated Bootstrap events ================= */

    /* Bootstrap 5 dispatches its lifecycle events as bubbling DOM events,
     * so page-specific tab/collapse wiring can be delegated and guarded
     * by element ids instead of bound per-element at load. */

    document.addEventListener('shown.bs.collapse', function (e) {
        /* admin: load "upcoming" on first expand of the expirations section */
        if (e.target.id === 'expirations-section' && !e.target.dataset.loaded) {
            loadExpirationsView('upcoming');
            e.target.dataset.loaded = 'true';
        }
    });

    document.addEventListener('shown.bs.tab', function (e) {
        var tab = e.target;
        /* admin: lazy-load expiration tab content on first switch */
        if (tab.closest('#expirations-tabs') && tab.dataset.view) {
            var container = document.getElementById(tab.dataset.view + '-container');
            if (container && !container.dataset.loaded) {
                loadExpirationsView(tab.dataset.view);
                container.dataset.loaded = 'true';
            }
        }
        /* allocations: the new pane mirrors the one just left */
        if (tab.closest('#resourceTabs') && e.relatedTarget) {
            mirrorResourcePane(
                document.querySelector(e.relatedTarget.getAttribute('href')),
                document.querySelector(tab.getAttribute('href')));
        }
    });

    /* admin: switch to the Projects tab whenever a project card is loaded
     * from any context (e.g. user card badges).
     *
     * Scrolling is NOT done here: this fires for every swap into the
     * container, including the in-place reloads after an allocation edit
     * (modals.js) — those must not yank the page back to the card top.
     * The paths where the user asked for a different card call
     * revealCard() themselves (form-helpers.js on a search hit,
     * htmx-config.js after a create, data-reveal-on-load below). */
    document.body.addEventListener('htmx:afterSwap', function (e) {
        if (e.detail.target && e.detail.target.id === 'projectCardContainer') {
            var projectsTabBtn = document.getElementById('projects-tab');
            if (projectsTabBtn && !projectsTabBtn.classList.contains('active')) {
                bootstrap.Tab.getOrCreateInstance(projectsTabBtn).show();
            }
        }
        /* ?projcode= deep links auto-load a card on page load — reveal it
         * once, then drop the flag so reloads of the same container stay
         * put. */
        if (e.detail.target && e.detail.target.hasAttribute('data-reveal-on-load')) {
            e.detail.target.removeAttribute('data-reveal-on-load');
            revealCard(e.detail.target);
        }
    });

    /* ================= Collapsible filter panels ================= */

    /* Filter panels ship expanded (class="collapse show") but default to
     * collapsed on phones, where they can fill 1.5 screens. Markup can't be
     * viewport-conditional, so the default is applied here: strip .show
     * before first paint (script runs at end of body) and after htmx swaps
     * that re-render a panel. Re-collapsing after a filter *submit* is
     * deliberate — on a phone the results matter more than the form.
     * The panels carry data-no-persist so nav-view-persistence.js does not
     * re-expand them from a stale localStorage entry. */
    function collapseFilterPanels(root) {
        if (!window.matchMedia('(max-width: 767.98px)').matches) { return; }
        root.querySelectorAll('.filter-fields-collapse.show').forEach(function (el) {
            el.classList.remove('show');
            var toggle = document.querySelector('[data-bs-target="#' + el.id + '"]');
            if (toggle) { toggle.setAttribute('aria-expanded', 'false'); }
        });
    }

    collapseFilterPanels(document);
    document.body.addEventListener('htmx:afterSettle', function (e) {
        if (e.detail.elt) { collapseFilterPanels(e.detail.elt); }
    });
})();
