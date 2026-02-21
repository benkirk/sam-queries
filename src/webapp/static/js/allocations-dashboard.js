/**
 * Allocations Dashboard
 * Handles expandable rows, sortable tables, and usage modal loading.
 *
 * Flask URLs are injected via data attributes on #allocations-dashboard-config:
 *   data-projects-url   — endpoint for expanding allocation type rows
 *   data-usage-url      — URL template (with __PROJCODE__ / __RESOURCE__ tokens)
 *                         for the per-project usage modal
 */

$(document).ready(function() {
    // Read Flask-generated URLs from the config element so this file stays
    // free of hard-coded endpoint strings.
    var config = document.getElementById('allocations-dashboard-config');
    var projectsUrl      = config ? config.dataset.projectsUrl  : '';
    var usageUrlTemplate = config ? config.dataset.usageUrl     : '';

    // ---------------------------------------------------------------
    // Expandable rows — click to load project details inline
    // ---------------------------------------------------------------

    $('.expandable-row').click(function() {
        var $row     = $(this);
        var $icon    = $row.find('.expand-icon');
        var resource = $row.data('resource');
        var facility = $row.data('facility');
        var type     = $row.data('type');
        var activeAt = $row.data('active-at');
        var detailsId  = '#details-' + resource.replace(/ /g, '_') + '-' + facility + '-' + type.replace(/ /g, '_');
        var $detailsRow = $(detailsId);

        // Toggle chevron icon
        $icon.toggleClass('expanded');

        if ($detailsRow.hasClass('show')) {
            $detailsRow.removeClass('show');
        } else {
            // Fetch project details if not already loaded
            if (!$detailsRow.data('loaded')) {
                $.ajax({
                    url: projectsUrl,
                    data: {
                        resource:        resource,
                        facility:        facility,
                        allocation_type: type,
                        active_at:       activeAt
                    },
                    success: function(data) {
                        $detailsRow.find('td').html('<div class="p-3">' + data + '</div>');
                        $detailsRow.data('loaded', true);
                        $detailsRow.addClass('show');
                        initSortableTable($detailsRow.find('table'));
                    },
                    error: function() {
                        $detailsRow.find('td').html(
                            '<div class="alert alert-danger">Failed to load project details</div>'
                        );
                    }
                });
            } else {
                $detailsRow.addClass('show');
            }
        }
    });

    // ---------------------------------------------------------------
    // Client-side table sorting for dynamically loaded project tables
    // ---------------------------------------------------------------

    function initSortableTable($table) {
        // Reflect the server's default sort (Amount descending = column index 2)
        $table.find('th').eq(2).addClass('sort-desc');

        $table.find('th.sortable-header').on('click', function() {
            var $th      = $(this);
            var colIndex = $th.index();
            var sortType = $th.data('sort');
            var isAsc    = !$th.hasClass('sort-asc');

            $table.find('th').removeClass('sort-asc sort-desc');
            $th.addClass(isAsc ? 'sort-asc' : 'sort-desc');

            var $tbody = $table.find('tbody');
            var rows   = $tbody.find('tr').toArray();

            rows.sort(function(a, b) {
                var aVal = $(a).find('td').eq(colIndex).data('sort-value');
                var bVal = $(b).find('td').eq(colIndex).data('sort-value');

                if (sortType === 'numeric') {
                    aVal = parseFloat(aVal) || 0;
                    bVal = parseFloat(bVal) || 0;
                    return isAsc ? aVal - bVal : bVal - aVal;
                } else {
                    aVal = String(aVal || '').toLowerCase();
                    bVal = String(bVal || '').toLowerCase();
                    return isAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
                }
            });

            $tbody.append(rows);
        });
    }

    // ---------------------------------------------------------------
    // Usage modal — delegated handler for dynamically loaded content
    // ---------------------------------------------------------------

    $(document).on('click', '.view-usage-btn', function(e) {
        e.preventDefault();
        var projcode = $(this).data('projcode');
        var resource = $(this).data('resource');
        var activeAt = $(this).data('active-at');

        bootstrap.Modal.getOrCreateInstance(document.getElementById('usageModal')).show();

        $('#usageModalBody').html(
            '<div class="text-center"><div class="spinner-border text-primary" role="status"></div></div>'
        );

        var url = usageUrlTemplate
            .replace('__PROJCODE__', projcode)
            .replace('__RESOURCE__', resource);

        $.ajax({
            url: url,
            data: { active_at: activeAt },
            success: function(data) {
                $('#usageModalBody').html(data);
            },
            error: function() {
                $('#usageModalBody').html(
                    '<div class="alert alert-danger">Failed to load usage details</div>'
                );
            }
        });
    });
});
