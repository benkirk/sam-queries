/**
 * Project Details Modal Handler
 *
 * Handles clickable project codes that open a modal with full project details.
 * Uses delegated event handlers for dynamically loaded content (AJAX, etc.).
 */

$(document).ready(function() {
    // Handle project details modal (delegated event for dynamically loaded content)
    $(document).on('click', '.view-project-details-btn', function(e) {
        e.preventDefault();
        e.stopPropagation(); // Prevent card collapse when clicking project code

        var projcode = $(this).data('projcode');

        // Show modal
        $('#projectDetailsModal').modal('show');

        // Load project details
        $('#projectDetailsModalBody').html(
            '<div class="text-center"><div class="spinner-border text-primary" role="status"></div></div>'
        );

        // Build URL - get the base URL from a data attribute if available, or construct it
        var baseUrl = $('#projectDetailsModal').data('url-template');
        if (!baseUrl) {
            // Fallback: construct URL assuming Flask route structure
            baseUrl = '/user/project-details-modal/__PROJCODE__';
        }
        var url = baseUrl.replace('__PROJCODE__', projcode);

        $.ajax({
            url: url,
            success: function(data) {
                $('#projectDetailsModalBody').html(data);
            },
            error: function() {
                $('#projectDetailsModalBody').html(
                    '<div class="alert alert-danger">Failed to load project details</div>'
                );
            }
        });
    });
});
