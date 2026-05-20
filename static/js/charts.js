/**
 * charts.js
 * Pure chart rendering module. No business logic.
 * Reads data from JSON embedded in data-* attributes on canvas elements.
 * Each function is independent — adding/removing one doesn't affect others.
 *
 * Charts implemented:
 *   1. renderHealthScoreChart(canvasId)  — horizontal bar, analyze page
 *   2. renderReadinessTrendChart(canvasId) — line chart, compare page
 *   3. renderTargetDriftChart(canvasId)  — line chart, compare page
 *   4. renderPsiHeatmap(containerId)     — CSS grid heatmap, compare page
 */

'use strict';

// ── Shared palette ────────────────────────────────────────────────────────────
const COLORS = {
  good:     '#22c55e',
  fair:     '#3b82f6',
  poor:     '#f59e0b',
  critical: '#ef4444',
  stable:   '#22c55e',
  monitor:  '#f59e0b',
  shift:    '#ef4444',
  grid:     'rgba(148,163,184,0.15)',
  text:     '#94a3b8',
};

Chart.defaults.color      = COLORS.text;
Chart.defaults.borderColor = COLORS.grid;
Chart.defaults.font.family = 'Inter, system-ui, sans-serif';
Chart.defaults.font.size   = 11;

// ─────────────────────────────────────────────────────────────────────────────
// 1. Column Health Score — horizontal bar chart (analyze page)
//    Canvas must have: data-scores='[{"column":"age","score":72,"label":"fair"},...]'
// ─────────────────────────────────────────────────────────────────────────────
function renderHealthScoreChart(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const data   = JSON.parse(canvas.dataset.scores || '[]');
  if (!data.length) return;

  // Sort worst-first so worst columns are at top
  data.sort((a, b) => a.score - b.score);

  const labels = data.map(d => d.column);
  const scores = data.map(d => d.score);
  const colors = data.map(d => {
    if (d.score >= 80) return COLORS.good;
    if (d.score >= 55) return COLORS.fair;
    if (d.score >= 30) return COLORS.poor;
    return COLORS.critical;
  });

  new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Health Score',
        data: scores,
        backgroundColor: colors,
        borderRadius: 4,
        borderSkipped: false,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.raw}/100 (${data[ctx.dataIndex].label})`,
            afterLabel: ctx => data[ctx.dataIndex].top_issue
              ? `  ↳ ${data[ctx.dataIndex].top_issue}` : '',
          }
        }
      },
      scales: {
        x: {
          min: 0, max: 100,
          grid: { color: COLORS.grid },
          ticks: { callback: v => v + '/100' }
        },
        y: {
          grid: { display: false },
          ticks: { font: { family: 'JetBrains Mono, monospace', size: 11 } }
        }
      }
    }
  });
}


// ─────────────────────────────────────────────────────────────────────────────
// 2. Dataset Readiness Trend — line chart (compare page, ≥2 versions)
//    Canvas must have: data-scores='[{"abt":"v1","score":88},{"abt":"v2","score":72}]'
// ─────────────────────────────────────────────────────────────────────────────
function renderReadinessTrendChart(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const data = JSON.parse(canvas.dataset.scores || '[]');
  if (data.length < 2) {
    canvas.parentElement.style.display = 'none';
    return;
  }

  const labels = data.map(d => d.abt.replace('_v', ' v'));
  const scores = data.map(d => d.score);

  // Color each point individually
  const pointColors = scores.map(s =>
    s >= 80 ? COLORS.good : s >= 60 ? COLORS.fair : s >= 40 ? COLORS.poor : COLORS.critical
  );

  new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Dataset Readiness',
        data: scores,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.08)',
        pointBackgroundColor: pointColors,
        pointBorderColor: pointColors,
        pointRadius: 6,
        pointHoverRadius: 8,
        tension: 0.3,
        fill: true,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: ctx => ` ${ctx.raw}/100 readiness` }
        }
      },
      scales: {
        y: {
          min: 0, max: 100,
          grid: { color: COLORS.grid },
          ticks: { callback: v => v + '/100' }
        },
        x: { grid: { display: false } }
      }
    }
  });
}


// ─────────────────────────────────────────────────────────────────────────────
// 3. Target Event Rate Timeline — line chart (compare page)
//    Canvas must have: data-rates='[{"abt":"v1","event_rate":30.1},...]'
// ─────────────────────────────────────────────────────────────────────────────
function renderTargetDriftChart(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const data = JSON.parse(canvas.dataset.rates || '[]').filter(d => d.event_rate != null);
  if (data.length < 2) {
    canvas.parentElement.style.display = 'none';
    return;
  }

  const labels = data.map(d => d.abt.replace('_v', ' v'));
  const rates  = data.map(d => d.event_rate);

  // Flag points where back-testing was triggered
  const drifts  = JSON.parse(canvas.dataset.drifts || '[]');
  const driftMap = {};
  drifts.forEach(d => {
    if (d.severity === 'critical') driftMap[d.to_ver] = '#ef4444';
    else if (d.severity === 'notable') driftMap[d.to_ver] = '#f59e0b';
  });

  const pointColors = data.map(d => driftMap[d.abt] || '#3b82f6');

  new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Event Rate %',
        data: rates,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.08)',
        pointBackgroundColor: pointColors,
        pointBorderColor: pointColors,
        pointRadius: 6,
        pointHoverRadius: 8,
        tension: 0.3,
        fill: true,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` Event rate: ${ctx.raw}%`,
            afterLabel: ctx => {
              const ver = data[ctx.dataIndex].abt;
              if (driftMap[ver] === '#ef4444') return '  ⚠ Critical drift — back-test required';
              if (driftMap[ver] === '#f59e0b') return '  ⚡ Notable drift — monitor closely';
              return '';
            }
          }
        }
      },
      scales: {
        y: {
          min: 0, max: 100,
          grid: { color: COLORS.grid },
          ticks: { callback: v => v + '%' }
        },
        x: { grid: { display: false } }
      }
    }
  });
}


// ─────────────────────────────────────────────────────────────────────────────
// 4. PSI Heatmap — CSS grid table with color cells (compare page)
//    Container must have: data-psi='{"version_labels":[...],"columns":[...]}'
//    Pure DOM, no Chart.js — heatmaps are better as styled tables.
// ─────────────────────────────────────────────────────────────────────────────
function renderPsiHeatmap(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const psi = JSON.parse(container.dataset.psi || '{}');
  const cols = psi.columns || [];
  const vls  = psi.version_labels || [];

  if (!cols.length || vls.length < 2) {
    container.innerHTML = '<p class="empty-state">Not enough data for PSI heatmap.</p>';
    return;
  }

  const pairCount = vls.length - 1;

  // Build header
  let html = '<div class="psi-heatmap">';
  html += '<div class="phm-row phm-header">';
  html += '<div class="phm-cell phm-col-name">Column</div>';
  for (let i = 0; i < pairCount; i++) {
    const from = vls[i].replace('_v', ' v');
    const to   = vls[i+1].replace('_v', ' v');
    html += `<div class="phm-cell phm-pair">${from}→${to}</div>`;
  }
  html += '<div class="phm-cell phm-worst">Worst</div></div>';

  // Build rows
  cols.forEach(col => {
    html += '<div class="phm-row">';
    html += `<div class="phm-cell phm-col-name"><code>${col.column}</code></div>`;
    col.pairs.forEach(pair => {
      const label = pair.label || 'n/a';
      const psiVal = pair.psi != null ? pair.psi.toFixed(3) : '—';
      html += `<div class="phm-cell phm-val phm-${label}" title="${pair.note || ''}">${psiVal}</div>`;
    });
    html += `<div class="phm-cell phm-val phm-${col.worst_label}">${col.worst_label}</div>`;
    html += '</div>';
  });

  html += '</div>';
  container.innerHTML = html;
}


// ─────────────────────────────────────────────────────────────────────────────
// Auto-init: run all charts present on the current page
// ─────────────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  renderHealthScoreChart('chart-health-scores');
  renderReadinessTrendChart('chart-readiness-trend');
  renderTargetDriftChart('chart-target-drift');
  renderPsiHeatmap('psi-heatmap-container');
});