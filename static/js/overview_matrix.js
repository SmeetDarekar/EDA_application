/**
 * overview_matrix.js
 * Column Overview Matrix — sort, search, row highlight, scroll-to-section,
 * drill-down panel with Quantile Trend + Ridge Plot via Plotly.
 *
 * Loaded via {% block scripts %} in compare_results.html.
 * Depends on Plotly (loaded from CDN in the same block).
 */

'use strict';

// ── Plotly dark theme tokens (match CSS variables) ────────────────────────────
const OM_PLOTLY_LAYOUT = {
  paper_bgcolor: 'transparent',
  plot_bgcolor:  'transparent',
  font:          { family: 'Inter, system-ui, sans-serif', color: '#94a3b8', size: 11 },
  margin:        { t: 32, r: 16, b: 40, l: 52 },
  legend:        { bgcolor: 'transparent', font: { color: '#94a3b8', size: 10 } },
  xaxis: { gridcolor: 'rgba(148,163,184,0.12)', zerolinecolor: 'rgba(148,163,184,0.15)' },
  yaxis: { gridcolor: 'rgba(148,163,184,0.12)', zerolinecolor: 'rgba(148,163,184,0.15)' },
};

const OM_VERSION_COLORS = [
  '#3b82f6', '#22c55e', '#f59e0b', '#a855f7',
  '#ef4444', '#06b6d4', '#f97316', '#84cc16',
];

// ── Parse embedded c4 data ────────────────────────────────────────────────────
function omGetC4Data() {
  try {
    const el = document.getElementById('om-c4-data');
    return el ? JSON.parse(el.textContent) : [];
  } catch (e) {
    return [];
  }
}

// Build {colName → version_stats[]} lookup from c4
function omBuildC4Lookup(c4) {
  const map = {};
  c4.forEach(col => { map[col.column] = col.version_stats || []; });
  return map;
}

// ── Sorting ───────────────────────────────────────────────────────────────────
const OM_SORT_ORDER = {
  // readiness: drop worst
  readiness: { drop: 0, caution: 1, ready: 2, absent: 3 },
  // psi: shift worst
  psi:       { shift: 0, monitor: 1, stable: 2, 'n/a': 3, 'not_applicable': 3 },
  // drift: critical worst
  drift:     { critical: 0, notable: 1, stable: 2, 'n/a': 3 },
  // cardinality: yes = problem
  cardinality: { yes: 0, no: 1 },
};

let omSortCol = 'health';
let omSortDir = 'asc';   // asc = worst first for numeric (lower health = worse)

function omSortValue(row, col) {
  const v = row.dataset[col];
  if (col === 'health' || col === 'completeness') return parseFloat(v) ?? 999;
  if (OM_SORT_ORDER[col]) return OM_SORT_ORDER[col][v] ?? 99;
  return (v || '').toLowerCase();
}

function omSortTable(col) {
  if (omSortCol === col) {
    omSortDir = omSortDir === 'asc' ? 'desc' : 'asc';
  } else {
    omSortCol = col;
    // health/completeness: asc = worst first (lowest number first)
    // categorical: asc = worst first (shift before stable)
    omSortDir = 'asc';
  }

  const tbody  = document.getElementById('om-tbody');
  // Collect only data rows (skip drilldown rows)
  const pairs  = [];
  const rows   = Array.from(tbody.querySelectorAll('tr.om-row'));
  rows.forEach(r => {
    const ddId  = 'dd-' + r.dataset.name.replace(/[ .]/g, '_');
    const ddRow = document.getElementById(ddId);
    pairs.push({ row: r, dd: ddRow });
  });

  pairs.sort((a, b) => {
    const va = omSortValue(a.row, col);
    const vb = omSortValue(b.row, col);
    if (va < vb) return omSortDir === 'asc' ? -1 : 1;
    if (va > vb) return omSortDir === 'asc' ?  1 : -1;
    return 0;
  });

  // Re-append in sorted order (keep drilldown rows immediately after their data row)
  pairs.forEach(({ row, dd }) => {
    tbody.appendChild(row);
    if (dd) tbody.appendChild(dd);
  });

  // Update header arrows
  document.querySelectorAll('.om-th').forEach(th => {
    th.classList.remove('om-sort-asc', 'om-sort-desc');
    if (th.dataset.col === col) {
      th.classList.add(omSortDir === 'asc' ? 'om-sort-asc' : 'om-sort-desc');
    }
  });
}

