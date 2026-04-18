# Institutions tab — expand-row persistence loses race after Search

## Status

**Open / partially mitigated.** The fix works for some entry points but loses
a race against an unidentified handler when the user clicks **Search** on the
inner filter bar of the admin Organizations → Institutions tab.

## Symptom

1. Open Admin → Organizations → Institutions.
2. Toggle **Show users & projects** on (rows become expandable).
3. Expand any institution row — e.g. *Corporation > ATMOSPHERIC & ENVIRONMENTAL
   RESEARCH INC* (id `inst-users-81`).
4. Toggle the inner **Active users & projects only** checkbox and click
   **Search** (or just toggle, since `hx-trigger="change"` also submits).
5. The fragment HTMX-swaps. The expanded row collapses back to `display: none`.

`localStorage["collapse:inst-users-81"]` stays `"1"` throughout, so a manual
re-click immediately re-opens the row, and a full-page refresh restores
correctly. The bug is purely about the in-fragment swap.

## Files involved

- `src/webapp/templates/dashboards/admin/fragments/institutions_table.html`
  — the partial that renders the institutions table; contains the inline
  `<script>` block with the persistence/restore logic at the bottom.
- `src/webapp/templates/dashboards/admin/fragments/institution_filters.html`
  — the filter macro whose checkboxes (`show_users_projects`,
  `active_users_projects`) trigger the HTMX swap; the form has
  `hx-target="#institutions-pane"` and `hx-swap="innerHTML"`.
- `src/webapp/templates/dashboards/admin/fragments/organization_card.html`
  — outer card with the lazy-loading `<div id="institutions-pane">` container.
- `src/webapp/static/js/nav-view-persistence.js` — the existing global helper
  that persists Bootstrap `.collapse[id]` state via `localStorage`. Our
  current row-level persistence intentionally piggy-backs on the same
  `collapse:<id>` keyspace.
- `src/webapp/static/js/collapse-chevron.js` — `SamCollapseChevron.attach()`,
  the chevron-rotation helper used for the InstitutionType-level tbody.
- `src/webapp/dashboards/admin/orgs_routes.py` — `htmx_institutions_fragment`
  is `@cache.cached(query_string=True)`. **Important during debugging**: the
  cache is keyed by URL+query, so editing the template DOES NOT invalidate
  the entries already in cache for query combinations you've previously hit.
  Either disable the decorator while debugging, or vary the query string with
  a `?_=Date.now()` cache-buster.

## What's confirmed (HEAD as of this branch)

| Step | Observation |
|---|---|
| Page load | `nav-view-persistence.js` restores Bootstrap collapse state for the InstitutionType tbody (e.g. `inst-type-3` Corporation). Works. |
| Toggle Show U&P | HTMX swap fires; new fragment HTML includes the inline script; script runs and restores `collapse:inst-users-X = "1"` rows to `style.display = ''`. Confirmed via console log. |
| Click row | Click handler toggles `style.display` and writes/removes localStorage. Confirmed. |
| Click Search | New swap. Inline script runs. Restores `inst-users-81` to `style=""` (visible). Console logs `[inst] post-restore inst-users-81 style: ` (empty) — i.e. visible. |
| ~+500 ms after settle | `MutationObserver` on inst-users-81 fires: `old: "" new: "display: none;"`. Row is now collapsed. localStorage still has `"1"`. |

## What's not yet pinned

The mutation at +500 ms goes through neither
`Element.setAttribute('style', …)` nor
`CSSStyleDeclaration.prototype.setProperty('display', …)` nor
`CSSStyleDeclaration.prototype.cssText = …`. Hooks on all three were
installed and never fired for the +500 ms write. So the writer is using
`el.style.display = '…'` directly, which goes through a per-instance
accessor on `CSSStyleDeclaration` — there is no prototype descriptor for
`display`, so it can't be intercepted at the prototype level (a runtime
attempt via `Object.defineProperty(CSSStyleDeclaration.prototype, 'display',
…)` returned `no prototype descriptor for display; cannot instrument`).

The `MutationObserver` callback runs in a microtask, so its stack trace
unhelpfully points only at the observer dispatcher, not the writer.

## Strongest suspect (unproven)

Bootstrap 5's `Collapse.show()` transition on the parent
`<tbody class="collapse" id="inst-type-X">`. `nav-view-persistence.js`
calls it on `htmx:afterSettle` for every `.collapse[id]` with saved state.
The transition is 350 ms by default; the +500 ms write timing matches
"after-transition cleanup". Bootstrap's documented behavior for `<tbody>`
collapse is quirky — there are several open Bootstrap issues about table
elements and inline display getting clobbered post-transition.

## What's already in the code

The fragment-bottom inline script (`institutions_table.html`) attempts to
restore via the `collapse:<id>` key on each render and attaches a fresh
click handler:

