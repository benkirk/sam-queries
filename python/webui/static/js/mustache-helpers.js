/**
 * SAM Mustache Rendering Helpers
 * Preprocesses API data for Mustache templates and handles rendering
 */

// Template cache
const templateCache = {};

/**
 * Load and cache a Mustache template
 * @param {string} templateName - Template filename (without .mustache extension)
 * @returns {Promise<string>} Template string
 */
async function loadTemplate(templateName) {
    if (templateCache[templateName]) {
        return templateCache[templateName];
    }

    try {
        const response = await fetch(`/static/templates/${templateName}.mustache`);
        if (!response.ok) {
            throw new Error(`Failed to load template: ${templateName}`);
        }
        const template = await response.text();
        templateCache[templateName] = template;
        return template;
    } catch (error) {
        console.error(`Error loading template ${templateName}:`, error);
        throw error;
    }
}

/**
 * Preprocess project data for Mustache rendering
 * Adds formatted values and computed properties
 * @param {object} project - Raw project data from API
 * @returns {object} Preprocessed project data
 */
function preprocessProject(project) {
    // Calculate progress bar color and width
    const progressColor = getProgressColor(project.percent_used);
    const progressWidth = Math.min(Math.max(project.percent_used, 0), 100);

    // Format allocation values
    const processed = {
        ...project,
        progressColor: progressColor,
        progressWidth: progressWidth,
        total_allocated: formatNumber(project.total_allocated),
        total_used: formatNumber(project.total_used),
        total_remaining: formatNumber(project.total_remaining),
        percent_used: formatNumber(project.percent_used, 1, 1)
    };

    // Preprocess resources if present
    if (project.resources && project.resources.length > 0) {
        processed.resources = project.resources.map(preprocessResource);
    }

    return processed;
}

/**
 * Preprocess resource data for Mustache rendering
 * @param {object} resource - Raw resource data from API
 * @returns {object} Preprocessed resource data
 */
function preprocessResource(resource) {
    const resourceProgressColor = getProgressColor(resource.percent_used);
    const progressWidth = Math.min(Math.max(resource.percent_used, 0), 100);

    return {
        ...resource,
        resourceProgressColor: resourceProgressColor,
        progressWidth: progressWidth,
        allocated_formatted: formatNumber(resource.allocated),
        used_formatted: formatNumber(resource.used),
        remaining_formatted: formatNumber(resource.remaining),
        percent_used_formatted: formatNumber(resource.percent_used, 1, 1),
        start_date_formatted: formatDate(resource.start_date),
        end_date_formatted: formatDate(resource.end_date),
        statusIsActive: resource.status === 'Active'
    };
}

/**
 * Preprocess transaction data for Mustache rendering
 * @param {object} transaction - Raw transaction data from API
 * @returns {object} Preprocessed transaction data
 */
function preprocessTransaction(transaction) {
    const amount = parseFloat(transaction.amount);

    return {
        ...transaction,
        date_formatted: formatDate(transaction.date),
        amount_formatted: formatNumber(Math.abs(amount)),
        isAllocation: transaction.type.includes('Allocation'),
        isCredit: transaction.type.includes('Credit'),
        isDebit: transaction.type.includes('Debit'),
        isPositive: amount > 0,
        isNegative: amount < 0,
        isZero: amount === 0
    };
}

/**
 * Preprocess sparkline data for SVG rendering
 * @param {Array} data - Array of {date, value} objects
 * @param {number} width - SVG width (default: 400)
 * @param {number} height - SVG height (default: 60)
 * @returns {object} Sparkline data with SVG points
 */
function preprocessSparkline(data, width = 400, height = 60) {
    if (!data || data.length === 0) {
        return {
            data: [],
            linePoints: '',
            areaPoints: '',
            points: [],
            baselineY: height / 2,
            width: width,
            height: height
        };
    }

    // Find min/max values
    const values = data.map(d => d.value);
    const minValue = Math.min(...values);
    const maxValue = Math.max(...values);
    const range = maxValue - minValue || 1; // Avoid division by zero

    // Calculate padding
    const padding = 10;
    const chartHeight = height - (padding * 2);
    const chartWidth = width - (padding * 2);

    // Generate points
    const step = chartWidth / (data.length - 1 || 1);
    const points = data.map((d, i) => {
        const x = padding + (i * step);
        const y = padding + chartHeight - (((d.value - minValue) / range) * chartHeight);
        return {
            x: x,
            y: y,
            date: formatDate(d.date),
            value: formatNumber(d.value)
        };
    });

    // Generate SVG polyline points string
    const linePoints = points.map(p => `${p.x},${p.y}`).join(' ');

    // Generate SVG area polygon points (line + baseline)
    const baselineY = padding + chartHeight;
    const areaPoints =
        `${padding},${baselineY} ` +
        linePoints +
        ` ${padding + chartWidth},${baselineY}`;

    return {
        data: data,
        linePoints: linePoints,
        areaPoints: areaPoints,
        points: points,
        baselineY: baselineY,
        width: width,
        height: height
    };
}

