/**
 * SAM Dashboard Utilities
 * Shared JavaScript functions for formatting, validation, and UI helpers
 */

// ============================================================================
// NUMBER & DATE FORMATTING
// ============================================================================

/**
 * Format a number with commas and optional decimal places
 * @param {number} num - The number to format
 * @param {number} minDecimals - Minimum decimal places (default: 0)
 * @param {number} maxDecimals - Maximum decimal places (default: 2)
 * @returns {string} Formatted number string
 */
function formatNumber(num, minDecimals = 0, maxDecimals = 2) {
    if (num === null || num === undefined || isNaN(num)) {
        return 'N/A';
    }
    return num.toLocaleString('en-US', {
        minimumFractionDigits: minDecimals,
        maximumFractionDigits: maxDecimals
    });
}

/**
 * Format a percentage value
 * @param {number} value - The percentage value
 * @param {number} decimals - Number of decimal places (default: 1)
 * @returns {string} Formatted percentage string with % symbol
 */
function formatPercent(value, decimals = 1) {
    if (value === null || value === undefined || isNaN(value)) {
        return 'N/A';
    }
    return value.toFixed(decimals) + '%';
}

/**
 * Format an ISO date string to local date
 * @param {string} dateStr - ISO date string
 * @param {object} options - Intl.DateTimeFormat options
 * @returns {string} Formatted date string
 */
function formatDate(dateStr, options = {}) {
    if (!dateStr) {
        return 'N/A';
    }

    const defaultOptions = {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    };

    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-US', { ...defaultOptions, ...options });
    } catch (e) {
        return 'Invalid Date';
    }
}

/**
 * Format an ISO datetime string to local datetime
 * @param {string} datetimeStr - ISO datetime string
 * @returns {string} Formatted datetime string
 */
function formatDateTime(datetimeStr) {
    if (!datetimeStr) {
        return 'N/A';
    }

    try {
        const date = new Date(datetimeStr);
        return date.toLocaleString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch (e) {
        return 'Invalid DateTime';
    }
}

// ============================================================================
// PROGRESS BAR & STATUS HELPERS
// ============================================================================

/**
 * Get Bootstrap progress bar color class based on usage percentage
 * @param {number} percent - Usage percentage (0-100+)
 * @returns {string} Bootstrap color class (bg-success, bg-info, bg-warning, bg-danger)
 */
function getProgressColor(percent) {
    if (percent >= 90) return 'bg-danger';
    if (percent >= 75) return 'bg-warning';
    if (percent >= 50) return 'bg-info';
    return 'bg-success';
}

/**
 * Get Bootstrap badge class for status
 * @param {string} status - Status string (Active, Inactive, Expired, etc.)
 * @returns {string} Bootstrap badge class
 */
function getStatusBadgeClass(status) {
    const statusLower = (status || '').toLowerCase();

    if (statusLower === 'active') return 'badge-success';
    if (statusLower === 'inactive' || statusLower === 'expired') return 'badge-secondary';
    if (statusLower === 'pending') return 'badge-warning';
    if (statusLower === 'expiring') return 'badge-warning';

    return 'badge-primary';
}

/**
 * Create a progress bar HTML element
 * @param {number} percent - Progress percentage
 * @param {string} label - Optional label to display
 * @param {number} height - Bar height in pixels (default: 20)
 * @returns {string} HTML string for progress bar
 */
function createProgressBar(percent, label = null, height = 20) {
    const colorClass = getProgressColor(percent);
    const displayPercent = Math.min(percent, 100).toFixed(1);
    const width = Math.min(Math.max(percent, 0), 100);
    const displayLabel = label || `${displayPercent}%`;

    return `
        <div class="progress" style="height: ${height}px;">
            <div class="progress-bar ${colorClass}"
                 role="progressbar"
                 style="width: ${width}%"
                 aria-valuenow="${percent}"
                 aria-valuemin="0"
                 aria-valuemax="100">
                ${displayLabel}
            </div>
        </div>
    `;
}

/**
 * Create a status badge HTML element
 * @param {string} status - Status text
 * @returns {string} HTML string for badge
 */
function createStatusBadge(status) {
    const badgeClass = getStatusBadgeClass(status);
    return `<span class="badge ${badgeClass}">${status}</span>`;
}

// ============================================================================
// API HELPERS
// ============================================================================

/**
 * Fetch wrapper with error handling
 * @param {string} url - API endpoint URL
 * @param {object} options - Fetch options
 * @returns {Promise} Resolves to parsed JSON or rejects with error
 */
async function apiFetch(url, options = {}) {
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json',
        },
        ...options
    };

    try {
        const response = await fetch(url, defaultOptions);

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        return await response.json();
    } catch (error) {
        console.error(`API Error (${url}):`, error);
        throw error;
    }
}

/**
 * Display error message in alert div
 * @param {string} message - Error message
 * @param {string} containerId - Container element ID (default: 'error-container')
 */
