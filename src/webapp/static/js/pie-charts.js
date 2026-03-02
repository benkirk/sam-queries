/**
 * Client-side pie chart rendering using Chart.js.
 * Replaces server-side Matplotlib SVG generation for the allocations dashboard.
 */
(function() {
    'use strict';

    const COLORS = [
        '#4e79a7', '#f28e2b', '#e15759', '#76b7b2',
        '#59a14f', '#edc948', '#b07aa1', '#ff9da7',
        '#9c755f', '#bab0ac'
    ];

    function renderPieChart(canvas) {
        const labels = JSON.parse(canvas.dataset.labels);
        const values = JSON.parse(canvas.dataset.values);
        const title = canvas.dataset.title || '';

        if (!labels.length || !values.length) return;

        new Chart(canvas, {
            type: 'pie',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: COLORS.slice(0, labels.length),
                    borderWidth: 1,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    title: {
                        display: !!title,
                        text: title,
                        font: { size: 14, weight: 'bold' }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                const val = ctx.parsed;
                                const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                                const pct = ((val / total) * 100).toFixed(1);
                                return ctx.label + ': ' + val.toLocaleString() + ' (' + pct + '%)';
                            }
                        }
                    }
                }
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('.facility-pie-chart, .alloc-type-pie-chart').forEach(renderPieChart);
    });
})();
