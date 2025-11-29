/**
 * Impersonation Functions
 * Handles searching for a user to impersonate.
 */
$(document).ready(function() {
    const searchInput = $('#userSearchImpersonate');
    if (searchInput.length === 0) {
        // Don't run if the impersonation search input is not on the page
        return;
    }

    const resultsContainer = $('#userSearchResultsImpersonate');
    const selectedUsernameInput = $('#selectedUsernameImpersonate');
    const selectedUserDisplay = $('#selectedUserDisplayImpersonate');
    const selectedUserName = $('#selectedUserNameImpersonate');
    const selectedUserEmail = $('#selectedUserEmailImpersonate');
    const submitBtn = $('#impersonateSubmitBtn');

    // Debounce function
    let debounceTimeout;
    function debounce(func, delay) {
        return function(...args) {
            clearTimeout(debounceTimeout);
            debounceTimeout = setTimeout(() => func.apply(this, args), delay);
        };
    }

    // Function to handle user search
    function searchUsers(query) {
        if (query.length < 2) {
            resultsContainer.hide();
            return;
        }

        fetch(`/api/v1/users/search?q=${encodeURIComponent(query)}`)
            .then(response => {
                if (!response.ok) throw new Error('Search failed');
                return response.json();
            })
            .then(users => {
                resultsContainer.empty().show();
                if (users.length === 0) {
                    resultsContainer.append('<a href="#" class="list-group-item list-group-item-action disabled">No users found</a>');
                } else {
                    users.forEach(user => {
                        const item = $(`<a href="#" class="list-group-item list-group-item-action"></a>`);
                        item.text(`${user.display_name} (${user.username})`);
                        item.data('user', user);
                        resultsContainer.append(item);
                    });
                }
            })
            .catch(error => {
                console.error("User search error:", error);
                resultsContainer.empty().hide();
            });
    }

    // Event listener for search input
    searchInput.on('keyup', debounce(function() {
        searchUsers($(this).val());
    }, 300));

    // Event listener for clicking a search result
    resultsContainer.on('click', '.list-group-item-action', function(e) {
        e.preventDefault();
        if ($(this).hasClass('disabled')) return;

        const user = $(this).data('user');
        selectedUsernameInput.val(user.username);
        selectedUserName.text(user.display_name);
        selectedUserEmail.text(user.primary_email || '');

        selectedUserDisplay.show();
        resultsContainer.hide();
        searchInput.val(''); // Clear search bar
        submitBtn.prop('disabled', false);
    });

    // Clear selected user
    window.clearSelectedUserImpersonate = function() {
        selectedUsernameInput.val('');
        selectedUserDisplay.hide();
        submitBtn.prop('disabled', true);
    }
});
