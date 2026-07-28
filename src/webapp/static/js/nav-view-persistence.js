/**
 * Navigation & View State Persistence
 *
 * Covers UI state stored client-side:
 *
 *  1. Tab selections  — key: "tab:<tablistId>"    value: "#pane-id"
 *  2. Collapse state  — key: "collapse:<id>"      value: "1"
 *  3. Chart selectors — key: "chart:<id>"         value: JSON params
 *     …and settings shared across surfaces (the owner dimension, each
 *     metric family) in one bucket: "chart:__shared__"
 *  4. Scroll position — key: "nav:scroll:<path>"  (sessionStorage, one-shot)
 *
 * The job-history period pills (5) reuse the chart-selector storage under
 * key "days", plus a card-level fan-out so per-machine subtabs agree.
 *
 * Tab / collapse handling works for both initial page load and after
 * HTMX swaps or Bootstrap modal close events.
 */
(function () {
    'use strict';

    // ── Tab persistence ──────────────────────────────────────────────────────

    var STORAGE_PREFIX = 'tab:';

    /** Return the pane selector ("#id") for a tab trigger element. */
    function getPaneSelector(trigger) {
        return trigger.dataset.bsTarget || trigger.getAttribute('href') || null;
    }

    /** Persist the active tab for a tablist whenever a tab is shown. */
    function saveActiveTab(event) {
        var trigger = event.target;
        var tablist = trigger.closest('[role="tablist"]');
        if (!tablist || !tablist.id) return;
        var paneSelector = getPaneSelector(trigger);
        if (paneSelector && paneSelector.startsWith('#')) {
            try {
                localStorage.setItem(STORAGE_PREFIX + tablist.id, paneSelector);
            } catch (_) {}  // private browsing / storage full — fail silently
        }
    }

    /** Restore saved tab selections within a root element (document or swapped fragment). */
    function restoreTabs(root) {
        root.querySelectorAll('[role="tablist"][id]').forEach(function (tablist) {
            var saved = null;
            try { saved = localStorage.getItem(STORAGE_PREFIX + tablist.id); } catch (_) {}
            if (!saved) return;

            var trigger = tablist.querySelector(
                '[data-bs-target="' + saved + '"], [href="' + saved + '"]'
            );
            if (!trigger) return;  // saved tab no longer in DOM — leave default active

            if (!trigger.classList.contains('active')) {
                try { new bootstrap.Tab(trigger).show(); } catch (_) {}
            }
        });
    }

    // 1. On page load: restore all tab groups in initial DOM
    document.addEventListener('DOMContentLoaded', function () {
        restoreTabs(document);
    });

    // 2. Save tab selections as they change
    document.addEventListener('shown.bs.tab', saveActiveTab);

    // 3. After HTMX swaps: restore tabs in the newly-settled fragment
    document.addEventListener('htmx:afterSettle', function (event) {
        restoreTabs(event.detail.elt);
    });

    // 4. After modal close: restore tabs reset by Bootstrap
    document.addEventListener('hidden.bs.modal', function (event) {
        restoreTabs(event.target);
    });

    // ── Collapse (expanded row) persistence ──────────────────────────────────

    var COLLAPSE_PREFIX = 'collapse:';

    // Collapses with `data-no-persist` opt out of restore-on-reload — for
    // drawers whose contents are expensive to fetch (e.g. the jobs plugin
    // drill-down), where re-firing the underlying hx-trigger on every
    // page load is the wrong default. Honored by all three handlers so
    // existing localStorage entries stay dormant and no new ones are
    // written.

    /** Save expanded state when a collapse opens. */
    function saveCollapse(event) {
        var el = event.target;
        if (!el.id || el.hasAttribute('data-no-persist')) return;
        try { localStorage.setItem(COLLAPSE_PREFIX + el.id, '1'); } catch (_) {}
    }

    /** Clear saved state when a collapse closes. */
    function clearCollapse(event) {
        var el = event.target;
        if (!el.id || el.hasAttribute('data-no-persist')) return;
        try { localStorage.removeItem(COLLAPSE_PREFIX + el.id); } catch (_) {}
    }

    /** Restore saved collapse state within a root element. */
    function restoreCollapses(root) {
        root.querySelectorAll('.collapse[id]').forEach(function (el) {
            if (el.hasAttribute('data-no-persist')) return;
            var saved = null;
            try { saved = localStorage.getItem(COLLAPSE_PREFIX + el.id); } catch (_) {}
            if (!saved) return;
            if (!el.classList.contains('show')) {
                try { new bootstrap.Collapse(el, { toggle: false }).show(); } catch (_) {}
            }
        });
    }

    // 5. On page load: restore all collapse state in initial DOM
    document.addEventListener('DOMContentLoaded', function () {
        restoreCollapses(document);
    });

    // 6. Save / clear as collapses open and close
    document.addEventListener('show.bs.collapse',  saveCollapse);
    document.addEventListener('hide.bs.collapse',  clearCollapse);

    // 7. After HTMX swap: restore within the newly-settled fragment
    document.addEventListener('htmx:afterSettle', function (event) {
        restoreCollapses(event.detail.elt);
    });

    // 8. After modal close: restore within the modal
    document.addEventListener('hidden.bs.modal', function (event) {
        restoreCollapses(event.target);
    });

    // (The referrer-based `.back-link` machinery that used to live here was
    // removed when detail pages moved to server-derived breadcrumbs —
    // see dashboards/fragments/breadcrumbs.html and webapp/utils/nav.py.)

    // ── Chart selector persistence ───────────────────────────────────────────
    //
    // Chart fragments with multiple HTMX btn-group selectors (group_by,
    // state, metric, rank_by, …) lose their selection on a full page
    // reload — the loader's hx-get defaults run again. We persist the
    // live URL params to localStorage and replay them on the next
    // configRequest. Which params, and where they live, is declared by
    // the template — co-locating the key list with the buttons that own
    // it avoids JS-side hardcoding, so adding a selector is a one-file
    // change. Server-side validation in the route is the safety net for
    // stale combinations.
    //
    // Two channels, both whitespace-separated declarations:
    //
    //   data-chart-persist-id + data-chart-persist-keys="state rank_by"
    //       a per-chart bucket keyed by the chart's stable dom id — for
    //       settings that only mean something on that one chart.
    //
    //   data-chart-persist-shared="group_by metric:jobs"
    //       ONE app-wide bucket, for settings whose vocabulary is shared
    //       across surfaces. A bare name is a genuinely global concept:
    //       `group_by` is user|project on the queue-load chart and on the
    //       job histograms alike, so choosing Project in one place
    //       pre-selects it in the other. `param:family` sends `param` on
    //       the wire but stores it under the family-scoped key, which is
    //       what keeps a homonym from cross-contaminating: `metric` means
    //       jobs|cpu_hours|gpu_hours on the jobs card, jobs|cores|gpus|
    //       nodes on the queue chart and charges|jobs|core_hours on the
    //       usage chart, so each family remembers its own.
    //
    // The rule when adding one: share a key across surfaces only when the
    // VALUE vocabularies are identical; otherwise give it a family.
    //
    // Saves MERGE into the bucket rather than replacing it — several
    // elements declare different key subsets of the same bucket, and a
    // wholesale write would drop whatever this request didn't carry.

    var CHART_PREFIX = 'chart:';
    var SHARED_BUCKET = '__shared__';

    /** Parse a declaration into {param, key} pairs; `metric:jobs` travels
     *  as `metric` and is stored under `metric:jobs`. */
    function persistSpecs(raw) {
        if (!raw) return [];
        return raw.split(/\s+/).filter(Boolean).map(function (spec) {
            var i = spec.indexOf(':');
            return { param: i === -1 ? spec : spec.slice(0, i), key: spec };
        });
    }

    function chartSpecs(elt) { return persistSpecs(elt.dataset.chartPersistKeys); }
    function sharedSpecs(elt) { return persistSpecs(elt.dataset.chartPersistShared); }

    function readBucket(id) {
        var raw = null;
        try { raw = localStorage.getItem(CHART_PREFIX + id); } catch (_) { return null; }
        if (!raw) return null;
        var saved;
        try { saved = JSON.parse(raw); } catch (_) { return null; }
        return (saved && typeof saved === 'object') ? saved : null;
    }

    /** Merge updates into a bucket, leaving keys this write didn't mention. */
    function mergeBucket(id, updates) {
        var bucket = readBucket(id) || {};
        Object.keys(updates).forEach(function (k) { bucket[k] = updates[k]; });
        try {
            localStorage.setItem(CHART_PREFIX + id, JSON.stringify(bucket));
        } catch (_) {}
    }

    /** Replay saved values into an outgoing request. */
    function injectSaved(detail, id, specs) {
        if (!specs.length) return;
        var saved = readBucket(id);
        if (!saved) return;

        var injected = [];
        specs.forEach(function (s) {
            if (s.key in saved) {
                detail.parameters[s.param] = saved[s.key];
                injected.push(s.param);
            }
        });
        if (!injected.length) return;

        // htmx appends `parameters` to `path` for GET requests, so any param
        // already encoded in the hx-get URL would produce a duplicate query
        // key (?metric=jobs&metric=cores). Werkzeug's request.args.get returns
        // the FIRST value, which silently defeats the override. Strip ours
        // from `path` so the injected parameters become authoritative.
        var path = detail.path;
        if (path && path.indexOf('?') !== -1) {
            var parts = path.split('?');
            var qs;
            try { qs = new URLSearchParams(parts[1]); } catch (_) { return; }
            injected.forEach(function (p) { qs.delete(p); });
            var rest = qs.toString();
            detail.path = rest ? parts[0] + '?' + rest : parts[0];
        }
    }

    /** Read saved selections and override hx-get parameters. Only applies to
     *  elements carrying a marker THEMSELVES — i.e. the loader with
     *  hx-trigger="load", or a tab button that fetches a panel. Selector
     *  buttons inside a persisted fragment have an ancestor with the marker,
     *  but `elt.matches` ensures we don't clobber a deliberate click. */
    document.addEventListener('htmx:configRequest', function (event) {
        var elt = event.detail.elt;
        if (!elt || !elt.matches) return;
        if (elt.matches('[data-chart-persist-id]')) {
            injectSaved(event.detail, elt.dataset.chartPersistId, chartSpecs(elt));
        }
        if (elt.matches('[data-chart-persist-shared]')) {
            injectSaved(event.detail, SHARED_BUCKET, sharedSpecs(elt));
        }
    });

    function saveFromUrl(url, id, specs) {
        if (!specs.length) return;
        var qs;
        try { qs = new URL(url, window.location.origin).searchParams; } catch (_) { return; }

        var saved = {};
        specs.forEach(function (s) {
            if (qs.has(s.param)) saved[s.key] = qs.get(s.param);
        });
        if (Object.keys(saved).length) mergeBucket(id, saved);
    }

    /** After a fragment swaps in, capture the resolved request URL — the
     *  source of truth for which selector combination is now showing, and
     *  cheaper than reading button DOM classes. htmx reports the SETTLED
     *  element here (the swapped-in wrapper, or the target container for an
     *  innerHTML swap) rather than the button that asked, which is what lets
     *  a selector click persist with no click handler of its own. */
    document.addEventListener('htmx:afterSettle', function (event) {
        var elt = event.detail.elt;
        if (!elt) return;
        var url = event.detail.xhr && event.detail.xhr.responseURL;
        if (!url) return;

        function nearest(selector) {
            if (elt.matches && elt.matches(selector)) return elt;
            return elt.querySelector ? elt.querySelector(selector) : null;
        }

        var chartEl = nearest('[data-chart-persist-id]');
        if (chartEl) {
            saveFromUrl(url, chartEl.dataset.chartPersistId, chartSpecs(chartEl));
        }
        var sharedEl = nearest('[data-chart-persist-shared]');
        if (sharedEl) {
            saveFromUrl(url, SHARED_BUCKET, sharedSpecs(sharedEl));
        }
    });

    // ── Job-history period pills ─────────────────────────────────────────────
    //
    // The jobs card's 30d/60d/90d/1yr pills re-render the whole card shell,
    // because each of its six panels bakes the window into its own hx-get
    // URL at render time. Markup contract (jobs_card.html):
    //
    //   [data-jobs-days-card]    card wrapper; also carries the
    //                            data-chart-persist-* pair above (key
    //                            "days") and data-jobs-card-url
    //   [data-jobs-days-pills]   the pill group inside it
    //   [data-days-value]        each pill button
    //   [data-jobs-explore-link] "Open full view", whose ?days= tracks them
    //
    // Cards sharing a persist id — the per-machine subtabs on My Jobs and
    // Status → Job History — stay in lockstep: a click saves the window and
    // re-renders its siblings. That fan-out goes through htmx.ajax() rather
    // than hx-* attributes on the wrapper, because hx-target/hx-swap are
    // inherited: declaring them on a card would hijack every descendant
    // request that doesn't name its own target.

    var DAYS_KEY = 'days';

    /** The stored window for a persist id, as a string, or null. */
    function savedDays(persistId) {
        var saved = readBucket(persistId);
        return (saved && saved[DAYS_KEY]) ? String(saved[DAYS_KEY]) : null;
    }

    /** Paint *days* as the active pill and point the explorer link at it. */
    function syncDaysCard(card, days) {
        if (!days) return;
        card.querySelectorAll('[data-days-value]').forEach(function (btn) {
            var on = btn.dataset.daysValue === days;
            btn.classList.toggle('btn-primary', on);
            btn.classList.toggle('btn-outline-primary', !on);
        });
        // The link speaks ?days=, never a date, so this stays a parameter
        // swap — no re-deriving client-side a window the server owns.
        var link = card.querySelector('[data-jobs-explore-link]');
        if (!link) return;
        try {
            var url = new URL(link.getAttribute('href'), window.location.origin);
            url.searchParams.set(DAYS_KEY, days);
            url.searchParams.delete('start');
            url.searchParams.delete('end');
            link.setAttribute('href', url.pathname + '?' + url.searchParams.toString());
        } catch (_) {}
    }

    /** Save on click — synchronously, BEFORE htmx issues any request, so a
     *  sibling's injected refetch can't read a stale window. The afterSettle
     *  save above lands too late for that hand-off. */
    document.addEventListener('click', function (event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var btn = target.closest('[data-jobs-days-pills] [data-days-value]');
        if (!btn) return;
        var card = btn.closest('[data-jobs-days-card]');
        if (!card) return;              // pills opted out of persistence
        var id = card.dataset.chartPersistId;
        if (!id) return;

        var days = btn.dataset.daysValue;
        mergeBucket(id, {days: days});

        // Siblings get the window spelled out rather than relying on the
        // injection above, so the hand-off can't race the save.
        document.querySelectorAll('[data-jobs-days-card]').forEach(function (other) {
            if (other === card || other.dataset.chartPersistId !== id) return;
            var url = other.dataset.jobsCardUrl;
            if (!url || !window.htmx) return;
            htmx.ajax('GET', url + (url.indexOf('?') === -1 ? '?' : '&') + DAYS_KEY + '=' + days,
                      {target: '#' + other.id, swap: 'outerHTML'});
        });
    }, true);

    // Cold start: the server renders the default window, so repaint the
    // pills from storage. The panels need no repaint — the configRequest
    // injection rewrites every panel fetch, and the route gives ?days=
    // precedence over the ?start= baked into the URL.
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-jobs-days-card]').forEach(function (card) {
            var id = card.dataset.chartPersistId;
            if (id) syncDaysCard(card, savedDays(id));
        });
    });

    // A pill swaps out the card that contains it, so the tab restore wired
    // to htmx:afterSettle — which walks the element that made the request —
    // finds only a detached button. Restore against the live card instead,
    // and only while it's on screen: re-showing a saved tab inside a hidden
    // machine subtab would fire that panel's query for a card nobody is
    // looking at.
    document.addEventListener('htmx:load', function (event) {
        var elt = event.detail && event.detail.elt;
        var card = (elt && elt.closest) ? elt.closest('[data-jobs-days-card]') : null;
        if (!card || card.offsetParent === null) return;
        restoreTabs(card);
    });

    // ── Job explorer: tell the server which tab is open ──────────────────────
    //
    // The explorer's filter panel re-renders the whole card, for the same
    // reason a pill does. Without this the server would always render Jobs
    // active, so an Apply while looking at a chart fetched BOTH — the chart
    // the viewer wanted and a per-job table nobody asked for (16s+ on a
    // machine-wide Casper window). The form carries the open tab instead, so
    // the shell comes back with that tab already active and exactly one
    // panel fetches.
    //
    // Markup contract: [data-jobs-tab="<key>"] on each tab button,
    // [data-jobs-active-tab-input] on the form's hidden field.
    document.addEventListener('shown.bs.tab', function (event) {
        var key = event.target && event.target.dataset
            ? event.target.dataset.jobsTab : null;
        if (!key) return;
        document.querySelectorAll('[data-jobs-active-tab-input]')
            .forEach(function (input) { input.value = key; });
    });

    // ── Scroll preservation across full-page navigation ──────────────────────
    //
    // Time-filter clicks (?hours=N) trigger `window.location.href = ...` or
    // a plain link, both of which reload the page and reset scroll to the
    // top — hiding the chart the user was just inspecting. We snapshot
    // scrollY on click (capture phase, before the inline onclick fires) and
    // restore it on the next DOMContentLoaded for the same pathname.

    var SCROLL_PREFIX = 'nav:scroll:';
    var SCROLL_TTL_MS = 5000;

    document.addEventListener('click', function (e) {
        var trigger = e.target.closest && e.target.closest('[data-scroll-preserve]');
        if (!trigger) return;
        try {
            sessionStorage.setItem(
                SCROLL_PREFIX + window.location.pathname,
                JSON.stringify({ y: window.scrollY, ts: Date.now() })
            );
        } catch (_) {}
    }, true);  // capture phase, runs before inline onclick handlers

    document.addEventListener('DOMContentLoaded', function () {
        var key = SCROLL_PREFIX + window.location.pathname;
        var raw = null;
        try {
            raw = sessionStorage.getItem(key);
            sessionStorage.removeItem(key);  // clear immediately — one-shot
        } catch (_) { return; }
        if (!raw) return;

        var saved;
        try { saved = JSON.parse(raw); } catch (_) { return; }
        if (!saved || typeof saved.y !== 'number') return;
        if (Date.now() - saved.ts > SCROLL_TTL_MS) return;

        window.scrollTo({ top: saved.y, behavior: 'instant' });
    });

})();
