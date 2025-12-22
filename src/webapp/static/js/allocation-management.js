/**
 * Allocation Management Functions
 * Handles editing allocation amounts and dates with audit logging
 *
 * API v1 endpoints:
 *   GET  /api/v1/allocations/<allocation_id> - Get allocation details
 *   PUT  /api/v1/allocations/<allocation_id> - Update allocation
 */

(function() {
    'use strict';

    // API base path (reuse from window if it exists to avoid conflicts)
    const API_BASE = '/api/v1';

    // Track current allocation for operations
    let currentAllocationId = null;
    let currentProjcode = null;

    /**
     * Show the Edit Allocation modal
     * Fetches current allocation data and populates the form
     */
    function showEditAllocationModal(projcode, allocationId, resourceName) {
        currentProjcode = projcode;
        currentAllocationId = allocationId;

        // Show loading state in modal
        $('#editAllocationModalLabel').html('<i class="fas fa-spinner fa-spin"></i> Loading...');
        $('#editAllocationModal').modal('show');

        // Fetch allocation data
        fetch(`${API_BASE}/allocations/${allocationId}`)
            .then(response => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .then(allocation => {
                // Populate form fields
                $('#editAllocationId').val(allocationId);
                $('#editAllocationProjcode').val(projcode);
                $('#editAllocationResourceName').text(resourceName);

                // Pre-fill amount with current value and display it
                // API returns 'amount' not 'allocated'
                const currentAmount = allocation.amount || 0;
                $('#allocationAmount').val(currentAmount);
                $('#allocationAmountDisplay').text(currentAmount.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 }));

                $('#allocationDescription').val(allocation.description || '');

                // Format dates to YYYY-MM-DD
                if (allocation.start_date) {
                    const startDate = new Date(allocation.start_date);
                    $('#allocationStartDate').val(startDate.toISOString().split('T')[0]);
                }
                if (allocation.end_date) {
                    const endDate = new Date(allocation.end_date);
                    $('#allocationEndDate').val(endDate.toISOString().split('T')[0]);
                } else {
                    $('#allocationEndDate').val('');
                }

                // Restore modal title
                $('#editAllocationModalLabel').html('<i class="fas fa-edit"></i> Edit Allocation');
                $('#editAllocationSubmitBtn').prop('disabled', false);
            })
            .catch(error => {
                console.error('Error loading allocation:', error);
                $('#editAllocationModal').modal('hide');
                showAllocationError('Failed to load allocation: ' + error.message);
            });
    }

    /**
     * Reload the project card fragment from the server
     */
    function reloadProjectCard(projcode) {
        // Reload the entire dashboard to refresh all project cards
        // This is simpler than trying to reload individual cards
        window.location.reload();
    }

    /**
     * Show allocation error modal
     */
    function showAllocationError(message) {
        $('#allocationErrorMessage').text(message);
        $('#allocationErrorModal').modal('show');
    }

    /**
     * Handle form submission
     */
    $(document).ready(function() {
        $('#editAllocationForm').on('submit', function(e) {
            e.preventDefault();

            const allocationId = $('#editAllocationId').val();
            const projcode = $('#editAllocationProjcode').val();

            // Disable submit button during request
            const submitBtn = $('#editAllocationSubmitBtn');
            submitBtn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> Saving...');

            // Build JSON payload from form data
            const formData = new FormData(this);
            const payload = {};

            // Only include fields that have values
            const amount = parseFloat(formData.get('amount'));
            if (amount && amount > 0) {
                payload.amount = amount;
            }

            const startDate = formData.get('start_date');
            if (startDate) {
                payload.start_date = startDate;
            }

            const endDate = formData.get('end_date');
            if (endDate) {
                payload.end_date = endDate;
            } else {
                // Explicitly set to null if empty (clears end date)
                payload.end_date = null;
            }

            const description = formData.get('description');
            if (description !== null) {
                payload.description = description;
            }

            // Validate that we have at least one field to update
            if (Object.keys(payload).length === 0) {
                showAllocationError('No changes to save');
                submitBtn.prop('disabled', false).html('<i class="fas fa-save"></i> Save Changes');
                return;
            }

            // Client-side date validation
            if (payload.start_date && payload.end_date) {
                const start = new Date(payload.start_date);
                const end = new Date(payload.end_date);
                if (end < start) {
                    showAllocationError('End date cannot be before start date');
                    submitBtn.prop('disabled', false).html('<i class="fas fa-save"></i> Save Changes');
                    return;
                }
            }

            // Submit update request
            fetch(`${API_BASE}/allocations/${allocationId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            })
            .then(response => response.json().then(data => ({ ok: response.ok, data })))
            .then(({ ok, data }) => {
                if (!ok) {
                    throw new Error(data.error || 'Failed to update allocation');
                }
                // Close modal and reload project data
                $('#editAllocationModal').modal('hide');
                reloadProjectCard(projcode);
            })
            .catch(error => {
                console.error('Error updating allocation:', error);
                showAllocationError('Failed to update allocation: ' + error.message);
            })
            .finally(() => {
                submitBtn.prop('disabled', false).html('<i class="fas fa-save"></i> Save Changes');
            });
        });

        // Form validation: ensure amount is positive
        $('#allocationAmount').on('input', function() {
            const value = parseFloat($(this).val());
            if (value <= 0) {
                $(this)[0].setCustomValidity('Amount must be greater than 0');
            } else {
                $(this)[0].setCustomValidity('');
            }
        });

        // Date validation: end date must be after start date
        $('#allocationStartDate, #allocationEndDate').on('change', function() {
            const startDate = $('#allocationStartDate').val();
            const endDate = $('#allocationEndDate').val();

            if (startDate && endDate) {
                const start = new Date(startDate);
                const end = new Date(endDate);

                if (end < start) {
                    $('#allocationEndDate')[0].setCustomValidity('End date must be after start date');
                } else {
                    $('#allocationEndDate')[0].setCustomValidity('');
                }
            } else {
                $('#allocationEndDate')[0].setCustomValidity('');
            }
        });
    });

    // Expose functions globally for onclick handlers
    window.showEditAllocationModal = showEditAllocationModal;
    window.showAllocationError = showAllocationError;

})();