```js
document.querySelectorAll('#institutions-pane .inst-expand-trigger').forEach(function(row) {
    var targetId = row.dataset.instTarget;
    var userRow = document.getElementById(targetId);
    if (!userRow) return;
    var saved = null;
    try { saved = localStorage.getItem('collapse:' + targetId); } catch (_) {}
    if (saved) setExpanded(row, userRow, true);
    row.addEventListener('click', function() {
        var willExpand = userRow.style.display === 'none';
        setExpanded(row, userRow, willExpand);
        try {
            if (willExpand) localStorage.setItem('collapse:' + targetId, '1');
            else            localStorage.removeItem('collapse:' + targetId);
        } catch (_) {}
    });
});
```

This DOES restore — the +500 ms clobber undoes it specifically on
filter-form submits, but everything else is fine.

## Things to try in a fresh session

Roughly in order of cleanliness:

1. **Convert the row to Bootstrap collapse semantics.**
   Make the `<tr id="inst-users-X">` a `class="collapse"` element and use
   `data-bs-toggle="collapse"` on the trigger. Then it gets the same free
   restoration as `inst-type-X` from `nav-view-persistence.js`, and any
   Bootstrap-internal post-transition behavior would be consistent on parent
   AND child. Caveat: Bootstrap collapse on `<tr>` is unusual (browsers
   don't transition table-row height the way they do block elements);
   alternative is to wrap the row contents in a single full-width
   `<td colspan>` containing a `<div class="collapse">`.

2. **Defer the restore past the parent-tbody transition.**
   In the inline script, schedule restore at `setTimeout(restore, ~600)` AND
   on `transitionend.bs.collapse` from the closest `tbody.collapsing`.
   Hacky but minimal-risk; race-wins against whatever the +500 ms writer is.

3. **Listen to `htmx:afterSettle` on the document instead of running inline.**
   Move the row-restore out of the fragment-bottom script and into a
   document-level handler that runs after the swap is fully settled — by
   then the parent collapse transition has either finished or not started
   yet, but at least we run after every other settle handler. May still
   lose to whatever is writing display, but worth comparing timing.

4. **Patch `el.style.display` per-instance after restore.**
   After calling `setExpanded(row, userRow, true)`, redefine
   `userRow.style.display` via a property descriptor on the instance
   (`Object.defineProperty(userRow.style, 'display', { ... })`) that ignores
   writes — purely diagnostic to prove the writer exists, then look at the
   stack the override captures.

5. **Bisect the global handlers.**
   Temporarily remove `nav-view-persistence.js` (`restoreCollapses` half) at
   page-load time and re-test. If the row stays open → confirmed it's the
   Bootstrap-collapse-on-parent path. From there, look at Bootstrap source
   `_completeCallBack` / `_setTransitioning` for what gets touched.

6. **Avoid the cache while debugging.**
   `htmx_institutions_fragment` has `@cache.cached(query_string=True)`
   (`src/webapp/dashboards/admin/orgs_routes.py:169`). Comment it out for
   the duration of the session, or pass `?_=<timestamp>` on the test URL.
   Without this you will think the template change didn't land when it
   actually did but the cache is still serving the old HTML.

## Repro recipe (Playwright MCP)

```js
// Pre: open http://127.0.0.1:5050/admin/, ensure DEV_AUTO_LOGIN_USER is set.
// localStorage.clear() first to keep state predictable.

new bootstrap.Tab(document.getElementById('organizations-tab')).show();
await new Promise(r => setTimeout(r, 1500));
new bootstrap.Tab(document.getElementById('institutions-tab')).show();
await afterSettle();

document.querySelector('#inst-filters [name="show_users_projects"]').checked = true;
document.querySelector('#inst-filters [name="show_users_projects"]')
  .dispatchEvent(new Event('change', {bubbles: true}));
await afterSettle();

new bootstrap.Collapse(document.getElementById('inst-type-3'), {toggle: false}).show();
await new Promise(r => setTimeout(r, 600));

Array.from(document.querySelectorAll('#institutions-pane .inst-expand-trigger'))
  .find(tr => /ATMOSPHERIC\s*&\s*ENVIRONMENTAL\s+RESEARCH/i.test(tr.textContent))
  .click();
// row is now expanded; localStorage["collapse:inst-users-81"] === "1"

document.querySelector('#inst-filters [name="active_users_projects"]').checked = true;
document.querySelector('#inst-filters button[type="submit"]').click();
await afterSettle();
await new Promise(r => setTimeout(r, 1500));

// Expected: <tr id="inst-users-81" style="">    (visible)
// Actual:   <tr id="inst-users-81" style="display: none;">    (collapsed)
// In both cases: localStorage["collapse:inst-users-81"] === "1"
```

(`afterSettle` = wait for `htmx:afterSettle` once, fallback timeout 8–12 s.)

## Tracking branch

- Branch: `institutions`
- Last good commit before this debug attempt: `a60e0ca` (filterable
  Country/State-Prov tab, no U&P toggles).
- The U&P toggles + persistence land in a follow-up commit on the same
  branch; the persistence comment block in `institutions_table.html`
  references this document.
