/**
 * SAM Shared JavaScript Utilities
 *
 * Loaded on every dashboard page (base.html).  Exposes window.SAMUtils with
 * helpers that are used by multiple page-specific scripts.
 */

(function(window) {
    'use strict';

    /**
     * Return a Bootstrap spinner HTML string for use as a loading placeholder.
     * @param {string} [message] - Screen-reader label (default: "Loading...")
     */
    function spinnerHtml(message) {
        var label = message || 'Loading...';
        return (
            '<div class="text-center py-4">' +
                '<div class="spinner-border text-primary" role="status">' +
                    '<span class="sr-only">' + label + '</span>' +
                '</div>' +
            '</div>'
        );
    }

    /**
     * Load a project card into #projectCardContainer.
     *
     * Fetches /admin/project/<projcode>, injects the HTML, then calls
     * window.initLazyLoading() so any collapsible sections inside the
     * freshly loaded card pick up their lazy-load URLs.
     *
     * @param {string} projcode - The project code to load
     */
    function loadAdminProjectCard(projcode) {
        var container = document.getElementById('projectCardContainer');
        if (!container) return;

        container.innerHTML = spinnerHtml('Loading project ' + projcode + '...');
        container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        fetch('/admin/project/' + encodeURIComponent(projcode))
            .then(function(response) { return response.text(); })
            .then(function(html) {
                container.innerHTML = html;
                if (typeof window.initLazyLoading === 'function') {
                    window.initLazyLoading();
                }
            })
            .catch(function(error) {
                console.error('Error loading project card:', error);
                container.innerHTML =
                    '<div class="alert alert-danger">' +
                        '<i class="fas fa-exclamation-triangle"></i> ' +
                        'Error loading project ' + projcode +
                    '</div>';
            });
    }

    // Expose namespace
    window.SAMUtils = {
        spinnerHtml:          spinnerHtml,
        loadAdminProjectCard: loadAdminProjectCard
    };

    // Also expose directly so existing onclick/window.loadAdminProjectCard callers
    // (allocation-management.js) continue to work without changes.
    window.loadAdminProjectCard = loadAdminProjectCard;

})(window);