/**
 * Render a Mustache template with preprocessed data
 * @param {string} templateName - Template filename (without extension)
 * @param {object} data - Raw data from API
 * @param {object} partials - Optional partials object
 * @returns {Promise<string>} Rendered HTML
 */
async function renderTemplate(templateName, data, partials = {}) {
    try {
        // Load main template
        const template = await loadTemplate(templateName);

        // Load partials if not provided
        const loadedPartials = { ...partials };

        // Common partials used by templates
        const commonPartials = [
            'resource-row',
            'progress-bar',
            'project-tree-node',
            'transaction-row',
            'sparkline'
        ];

        for (const partialName of commonPartials) {
            if (!loadedPartials[partialName]) {
                try {
                    loadedPartials[partialName] = await loadTemplate(partialName);
                } catch (e) {
                    // Partial not found, skip
                }
            }
        }

        // Render template
        const html = Mustache.render(template, data, loadedPartials);
        return html;
    } catch (error) {
        console.error(`Error rendering template ${templateName}:`, error);
        throw error;
    }
}

/**
 * Render project list from API data
 * @param {object} apiData - Response from /api/v1/users/me/projects?format=dashboard
 * @param {string} containerId - Target container element ID
 */
async function renderProjects(apiData, containerId) {
    try {
        // Preprocess all projects
        const processedData = {
            username: apiData.username,
            total_projects: apiData.total_projects,
            projects: apiData.projects.map(preprocessProject)
        };

        // Render template
        const html = await renderTemplate('project-list', processedData);

        // Insert into DOM
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = html;
        } else {
            console.error(`Container not found: ${containerId}`);
        }
    } catch (error) {
        console.error('Error rendering projects:', error);
        showError('Failed to load projects. Please refresh the page.');
    }
}

/**
 * Render sparkline chart from API data
 * @param {Array} chargeData - Array of {date, comp, dav, disk, archive} objects
 * @param {string} containerId - Target container element ID
 * @param {string} chargeType - Type of charge to display (comp, dav, disk, archive)
 */
async function renderSparkline(chargeData, containerId, chargeType = 'comp') {
    try {
        // Extract values for specified charge type
        const data = chargeData.map(day => ({
            date: day.date,
            value: day[chargeType] || 0
        }));

        // Preprocess for SVG
        const sparklineData = preprocessSparkline(data);
        sparklineData.id = containerId;

        // Render template
        const html = await renderTemplate('sparkline', sparklineData);

        // Insert into DOM
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = html;
        } else {
            console.error(`Container not found: ${containerId}`);
        }
    } catch (error) {
        console.error('Error rendering sparkline:', error);
    }
}

/**
 * Render allocation transactions table
 * @param {Array} changes - Array of transaction objects from API
 * @param {string} tableBodyId - Target tbody element ID
 */
async function renderTransactions(changes, tableBodyId) {
    try {
        // Preprocess all transactions
        const processedTransactions = changes.map(preprocessTransaction);

        // Load transaction-row template
        const template = await loadTemplate('transaction-row');

        // Render all rows
        const rows = processedTransactions.map(transaction =>
            Mustache.render(template, transaction)
        ).join('');

        // Insert into DOM
        const tbody = document.getElementById(tableBodyId);
        if (tbody) {
            tbody.innerHTML = rows;
        } else {
            console.error(`Table body not found: ${tableBodyId}`);
        }
    } catch (error) {
        console.error('Error rendering transactions:', error);
    }
}

/**
 * Preprocess charge data for table rendering
 * @param {Array} chargeData - Array of {date, comp, dav, disk, archive} objects
 * @returns {object} Processed data with column visibility flags
 */
function preprocessCharges(chargeData) {
    if (!chargeData || chargeData.length === 0) {
        return {
            data: [],
            hasComp: false,
            hasDav: false,
            hasDisk: false,
            hasArchive: false
        };
    }

    // Determine which columns have non-zero values
    const hasComp = chargeData.some(d => (d.comp || 0) > 0);
    const hasDav = chargeData.some(d => (d.dav || 0) > 0);
    const hasDisk = chargeData.some(d => (d.disk || 0) > 0);
    const hasArchive = chargeData.some(d => (d.archive || 0) > 0);

    // Process each row
    const processedData = chargeData.map(day => {
        const comp = day.comp || 0;
        const dav = day.dav || 0;
        const disk = day.disk || 0;
        const archive = day.archive || 0;
        const total = comp + dav + disk + archive;

        return {
            date: day.date,
            date_formatted: formatDate(day.date),
            comp: comp,
            comp_formatted: formatNumber(comp),
            dav: dav,
            dav_formatted: formatNumber(dav),
            disk: disk,
            disk_formatted: formatNumber(disk),
            archive: archive,
            archive_formatted: formatNumber(archive),
            total: total,
            total_formatted: formatNumber(total),
            hasComp: hasComp,
            hasDav: hasDav,
            hasDisk: hasDisk,
            hasArchive: hasArchive
        };
    });

    return {
        data: processedData,
        hasComp: hasComp,
        hasDav: hasDav,
        hasDisk: hasDisk,
        hasArchive: hasArchive
    };
}

