/**
 * Expirations panel functionality for admin tab
 */

(function() {
    'use strict';

    let currentView = 'upcoming';

    /**
     * Initialize expirations panel
     */
    function initExpirations() {
        const expirationsSection = document.getElementById('expirations-section');
        if (!expirationsSection) {
            return; // Not on admin tab
        }

        // Load upcoming data immediately when section is shown
        $('#expirations-section').on('shown.bs.collapse', function() {
            if (!this.dataset.loaded) {
                loadExpirations('upcoming');
                this.dataset.loaded = 'true';
            }
        });

        // Tab switching
        $('#expirations-tabs a[data-toggle="pill"]').on('shown.bs.tab', function(e) {
            currentView = this.dataset.view;
            const container = document.getElementById(`${currentView}-container`);

            // Load if not already loaded
            if (container && !container.dataset.loaded) {
                loadExpirations(currentView);
                container.dataset.loaded = 'true';
            }
        });

        // Apply filters button
        $('#apply-expirations-filters').on('click', function() {
            // Reload current view with new filters
            const container = document.getElementById(`${currentView}-container`);
            if (container) {
                container.dataset.loaded = 'false'; // Force reload
                loadExpirations(currentView);
            }
        });

        // Export CSV button
        $('#export-expirations-csv').on('click', function() {
            exportExpirations();
        });
    }

    /**
     * Load expirations data via AJAX
     */
    function loadExpirations(view) {
        const container = document.getElementById(`${view}-container`);
        if (!container) return;

        // Show loading spinner
        container.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="sr-only">Loading...</span>
                </div>
            </div>
        `;

        // Build query parameters from filters
        const params = new URLSearchParams();
        params.append('view', view);

        // Facilities
        $('.facility-filter:checked').each(function() {
            params.append('facilities', this.value);
        });

        // Resource
        const resource = $('#resource-filter').val();
        if (resource) {
            params.append('resource', resource);
        }

        // Time range (for upcoming only)
        if (view === 'upcoming') {
            const timeRange = $('#time-range-filter').val();
            if (timeRange) {
                params.append('time_range', timeRange);
            }
        }

        // Fetch data
        fetch(`/user/admin/expirations?${params.toString()}`)
            .then(response => response.text())
            .then(html => {
                container.innerHTML = html;

                // Update count badge
                const count = extractCount(html, view);
                updateCountBadge(view, count);

                // Initialize lazy loading for project cards
                if (typeof initLazyLoading === 'function') {
                    initLazyLoading();
                }

                // Attach impersonate handlers for abandoned users
                if (view === 'abandoned') {
                    attachImpersonateHandlers();
                    attachProjectCodeHandlers();
                }
            })
            .catch(error => {
                console.error('Error loading expirations:', error);
                container.innerHTML = `
                    <div class="alert alert-danger">
                        <i class="fas fa-exclamation-triangle"></i>
                        Error loading expirations data
                    </div>
                `;
            });
    }

    /**
     * Export expirations to CSV
     */
    function exportExpirations() {
        const params = new URLSearchParams();
        params.append('export_type', currentView);

        // Add filters
        $('.facility-filter:checked').each(function() {
            params.append('facilities', this.value);
        });

        const resource = $('#resource-filter').val();
        if (resource) {
            params.append('resource', resource);
        }

        if (currentView === 'upcoming') {
            const timeRange = $('#time-range-filter').val();
            if (timeRange) {
                params.append('time_range', timeRange);
            }
        }

        // Open in new window (downloads file)
        window.open(`/user/admin/expirations/export?${params.toString()}`, '_blank');
    }

    /**
     * Extract count from HTML (for badge)
     */
    function extractCount(html, view) {
        // Parse count from "Showing X projects" or table rows
        const showingMatch = html.match(/Showing (\d+) project/i);
        const usersMatch = html.match(/(\d+) abandoned user/i);
        const rowMatches = html.match(/<tr[^>]*>/g);

        if (showingMatch) {
            return parseInt(showingMatch[1], 10);
        } else if (usersMatch) {
            return parseInt(usersMatch[1], 10);
        } else if (rowMatches) {
            return rowMatches.length - 1; // Subtract header row
        }
        return 0;
    }

    /**
     * Update count badge in tab
     */
    function updateCountBadge(view, count) {
        const badge = document.getElementById(`${view}-count`);
        if (badge) {
            badge.textContent = count;
            badge.classList.remove('badge-light', 'badge-primary', 'badge-secondary');
            badge.classList.add(count > 0 ? 'badge-primary' : 'badge-secondary');
        }
    }

    /**
     * Attach impersonate button handlers
     */
    function attachImpersonateHandlers() {
        $('.impersonate-user-btn').on('click', function() {
            const username = this.dataset.username;
            if (username && confirm(`Impersonate user ${username}?`)) {
                // Use existing impersonation form
                const form = document.getElementById('impersonateUserForm');
                const usernameInput = document.getElementById('selectedUsernameImpersonate');

                if (form && usernameInput) {
                    usernameInput.value = username;
                    form.submit();
                }
            }
        });
    }

    /**
     * Attach project code click handlers
     */
    function attachProjectCodeHandlers() {
        $('.project-code-link').on('click', function(e) {
            e.preventDefault();
            const projcode = this.dataset.projcode;
            if (projcode) {
                loadProjectCard(projcode);
            }
        });
    }

    /**
     * Load project card into projectCardContainer
     */
    function loadProjectCard(projcode) {
        const container = document.getElementById('projectCardContainer');
        if (!container) return;

        // Show loading spinner
        container.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="sr-only">Loading project ${projcode}...</span>
                </div>
            </div>
        `;

        // Scroll to the container
        container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        // Fetch project card
        fetch(`/user/project-card/${projcode}`)
            .then(response => response.text())
            .then(html => {
                container.innerHTML = html;

                // Initialize lazy loading for the project card
                if (typeof initLazyLoading === 'function') {
                    initLazyLoading();
                }
            })
            .catch(error => {
                console.error('Error loading project card:', error);
                container.innerHTML = `
                    <div class="alert alert-danger">
                        <i class="fas fa-exclamation-triangle"></i>
                        Error loading project ${projcode}
                    </div>
                `;
            });
    }

    // Initialize on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initExpirations);
    } else {
        initExpirations();
    }
})();