// ── Search / filter ───────────────────────────────────────────────────────────
function omFilterTable(query) {
  const q = query.trim().toLowerCase();
  let visible = 0;
  document.querySelectorAll('#om-tbody tr.om-row').forEach(row => {
    const name = (row.dataset.name || '').toLowerCase();
    const show = !q || name.includes(q);
    row.style.display = show ? '' : 'none';
    // Also hide drilldown row when parent is hidden
    const ddId  = 'dd-' + row.dataset.name.replace(/[ .]/g, '_');
    const ddRow = document.getElementById(ddId);
    if (ddRow) ddRow.style.display = 'none';
    if (show) visible++;
  });
  const countEl = document.getElementById('om-count');
  if (countEl) countEl.textContent = q ? `${visible} match${visible !== 1 ? 'es' : ''}` : '';
}

// ── Scroll to section + highlight column row ──────────────────────────────────
function omScrollTo(sectionId, colName) {
  const section = document.getElementById(sectionId);
  if (!section) return;

  // Open the details element if collapsed
  if (section.tagName === 'DETAILS') section.open = true;

  section.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Highlight matching row in that section after scroll settles
  setTimeout(() => {
    section.querySelectorAll('tr, .schema-row').forEach(row => {
      const code = row.querySelector('code');
      if (code && code.textContent.trim() === colName) {
        row.classList.add('om-target-highlight');
        setTimeout(() => row.classList.remove('om-target-highlight'), 2500);
      }
    });
  }, 400);
}

// ── Drill-down: toggle panel + render Plotly charts ──────────────────────────
const omRendered = new Set();

function omToggleDrilldown(colName, btn) {
  const safeId  = colName.replace(/[ .]/g, '_');
  const ddRow   = document.getElementById('dd-' + safeId);
  if (!ddRow) return;

  const isOpen  = ddRow.style.display !== 'none';

  // Close all open drilldowns
  document.querySelectorAll('.om-drilldown-row').forEach(r => { r.style.display = 'none'; });
  document.querySelectorAll('.om-expand-icon').forEach(i => { i.textContent = '▸'; });

  if (isOpen) return;   // was open → just close

  ddRow.style.display = '';
  btn.querySelector('.om-expand-icon').textContent = '▾';

  if (!omRendered.has(colName)) {
    omRendered.add(colName);
    omRenderDrilldown(colName, safeId);
  }
}

function omRenderDrilldown(colName, safeId) {
  const container = document.getElementById('dd-inner-' + safeId);
  if (!container) return;

  const c4Lookup = omBuildC4Lookup(omGetC4Data());
  const vstats   = c4Lookup[colName];

  if (!vstats || vstats.length < 2 || vstats.every(v => v.mean == null)) {
    container.innerHTML = `
      <div class="om-dd-nodata">
        No numeric distribution data available for <code>${colName}</code>.
        Check C4 Distribution Drift for details.
      </div>`;
    return;
  }

  // Filter to versions that have data
  const validStats = vstats.filter(v => v.min != null && v.max != null &&
                                        v.q25 != null && v.q50 != null && v.q75 != null);

  container.innerHTML = `
    <div class="om-dd-title">Distribution Drill-down: <code>${colName}</code></div>
    <div class="om-dd-charts">
      <div class="om-dd-chart-wrap">
        <div class="om-dd-chart-label">Quantile Trend Lines</div>
        <div id="om-qt-${safeId}" style="height:220px;"></div>
      </div>
      <div class="om-dd-chart-wrap">
        <div class="om-dd-chart-label">Distribution Ridge (reconstructed from quantiles)</div>
        <div id="om-ridge-${safeId}" style="height:220px;"></div>
      </div>
    </div>`;

  // Small delay to let DOM paint before Plotly renders
  requestAnimationFrame(() => {
    omRenderQuantileTrend(`om-qt-${safeId}`, validStats);
    omRenderRidgePlot(`om-ridge-${safeId}`, validStats);
  });
}

