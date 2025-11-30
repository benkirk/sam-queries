/**
 * Lazy Loading for Collapsible Sections
 * Loads HTML fragments on-demand when sections are expanded
 */
$(document).on('show.bs.collapse', function(event) {
    const container = $(event.target).find('[data-load-url]:not([data-loaded="true"])').first();

    if (container.length > 0) {
        const url = container.data('load-url');

        // Fetch HTML fragment
        fetch(url)
            .then(response => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.text();
            })
            .then(html => {
                container.html(html);
                container.attr('data-loaded', 'true');
            })
            .catch(error => {
                console.error('Error loading content:', error);
                container.html('<p class="text-danger mb-0">Failed to load content</p>');
            });
    }
});