function showError(message, containerId = 'error-container') {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error('Error container not found:', containerId);
        return;
    }

    container.innerHTML = `
        <div class="alert alert-danger alert-dismissible fade show" role="alert">
            <i class="fas fa-exclamation-circle"></i> ${message}
            <button type="button" class="close" data-dismiss="alert">
                <span>&times;</span>
            </button>
        </div>
    `;
}

/**
 * Display success message in alert div
 * @param {string} message - Success message
 * @param {string} containerId - Container element ID (default: 'message-container')
 */
function showSuccess(message, containerId = 'message-container') {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error('Message container not found:', containerId);
        return;
    }

    container.innerHTML = `
        <div class="alert alert-success alert-dismissible fade show" role="alert">
            <i class="fas fa-check-circle"></i> ${message}
            <button type="button" class="close" data-dismiss="alert">
                <span>&times;</span>
            </button>
        </div>
    `;
}

// ============================================================================
// LOADING SPINNERS
// ============================================================================

/**
 * Create a loading spinner HTML
 * @param {string} message - Optional loading message
 * @param {string} size - Size (sm, md, lg) - default: md
 * @returns {string} HTML string for spinner
 */
function createLoadingSpinner(message = 'Loading...', size = 'md') {
    const sizeClass = size === 'sm' ? 'spinner-border-sm' : '';

    return `
        <div class="loading-spinner text-center py-4">
            <div class="spinner-border text-primary ${sizeClass}" role="status">
                <span class="sr-only">${message}</span>
            </div>
            ${message ? `<p class="mt-2 text-muted">${message}</p>` : ''}
        </div>
    `;
}

// ============================================================================
// MUSTACHE HELPERS (for use with Mustache.js)
// ============================================================================

/**
 * Mustache view helpers - register these in your Mustache rendering
 */
const MustacheHelpers = {
    formatNumber: function() {
        return function(val, render) {
            const num = parseFloat(render(val));
            return formatNumber(num);
        };
    },

    formatPercent: function() {
        return function(val, render) {
            const num = parseFloat(render(val));
            return formatPercent(num);
        };
    },

    formatDate: function() {
        return function(val, render) {
            return formatDate(render(val));
        };
    },

    progressColor: function() {
        return function(val, render) {
            const percent = parseFloat(render(val));
            return getProgressColor(percent);
        };
    },

    statusBadge: function() {
        return function(val, render) {
            return getStatusBadgeClass(render(val));
        };
    }
};

// ============================================================================
// DOM UTILITIES
// ============================================================================

/**
 * Safely get element by ID with error logging
 * @param {string} id - Element ID
 * @returns {HTMLElement|null} Element or null if not found
 */
function getElement(id) {
    const el = document.getElementById(id);
    if (!el) {
        console.warn(`Element not found: #${id}`);
    }
    return el;
}

/**
 * Show/hide element by ID
 * @param {string} id - Element ID
 * @param {boolean} visible - True to show, false to hide
 */
function toggleElement(id, visible) {
    const el = getElement(id);
    if (el) {
        el.style.display = visible ? '' : 'none';
    }
}

// ============================================================================
// CSV EXPORT HELPERS
// ============================================================================

/**
 * Download data as CSV file
 * @param {string} filename - Filename for download
 * @param {Array} rows - Array of arrays (CSV rows)
 */
function downloadCSV(filename, rows) {
    const csvContent = rows.map(row =>
        row.map(cell => {
            // Escape quotes and wrap in quotes if needed
            const cellStr = String(cell).replace(/"/g, '""');
            return cellStr.includes(',') || cellStr.includes('\n') ? `"${cellStr}"` : cellStr;
        }).join(',')
    ).join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);

    link.setAttribute('href', url);
    link.setAttribute('download', filename);
    link.style.visibility = 'hidden';

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// ============================================================================
// VALIDATION HELPERS
// ============================================================================

/**
 * Validate username format
 * @param {string} username - Username to validate
 * @returns {boolean} True if valid
 */
function isValidUsername(username) {
    return /^[a-z][a-z0-9_-]{0,31}$/.test(username);
}

/**
 * Validate projcode format
 * @param {string} projcode - Project code to validate
 * @returns {boolean} True if valid
 */
function isValidProjcode(projcode) {
    return /^[A-Z]{4}[0-9]{4}$/.test(projcode);
}

// Make functions available globally
window.SAM = {
    // Formatting
    formatNumber,
    formatPercent,
    formatDate,
    formatDateTime,

    // UI Helpers
    getProgressColor,
    getStatusBadgeClass,
    createProgressBar,
    createStatusBadge,
    createLoadingSpinner,

    // API
    apiFetch,
    showError,
    showSuccess,

    // DOM
    getElement,
    toggleElement,

    // Export
    downloadCSV,

    // Validation
    isValidUsername,
    isValidProjcode,

    // Mustache helpers
    MustacheHelpers
};

// Also keep functions in global scope for backward compatibility
// This allows templates to use both SAM.formatNumber() and formatNumber()
