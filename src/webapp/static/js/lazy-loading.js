/**
 * Lazy Loading for Collapsible Sections
 * Loads HTML fragments on-demand when sections are expanded
 */

/**
 * Load content into a container from its data-load-url attribute.
 * Marks it as loaded to prevent duplicate fetches.
 */
function loadLazyContainer(container) {
    const url = container.dataset.loadUrl;

    fetch(url)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.text();
        })
        .then(html => {
            container.innerHTML = html;
            container.setAttribute('data-loaded', 'true');
            // Let htmx discover hx-* attributes on newly injected content
            if (window.htmx) htmx.process(container);
            // Bind sortable headers — this fetch path fires neither
            // DOMContentLoaded nor htmx:afterSwap (sortable_table.js's hooks)
            if (window.bindSortableTables) window.bindSortableTables(container);
        })
        .catch(error => {
            console.error('Error loading content:', error);
            container.innerHTML = '<p class="text-danger mb-0">Failed to load content</p>';
        });
}

// Trigger lazy loading when a collapsible section expands. Bootstrap 5
// dispatches a native, bubbling show.bs.collapse event, so one document-level
// listener catches every collapse via delegation.
document.addEventListener('show.bs.collapse', function(event) {
    const container = event.target.querySelector(
        '[data-load-url]:not([data-loaded="true"])');
    if (container) {
        loadLazyContainer(container);
    }
});

/**
 * Manually trigger lazy loading for containers that are already visible
 * but have not yet fetched their content.  Call this after injecting
 * dynamic HTML that may contain lazy-loadable sections (e.g. project cards
 * loaded into an admin panel).
 */
function initLazyLoading() {
    document.querySelectorAll('[data-load-url]:not([data-loaded="true"])').forEach(function(el) {
        // Skip containers that are inside a collapsed (hidden) ancestor
        if (!el.closest('.collapse:not(.show)')) {
            loadLazyContainer(el);
        }
    });
}

// Expose globally so other scripts can call it after injecting content
window.initLazyLoading = initLazyLoading;
