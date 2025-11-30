/**
 * Project Search functionality for Admin tab
 *
 * Provides autocomplete search for projects and displays results using project cards.
 */

(function() {
    'use strict';

    let searchTimeout = null;
    const SEARCH_DELAY = 300; // ms delay before searching

    /**
     * Initialize project search functionality
     */
    function initProjectSearch() {
        const searchInput = document.getElementById('projectSearchInput');
        const searchResults = document.getElementById('projectSearchResults');
        const selectedProjectDisplay = document.getElementById('selectedProjectDisplay');
        const selectedProjectCode = document.getElementById('selectedProjectCode');
        const viewProjectBtn = document.getElementById('viewProjectBtn');
        const projectCardContainer = document.getElementById('projectCardContainer');
        const activeOnlyCheckbox = document.getElementById('activeProjectsOnly');

        if (!searchInput || !searchResults) {
            console.error('Project search elements not found');
            return;
        }

        // Search as user types
        searchInput.addEventListener('input', function() {
            const searchTerm = this.value.trim();

            // Clear previous timeout
            if (searchTimeout) {
                clearTimeout(searchTimeout);
            }

            // Clear results if search is empty
            if (searchTerm.length === 0) {
                searchResults.style.display = 'none';
                searchResults.innerHTML = '';
                return;
            }

            // Delay search to avoid excessive requests
            searchTimeout = setTimeout(function() {
                performSearch(searchTerm);
            }, SEARCH_DELAY);
        });

        // View project button click
        if (viewProjectBtn) {
            viewProjectBtn.addEventListener('click', function() {
                const projcode = selectedProjectCode.value;
                if (projcode) {
                    loadProjectCard(projcode);
                }
            });
        }

        // Active projects filter
        if (activeOnlyCheckbox) {
            activeOnlyCheckbox.addEventListener('change', function() {
                // Re-trigger search if there's a current search term
                const searchTerm = searchInput.value.trim();
                if (searchTerm.length > 0) {
                    performSearch(searchTerm);
                }
            });
        }
    }

    /**
     * Perform project search via AJAX
     */
    function performSearch(searchTerm) {
        const searchResults = document.getElementById('projectSearchResults');
        const activeOnlyCheckbox = document.getElementById('activeProjectsOnly');

        // Build URL with query parameters
        let url = `/user/project-search?search=${encodeURIComponent(searchTerm)}`;
        if (activeOnlyCheckbox && activeOnlyCheckbox.checked) {
            url += '&active=true';
        }

        fetch(url)
            .then(response => response.json())
            .then(data => {
                displaySearchResults(data.projects);
            })
            .catch(error => {
                console.error('Search error:', error);
                searchResults.innerHTML = '<div class="list-group-item text-danger">Error searching projects</div>';
                searchResults.style.display = 'block';
            });
    }

    /**
     * Display search results in dropdown
     */
    function displaySearchResults(projects) {
        const searchResults = document.getElementById('projectSearchResults');

        if (projects.length === 0) {
            searchResults.innerHTML = '<div class="list-group-item text-muted">No projects found</div>';
            searchResults.style.display = 'block';
            return;
        }

        let html = '';
        projects.forEach(project => {
            const activeClass = project.active ? '' : 'text-muted';
            const activeBadge = project.active
                ? '<span class="badge badge-success ml-2">Active</span>'
                : '<span class="badge badge-secondary ml-2">Inactive</span>';

            html += `
                <a href="#" class="list-group-item list-group-item-action ${activeClass}"
                   data-projcode="${project.projcode}"
                   data-title="${project.title}"
                   data-lead="${project.lead}">
                    <div>
                        <strong>${project.projcode}</strong>${activeBadge}
                    </div>
                    <small class="text-muted">${project.title}</small>
                    ${project.lead ? `<br><small class="text-muted">Lead: ${project.lead}</small>` : ''}
                </a>
            `;
        });

        searchResults.innerHTML = html;
        searchResults.style.display = 'block';

        // Add click handlers to results
        searchResults.querySelectorAll('.list-group-item-action').forEach(item => {
            item.addEventListener('click', function(e) {
                e.preventDefault();
                selectProject(
                    this.dataset.projcode,
                    this.dataset.title,
                    this.dataset.lead
                );
            });
        });
    }

    /**
     * Select a project from search results
     */
    function selectProject(projcode, title, lead) {
        const searchInput = document.getElementById('projectSearchInput');
        const searchResults = document.getElementById('projectSearchResults');
        const selectedProjectDisplay = document.getElementById('selectedProjectDisplay');
        const selectedProjectCode = document.getElementById('selectedProjectCode');
        const selectedProjectName = document.getElementById('selectedProjectName');
        const selectedProjectLead = document.getElementById('selectedProjectLead');
        const viewProjectBtn = document.getElementById('viewProjectBtn');

        // Update hidden input
        selectedProjectCode.value = projcode;

        // Update display
        selectedProjectName.textContent = `${projcode} - ${title}`;
        if (lead && selectedProjectLead) {
            selectedProjectLead.textContent = `Lead: ${lead}`;
        }

        // Show selected project display
        selectedProjectDisplay.style.display = 'block';

        // Enable view button
        viewProjectBtn.disabled = false;

        // Hide search results
        searchResults.style.display = 'none';

        // Clear search input
        searchInput.value = '';

        // Automatically load the project card
        loadProjectCard(projcode);
    }

    /**
     * Load and display project card
     */
    function loadProjectCard(projcode) {
        const projectCardContainer = document.getElementById('projectCardContainer');

        if (!projectCardContainer) {
            console.error('Project card container not found');
            return;
        }

        // Show loading state
        projectCardContainer.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="sr-only">Loading project...</span>
                </div>
            </div>
        `;

        // Fetch project card HTML
        fetch(`/user/project-card/${encodeURIComponent(projcode)}`)
            .then(response => response.text())
            .then(html => {
                projectCardContainer.innerHTML = html;

                // Initialize lazy loading for the project card
                if (window.initLazyLoading) {
                    window.initLazyLoading();
                }
            })
            .catch(error => {
                console.error('Error loading project card:', error);
                projectCardContainer.innerHTML = `
                    <div class="alert alert-danger">
                        <i class="fas fa-exclamation-triangle"></i>
                        Error loading project information
                    </div>
                `;
            });
    }

    // Initialize on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initProjectSearch);
    } else {
        initProjectSearch();
    }
})();
