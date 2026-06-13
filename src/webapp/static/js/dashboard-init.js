/* Page-level behaviors for the allocations dashboard
 * (dashboards/allocations/dashboard.html) and the admin dashboard
 * (dashboards/admin/dashboard.html), extracted from their inline
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

    /* Toggle facility rows — shows/hides interleaved type rows in the
     * flat table. The clicked header row carries data-fac (NOT
     * data-fac-id: that attribute marks the child rows it toggles). */
    registerAction('alloc-toggle-facility', function (row) {
        var icon = row.querySelector('.expand-icon');
        var isExpanding = !icon.classList.contains('expanded');
        icon.classList.toggle('expanded');
        document.querySelectorAll('[data-fac-id="' + row.dataset.fac + '"]')
            .forEach(function (r) {
                if (r.classList.contains('project-details-row')) {
                    /* Always collapse project detail rows when facility toggles */
                    r.classList.remove('show');
                } else {
                    r.classList.toggle('show', isExpanding);
                    /* Type-row chevrons appear collapsed when facility expands */
                    if (isExpanding && r.classList.contains('alloc-type-row')) {
                        var typeIcon = r.querySelector('.expand-icon');
                        if (typeIcon) { typeIcon.classList.remove('expanded'); }
                    }
                }
            });
    });

    /* Toggle expandable detail rows; lazy-load contents on first expand */
    registerAction('alloc-toggle-details', function (row) {
        var icon = row.querySelector('.expand-icon');
        var detailsRow = document.getElementById(row.dataset.detailsId);
        icon.classList.toggle('expanded');
        if (detailsRow.classList.contains('show')) {
            detailsRow.classList.remove('show');
        } else {
            var td = detailsRow.querySelector('td[hx-get]');
            if (td && !detailsRow.dataset.loaded) {
                htmx.trigger(td, 'load-details');
                detailsRow.dataset.loaded = 'true';
            }
            detailsRow.classList.add('show');
        }
    });

    /* Create Adjustment form: show the intent hint matching the selected
     * adjustment type (fragments/create_adjustment_form_htmx.html) */
    registerAction('adj-intent-toggle', function (select) {
        document.querySelectorAll('#createAdjustmentFormContainer .adj-intent')
            .forEach(function (el) {
                el.style.display = (el.dataset.typeId === select.value) ? '' : 'none';
            });
    });

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

    /* Swap facility pie charts above the tabs to follow the active
     * resource tab (initial state set at load below) */
    function showPieForResource(resourceId) {
        ['.facility-pie-panel', '.facility-usage-pie-panel', '.facility-pace-panel']
            .forEach(function (sel) {
                document.querySelectorAll(sel).forEach(function (p) {
                    p.style.display = p.dataset.resource === resourceId ? '' : 'none';
                });
            });
    }

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
            htmx.ajax('GET', '/admin/project/' + link.dataset.projcode,
                      {target: '#projectCardContainer', swap: 'innerHTML'});
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
        /* allocations: pie panels follow the active resource tab */
        if (tab.closest('#resourceTabs')) {
            showPieForResource(tab.getAttribute('href').slice(1));
        }
    });

    /* admin: switch to the Projects tab whenever a project card is loaded
     * from any context (e.g. user card badges) */
    document.body.addEventListener('htmx:afterSwap', function (e) {
        if (e.detail.target && e.detail.target.id === 'projectCardContainer') {
            var projectsTabBtn = document.getElementById('projects-tab');
            if (projectsTabBtn && !projectsTabBtn.classList.contains('active')) {
                bootstrap.Tab.getOrCreateInstance(projectsTabBtn).show();
            }
            e.detail.target.scrollIntoView({behavior: 'smooth', block: 'start'});
        }
    });

    /* allocations: show the pie for the initially active resource tab
     * (script loads at end of body, DOM is parsed) */
    var activeResourceTab = document.querySelector('#resourceTabs .nav-link.active');
    if (activeResourceTab) {
        showPieForResource(activeResourceTab.getAttribute('href').slice(1));
    }
})();
