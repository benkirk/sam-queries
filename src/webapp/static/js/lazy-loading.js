/**
 * Lazy Loading for Collapsible Sections
 * Loads HTML fragments on-demand when sections are expanded
 */

/**
 * Load content into a container from its data-load-url attribute.
 * Marks it as loaded to prevent duplicate fetches.
 */
function loadLazyContainer(container) {
    const $container = $(container);
    const url = $container.data('load-url');

    fetch(url)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.text();
        })
        .then(html => {
            $container.html(html);
            $container.attr('data-loaded', 'true');
            // Let htmx discover hx-* attributes on newly injected content
            if (window.htmx) htmx.process(container);
        })
        .catch(error => {
            console.error('Error loading content:', error);
            $container.html('<p class="text-danger mb-0">Failed to load content</p>');
        });
}

// Trigger lazy loading when a collapsible section expands
$(document).on('show.bs.collapse', function(event) {
    const container = $(event.target).find('[data-load-url]:not([data-loaded="true"])').first();
    if (container.length > 0) {
        loadLazyContainer(container[0]);
    }
});

/**
 * Manually trigger lazy loading for containers that are already visible
 * but have not yet fetched their content.  Call this after injecting
 * dynamic HTML that may contain lazy-loadable sections (e.g. project cards
 * loaded into an admin panel).
 */
function initLazyLoading() {
    $('[data-load-url]:not([data-loaded="true"])').each(function() {
        // Skip containers that are inside a collapsed (hidden) ancestor
        if ($(this).closest('.collapse:not(.show)').length === 0) {
            loadLazyContainer(this);
        }
    });
}

// Expose globally so other scripts can call it after injecting content
window.initLazyLoading = initLazyLoading;