/**
 * Render charge table from API data
 * @param {Array} chargeData - Array of {date, comp, dav, disk, archive} objects
 * @param {string} tableBodyId - Target tbody element ID
 */
async function renderChargeTable(chargeData, tableBodyId) {
    try {
        const processed = preprocessCharges(chargeData);

        // Load template
        const template = await loadTemplate('charge-table-row');

        // Render all rows
        const rows = processed.data.map(row =>
            Mustache.render(template, row)
        ).join('');

        // Insert into DOM
        const tbody = document.getElementById(tableBodyId);
        if (tbody) {
            tbody.innerHTML = rows;
        } else {
            console.error(`Table body not found: ${tableBodyId}`);
        }

        return processed; // Return for header updates
    } catch (error) {
        console.error('Error rendering charge table:', error);
    }
}

/**
 * Render resource row(s) from API data
 * @param {object|Array} resourceData - Single resource or array of resources
 * @param {string} projcode - Project code for navigation links
 * @param {string} tableBodyId - Target tbody element ID
 */
async function renderResourceRows(resourceData, projcode, tableBodyId) {
    try {
        // Normalize to array
        const resources = Array.isArray(resourceData) ? resourceData : [resourceData];

        // Preprocess all resources
        const processedResources = resources.map(preprocessResource);

        // Load template
        const template = await loadTemplate('resource-row');

        // Render all rows with projcode in context
        const rows = processedResources.map(resource =>
            Mustache.render(template, { ...resource, projcode: projcode })
        ).join('');

        // Insert into DOM
        const tbody = document.getElementById(tableBodyId);
        if (tbody) {
            tbody.innerHTML = rows;
        } else {
            console.error(`Table body not found: ${tableBodyId}`);
        }
    } catch (error) {
        console.error('Error rendering resource rows:', error);
    }
}

/**
 * Simple sparkline renderer using Canvas (lighter than Chart.js)
 * @param {Array} data - Array of {date, value} objects
 * @param {string} canvasId - Canvas element ID
 * @param {object} options - Optional configuration
 */
function renderSimpleSparkline(data, canvasId, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
        console.error(`Canvas not found: ${canvasId}`);
        return;
    }

    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    if (!data || data.length === 0) {
        ctx.fillStyle = '#6c757d';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('No data available', width / 2, height / 2);
        return;
    }

    // Extract values
    const values = data.map(d => d.value || 0);
    const minValue = Math.min(...values);
    const maxValue = Math.max(...values);
    const range = maxValue - minValue || 1;

    // Padding
    const padding = 20;
    const chartWidth = width - (padding * 2);
    const chartHeight = height - (padding * 2);

    // Calculate points
    const step = chartWidth / (values.length - 1 || 1);
    const points = values.map((value, i) => ({
        x: padding + (i * step),
        y: padding + chartHeight - (((value - minValue) / range) * chartHeight)
    }));

    // Draw area fill
    ctx.fillStyle = 'rgba(0, 153, 204, 0.1)';
    ctx.beginPath();
    ctx.moveTo(points[0].x, padding + chartHeight);
    points.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(points[points.length - 1].x, padding + chartHeight);
    ctx.closePath();
    ctx.fill();

    // Draw line
    ctx.strokeStyle = '#0099CC';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    points.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.stroke();

    // Draw points
    ctx.fillStyle = '#0099CC';
    points.forEach(p => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 3, 0, 2 * Math.PI);
        ctx.fill();
    });

    // Draw baseline
    const baselineY = padding + chartHeight;
    ctx.strokeStyle = '#dee2e6';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(padding, baselineY);
    ctx.lineTo(padding + chartWidth, baselineY);
    ctx.stroke();
    ctx.setLineDash([]);
}

// Export functions to global scope
window.SAM = window.SAM || {};
window.SAM.Mustache = {
    loadTemplate,
    renderTemplate,
    renderProjects,
    renderSparkline,
    renderTransactions,
    renderChargeTable,
    renderResourceRows,
    renderSimpleSparkline,
    preprocessProject,
    preprocessResource,
    preprocessTransaction,
    preprocessSparkline,
    preprocessCharges
};