// ── Chart 1: Quantile Trend Lines ─────────────────────────────────────────────
function omRenderQuantileTrend(divId, vstats) {
  const labels = vstats.map(v => v.abt || '?');

  const traces = [
    { name: 'Max',    y: vstats.map(v => v.max),  mode: 'lines',         line: { dash: 'dot',   color: '#64748b', width: 1 } },
    { name: 'Q3',     y: vstats.map(v => v.q75),  mode: 'lines+markers', line: { color: '#f59e0b', width: 2 } },
    { name: 'Median', y: vstats.map(v => v.q50),  mode: 'lines+markers', line: { color: '#3b82f6', width: 2.5 } },
    { name: 'Q1',     y: vstats.map(v => v.q25),  mode: 'lines+markers', line: { color: '#22c55e', width: 2 } },
    { name: 'Min',    y: vstats.map(v => v.min),   mode: 'lines',         line: { dash: 'dot',   color: '#64748b', width: 1 } },
    { name: 'Mean',   y: vstats.map(v => v.mean),  mode: 'lines+markers', line: { color: '#a855f7', width: 1.5, dash: 'dashdot' } },
  ].map(t => ({ ...t, x: labels, type: 'scatter', marker: { size: 5 } }));

  const layout = {
    ...OM_PLOTLY_LAYOUT,
    showlegend: true,
    xaxis: { ...OM_PLOTLY_LAYOUT.xaxis, title: { text: 'Version', font: { size: 10 } } },
    yaxis: { ...OM_PLOTLY_LAYOUT.yaxis, title: { text: 'Value', font: { size: 10 } } },
  };

  Plotly.newPlot(divId, traces, layout, { responsive: true, displayModeBar: false });
}

// ── Chart 2: Ridge Plot (density reconstructed from quantiles) ────────────────
function omBuildDensity(stat) {
  // Bins: [min, q25, q50, q75, max] — each interval = 25% of data
  const pts    = [stat.min, stat.q25, stat.q50, stat.q75, stat.max];
  const widths = pts.slice(1).map((p, i) => Math.max(p - pts[i], 1e-9));
  const total  = widths.reduce((a, b) => a + b, 0);
  // Density height for each bin = 0.25 / bin_width (uniform within bin)
  const heights = widths.map(w => 0.25 / w);

  // Build step-function x/y for the "histogram" shape
  const x = [], y = [];
  pts.slice(0, -1).forEach((left, i) => {
    const right = pts[i + 1];
    const h     = heights[i];
    x.push(left, left, right, right);
    y.push(0,    h,    h,     0);
  });
  return { x, y };
}

function omRenderRidgePlot(divId, vstats) {
  const traces = [];
  const vertOffset = 0.35;   // vertical stacking gap

  vstats.forEach((stat, idx) => {
    const { x, y }  = omBuildDensity(stat);
    const color      = OM_VERSION_COLORS[idx % OM_VERSION_COLORS.length];
    const yShifted   = y.map(v => v + idx * vertOffset);
    const yBase      = new Array(y.length).fill(idx * vertOffset);

    traces.push({
      x,
      y:          yShifted,
      type:       'scatter',
      mode:       'lines',
      name:       stat.abt || `v${idx + 1}`,
      fill:       'tonexty',
      fillcolor:  color + '28',   // low-opacity fill
      line:       { color, width: 1.8 },
    });
    // invisible base line for fill reference
    traces.push({
      x,
      y:          yBase,
      type:       'scatter',
      mode:       'lines',
      showlegend: false,
      line:       { color: 'transparent', width: 0 },
      hoverinfo:  'skip',
    });
  });

  const layout = {
    ...OM_PLOTLY_LAYOUT,
    showlegend: true,
    xaxis: { ...OM_PLOTLY_LAYOUT.xaxis, title: { text: 'Value', font: { size: 10 } } },
    yaxis: { ...OM_PLOTLY_LAYOUT.yaxis,
             title: { text: 'Density (stacked)', font: { size: 10 } },
             showticklabels: false },
  };

  Plotly.newPlot(divId, traces, layout, { responsive: true, displayModeBar: false });
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {

  // Wire up header sort clicks
  document.querySelectorAll('.om-th[data-col]').forEach(th => {
    th.addEventListener('click', () => omSortTable(th.dataset.col));
  });

  // Wire up search input
  const searchEl = document.getElementById('om-search');
  if (searchEl) {
    searchEl.addEventListener('input', e => omFilterTable(e.target.value));
  }

  // Default sort: worst health score first
  omSortTable('health');

  // Initial row count
  const countEl = document.getElementById('om-count');
  if (countEl) {
    const total = document.querySelectorAll('#om-tbody tr.om-row').length;
    countEl.textContent = `${total} columns`;
  }
});