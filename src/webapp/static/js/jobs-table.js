/**
 * Job Table Pagination
 *
 * Provides window.loadJobsPage(pageNum) used by pagination links in
 * the jobs_table.html fragment.  The base URL (with start_date and
 * end_date already encoded) is read from the #jobs-table-config
 * data attribute that is rendered by the server into the fragment.
 */

(function() {
    'use strict';

    /**
     * Fetch a specific page of the job list and replace the container contents.
     * @param {number} pageNum - 1-based page number to load
     */
    function loadJobsPage(pageNum) {
        var container = document.getElementById('jobs-container');
        var config = document.getElementById('jobs-table-config');
        if (!container || !config) return;

        var baseUrl = config.dataset.jobsUrl;
        var startDate = config.dataset.startDate;
        var endDate = config.dataset.endDate;
        var url = baseUrl +
                  '?start_date=' + encodeURIComponent(startDate) +
                  '&end_date='   + encodeURIComponent(endDate) +
                  '&page='       + pageNum;

        // Show loading indicator while fetching
        $(container).html(
            '<div class="text-center p-3">' +
                '<div class="spinner-border text-primary" role="status">' +
                    '<span class="sr-only">Loading...</span>' +
                '</div>' +
            '</div>'
        );

        fetch(url)
            .then(function(response) {
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.text();
            })
            .then(function(html) {
                $(container).html(html);
            })
            .catch(function(error) {
                console.error('Error loading jobs page:', error);
                $(container).html('<p class="text-danger mb-0">Failed to load jobs</p>');
            });
    }

    // Expose globally so Jinja-rendered onclick handlers can call it
    window.loadJobsPage = loadJobsPage;

})();
