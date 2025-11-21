/**
 * Member Management Functions
 * Handles adding/removing project members and admin role changes
 */

// Track current project code for operations
let currentProjcode = null;
let searchTimeout = null;

/**
 * Show the Add Member modal
 */
function showAddMemberModal(projcode) {
    currentProjcode = projcode;
    $('#addMemberProjcode').val(projcode);

    // Reset form
    $('#addMemberForm')[0].reset();
    $('#selectedUsername').val('');
    $('#userSearchResults').hide().empty();
    $('#selectedUserDisplay').hide();
    $('#addMemberSubmitBtn').prop('disabled', true);

    // Set default start date to today, leave end date empty (no end date)
    const today = new Date();
    $('#memberStartDate').val(today.toISOString().split('T')[0]);
    $('#memberEndDate').val('');  // Optional - leave blank for no end date

    $('#addMemberModal').modal('show');
}

/**
 * User search autocomplete with debouncing
 */
$(document).ready(function() {
    $('#userSearch').on('input', function() {
        clearTimeout(searchTimeout);
        const query = $(this).val().trim();
        const projcode = $('#addMemberProjcode').val();

        if (query.length < 2) {
            $('#userSearchResults').hide().empty();
            return;
        }

        searchTimeout = setTimeout(() => {
            fetch(`/dashboard/users/search?q=${encodeURIComponent(query)}&projcode=${encodeURIComponent(projcode)}`)
                .then(response => response.json())
                .then(users => {
                    const results = $('#userSearchResults');
                    results.empty();

                    if (users.length === 0) {
                        results.html('<div class="list-group-item text-muted">No users found</div>');
                    } else {
                        users.forEach(user => {
                            results.append(`
                                <a href="#" class="list-group-item list-group-item-action user-search-result"
                                   data-username="${user.username}"
                                   data-displayname="${user.display_name}"
                                   data-email="${user.email || ''}">
                                    <strong>${user.display_name}</strong>
                                    <small class="text-muted">(${user.username})</small>
                                    ${user.email ? '<br><small class="text-muted">' + user.email + '</small>' : ''}
                                </a>
                            `);
                        });
                    }
                    results.show();
                })
                .catch(error => {
                    console.error('Error searching users:', error);
                    $('#userSearchResults').html('<div class="list-group-item text-danger">Search failed</div>').show();
                });
        }, 300);
    });

    /**
     * Select user from search results
     */
    $(document).on('click', '.user-search-result', function(e) {
        e.preventDefault();
        const username = $(this).data('username');
        const displayName = $(this).data('displayname');
        const email = $(this).data('email');

        // Set hidden field
        $('#selectedUsername').val(username);

        // Update display
        $('#selectedUserName').text(displayName + ' (' + username + ')');
        $('#selectedUserEmail').text(email);
        $('#selectedUserDisplay').show();

        // Hide search
        $('#userSearch').val('');
        $('#userSearchResults').hide();

        // Enable submit button
        $('#addMemberSubmitBtn').prop('disabled', false);
    });

    /**
     * Add member form submission
     */
    $('#addMemberForm').on('submit', function(e) {
        e.preventDefault();

        const projcode = $('#addMemberProjcode').val();
        const formData = new FormData(this);

        // Disable submit button during request
        const submitBtn = $('#addMemberSubmitBtn');
        submitBtn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> Adding...');

        fetch(`/dashboard/projects/${projcode}/members/add`, {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => { throw new Error(text); });
            }
            return response.text();
        })
        .then(html => {
            // Find the members container for this project and update it
            reloadMembersContainer(projcode, html);
            $('#addMemberModal').modal('hide');
        })
        .catch(error => {
            console.error('Error adding member:', error);
            showError('Failed to add member: ' + error.message);
        })
        .finally(() => {
            submitBtn.prop('disabled', false).html('<i class="fas fa-user-plus"></i> Add Member');
        });
    });

    /**
     * Execute member removal
     */
    $('#confirmRemoveBtn').on('click', function() {
        const username = $(this).data('username');
        const btn = $(this);

        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> Removing...');

        fetch(`/dashboard/projects/${currentProjcode}/members/${username}/remove`, {
            method: 'POST'
        })
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => { throw new Error(text); });
            }
            return response.text();
        })
        .then(html => {
            reloadMembersContainer(currentProjcode, html);
            $('#removeMemberModal').modal('hide');
        })
        .catch(error => {
            console.error('Error removing member:', error);
            showError('Failed to remove member: ' + error.message);
        })
        .finally(() => {
            btn.prop('disabled', false).html('<i class="fas fa-user-times"></i> Remove Member');
        });
    });

    /**
     * Execute admin role change
     */
    $('#confirmAdminChangeBtn').on('click', function() {
        const username = $(this).data('username');
        const btn = $(this);

        btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> Updating...');

        const formData = new FormData();
        formData.append('admin_username', username);

        fetch(`/dashboard/projects/${currentProjcode}/admin`, {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => { throw new Error(text); });
            }
            return response.text();
        })
        .then(html => {
            reloadMembersContainer(currentProjcode, html);
            $('#changeAdminModal').modal('hide');
        })
        .catch(error => {
            console.error('Error changing admin:', error);
            showError('Failed to change admin: ' + error.message);
        })
        .finally(() => {
            btn.prop('disabled', false).html('<i class="fas fa-check"></i> Confirm');
        });
    });
});

/**
 * Clear selected user
 */
function clearSelectedUser() {
    $('#selectedUsername').val('');
    $('#selectedUserDisplay').hide();
    $('#addMemberSubmitBtn').prop('disabled', true);
    $('#userSearch').focus();
}

/**
 * Confirm and remove a member
 */
function confirmRemoveMember(projcode, username, displayName) {
    currentProjcode = projcode;
    $('#removeMemberName').text(displayName + ' (' + username + ')');
    $('#confirmRemoveBtn').data('username', username);
    $('#removeMemberModal').modal('show');
}

/**
 * Make a member the project admin
 */
function makeAdmin(projcode, username) {
    currentProjcode = projcode;
    $('#changeAdminMessage').html(`Make <strong>${username}</strong> the project admin?`);
    $('#confirmAdminChangeBtn').data('username', username);
    $('#confirmAdminChangeBtn').data('action', 'make');
    $('#changeAdminModal').modal('show');
}

/**
 * Remove admin role from a member
 */
function removeAdminRole(projcode, username) {
    currentProjcode = projcode;
    $('#changeAdminMessage').html(`Remove admin role from <strong>${username}</strong>? They will remain a project member.`);
    $('#confirmAdminChangeBtn').data('username', '');
    $('#confirmAdminChangeBtn').data('action', 'remove');
    $('#changeAdminModal').modal('show');
}

/**
 * Reload the members container for a specific project
 */
function reloadMembersContainer(projcode, html) {
    // Find the members container - it should be inside a collapse that has been loaded
    // The container has data-load-url containing the projcode
    const containers = $('[data-load-url*="members/' + projcode + '"]');
    if (containers.length > 0) {
        containers.html(html);
    }
}

/**
 * Show error modal
 */
function showError(message) {
    $('#errorMessage').text(message);
    $('#errorModal').modal('show');
}
