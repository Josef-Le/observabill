#!/usr/bin/env python3
"""
ObservaBill — Savings Dashboard UI
Pure rendering functions (no API calls). Python 3.11 stdlib only.
Returns HTML strings. Compose with app.py's html_page().
"""

import html
import json
import math


# ── CSS additions for the savings dashboard ────────────────────────────────────
DASHBOARD_CSS = """
/* ── Savings dashboard extras ───────────────────────────────────────────────── */
:root {
    --green:   #059669;
    --green-l: #d1fae5;
    --green-d: #047857;
    --amber:   #d97706;
    --amber-l: #fef3c7;
    --red:     #dc2626;
    --red-l:   #fee2e2;
    --blue:    #2563eb;
    --blue-l:  #dbeafe;
    --purple:  #7c3aed;
    --purple-l:#ede9fe;
    --slate:   #64748b;
    --border:  #e2e8f0;
    --bg:      #f8fafc;
    --surface: #ffffff;
    --text:    #0f172a;
    --text-2:  #475569;
}

/* Hero -------------------------------------------------------------------- */
.hero-card {
    background: linear-gradient(135deg, #022c22 0%, #064e3b 55%, #065f46 100%);
    border-radius: 16px;
    padding: 40px 44px;
    margin-bottom: 32px;
    position: relative;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(5,150,105,0.22), 0 2px 8px rgba(0,0,0,0.12);
}
.hero-card::before {
    content: '';
    position: absolute;
    top: -60px; right: -60px;
    width: 260px; height: 260px;
    border-radius: 50%;
    background: rgba(16,185,129,0.12);
    pointer-events: none;
}
.hero-eyebrow {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #6ee7b7;
    margin-bottom: 10px;
}
.hero-amount {
    font-size: clamp(2.8rem, 6vw, 4.2rem);
    font-weight: 800;
    color: #ecfdf5;
    letter-spacing: -2px;
    line-height: 1;
    font-variant-numeric: tabular-nums;
    margin-bottom: 10px;
}
.hero-amount span.currency {
    font-size: 55%;
    font-weight: 600;
    color: #6ee7b7;
    letter-spacing: 0;
    vertical-align: super;
    margin-right: 3px;
}
.hero-sub {
    color: #a7f3d0;
    font-size: 0.97rem;
    margin-bottom: 24px;
}
.hero-sub strong { color: #ecfdf5; }
.hero-sparkline {
    display: flex;
    align-items: flex-end;
    gap: 2px;
}
.hero-notes {
    margin-top: 20px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}
.hero-note-chip {
    background: rgba(255,255,255,0.1);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 20px;
    padding: 3px 12px;
    font-size: 0.78rem;
    color: #d1fae5;
}

/* Gear / settings icon ---------------------------------------------------- */
.settings-link {
    position: absolute;
    top: 20px; right: 22px;
    background: rgba(255,255,255,0.1);
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 8px;
    padding: 7px 10px;
    color: #d1fae5;
    text-decoration: none;
    font-size: 1rem;
    cursor: pointer;
    transition: background 0.15s;
    display: flex;
    align-items: center;
    gap: 6px;
}
.settings-link:hover {
    background: rgba(255,255,255,0.18);
    text-decoration: none;
    color: #ecfdf5;
}

/* Lever table ------------------------------------------------------------- */
.lever-table-wrap {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    margin-bottom: 28px;
}
.lever-table-wrap table {
    font-size: 0.88rem;
}
.lever-table-wrap th {
    background: #f1f5f9;
    padding: 11px 16px;
    font-size: 0.72rem;
    white-space: nowrap;
}
.lever-table-wrap td {
    padding: 14px 16px;
    vertical-align: middle;
}
.lever-table-wrap tr:hover td {
    background: #f8fafc;
}

/* Savings amount in table */
.savings-amt {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--green);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
.savings-pct {
    font-size: 0.75rem;
    color: var(--green-d);
    font-weight: 600;
}

/* Badges ------------------------------------------------------------------ */
.badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border-radius: 20px;
    padding: 3px 10px;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.3px;
    white-space: nowrap;
}
.badge-logs   { background: var(--blue-l);   color: #1e40af; }
.badge-metrics{ background: var(--purple-l); color: #5b21b6; }

.effort-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 0.72rem;
    font-weight: 700;
    white-space: nowrap;
}
.effort-low  { background: var(--green-l);  color: var(--green-d); }
.effort-med  { background: var(--amber-l);  color: #92400e; }
.effort-high { background: var(--red-l);    color: #991b1b; }

.conf-chip {
    display: inline-block;
    background: #f1f5f9;
    color: #475569;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 0.72rem;
    font-weight: 600;
    white-space: nowrap;
}

/* View-fix button --------------------------------------------------------- */
.btn-viewfix {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--slate);
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
}
.btn-viewfix:hover {
    background: #f0fdf4;
    border-color: var(--green);
    color: var(--green);
}
.btn-viewfix .arrow { display: inline-block; transition: transform 0.15s; }
.btn-viewfix.open .arrow { transform: rotate(90deg); }

/* Drilldown panel --------------------------------------------------------- */
.drilldown-panel {
    display: none;
    background: #f8fafc;
    border-top: 1px solid var(--border);
    padding: 24px 20px;
    animation: slideDown 0.18s ease;
}
.drilldown-panel.open { display: block; }

@keyframes slideDown {
    from { opacity: 0; transform: translateY(-6px); }
    to   { opacity: 1; transform: translateY(0); }
}

.drilldown-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
}
@media (max-width: 760px) {
    .drilldown-grid { grid-template-columns: 1fr; }
}

.dd-section-label {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--slate);
    margin-bottom: 10px;
}

/* Evidence bar chart ------------------------------------------------------ */
.bar-chart-wrap {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
}
.bar-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
}
.bar-row:last-child { margin-bottom: 0; }
.bar-label {
    font-size: 0.78rem;
    color: var(--text-2);
    min-width: 110px;
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex-shrink: 0;
}
.bar-track {
    flex: 1;
    background: #f1f5f9;
    border-radius: 4px;
    height: 10px;
    overflow: hidden;
}
.bar-fill {
    height: 100%;
    border-radius: 4px;
    background: linear-gradient(90deg, #059669, #10b981);
    transition: width 0.4s ease;
}
.bar-fill-danger {
    background: linear-gradient(90deg, #dc2626, #ef4444);
}
.bar-val {
    font-size: 0.78rem;
    font-weight: 700;
    color: var(--green);
    font-variant-numeric: tabular-nums;
    min-width: 60px;
    text-align: right;
}

/* Config code block ------------------------------------------------------- */
.config-wrap {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
}
.config-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    background: #1e293b;
    border-bottom: 1px solid #334155;
}
.config-verb {
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.78rem;
    font-weight: 700;
}
.verb-get    { color: #34d399; }
.verb-post   { color: #60a5fa; }
.verb-put    { color: #fbbf24; }
.verb-patch  { color: #fb923c; }
.verb-delete { color: #f87171; }
.config-endpoint {
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.78rem;
    color: #cbd5e1;
    margin-left: 8px;
}
.btn-copy {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 5px;
    padding: 3px 10px;
    font-size: 0.72rem;
    font-weight: 600;
    color: #94a3b8;
    cursor: pointer;
    transition: all 0.15s;
}
.btn-copy:hover { background: rgba(255,255,255,0.15); color: #e2e8f0; }
.btn-copy.copied { color: #34d399; border-color: #34d399; }

pre.config-body {
    margin: 0;
    padding: 14px 16px;
    background: #0f172a;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.78rem;
    line-height: 1.65;
    overflow-x: auto;
    color: #e2e8f0;
}
/* Syntax highlight colours (CSS classes, no JS syntax engine) */
.syn-key  { color: #93c5fd; }
.syn-str  { color: #86efac; }
.syn-num  { color: #fda4af; }
.syn-bool { color: #fdba74; }
.syn-null { color: #9ca3af; }

/* Before / after projection ----------------------------------------------- */
.projection-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
}
.proj-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.proj-label { font-size: 0.82rem; color: var(--text-2); }
.proj-val   {
    font-size: 1.05rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}
.proj-val-before { color: var(--text); }
.proj-val-after  { color: var(--green); }
.proj-divider    { border: none; border-top: 1px solid var(--border); margin: 0; }
.proj-saved-row  {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--green-l);
    border-radius: 7px;
    padding: 10px 14px;
}
.proj-saved-label { font-size: 0.82rem; font-weight: 600; color: var(--green-d); }
.proj-saved-val   {
    font-size: 1.25rem;
    font-weight: 800;
    color: var(--green);
    font-variant-numeric: tabular-nums;
}

/* Apply button ------------------------------------------------------------ */
.apply-row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
}
.btn-apply {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--green);
    color: #fff;
    border: none;
    border-radius: 7px;
    padding: 9px 20px;
    font-size: 0.88rem;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.15s;
}
.btn-apply:hover { background: var(--green-d); }
.btn-apply:disabled, .btn-apply[disabled] {
    background: #e2e8f0;
    color: #94a3b8;
    cursor: not-allowed;
}
.apply-tooltip {
    font-size: 0.75rem;
    color: var(--slate);
    display: flex;
    align-items: center;
    gap: 5px;
}
.apply-tooltip svg { flex-shrink: 0; }

/* Heatmap ----------------------------------------------------------------- */
.heatmap-wrap {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    overflow-x: auto;
    margin-bottom: 16px;
}
.heatmap-grid {
    display: grid;
    gap: 3px;
}
.heatmap-cell {
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.62rem;
    font-weight: 600;
    color: #fff;
    min-width: 36px;
    min-height: 26px;
    cursor: default;
}
.heatmap-label-row {
    display: grid;
    gap: 3px;
    font-size: 0.65rem;
    color: var(--slate);
    font-weight: 600;
    margin-bottom: 4px;
}
.heatmap-row-label {
    font-size: 0.65rem;
    color: var(--slate);
    font-weight: 600;
    display: flex;
    align-items: center;
    padding-right: 6px;
    min-width: 80px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.heatmap-legend {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 10px;
    font-size: 0.7rem;
    color: var(--slate);
}
.heatmap-legend-grad {
    width: 80px;
    height: 8px;
    border-radius: 4px;
    background: linear-gradient(90deg, #bbf7d0, #fbbf24, #dc2626);
}

/* Settings panel ---------------------------------------------------------- */
.settings-panel {
    display: none;
    position: fixed;
    top: 0; right: 0; bottom: 0;
    width: min(380px, 92vw);
    background: var(--surface);
    border-left: 1px solid var(--border);
    box-shadow: -8px 0 32px rgba(0,0,0,0.12);
    z-index: 999;
    padding: 28px 24px;
    overflow-y: auto;
    animation: slideInRight 0.2s ease;
}
.settings-panel.open { display: block; }
@keyframes slideInRight {
    from { transform: translateX(40px); opacity: 0; }
    to   { transform: translateX(0);    opacity: 1; }
}
.settings-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.25);
    z-index: 998;
}
.settings-overlay.open { display: block; }
.settings-close {
    position: absolute;
    top: 16px; right: 16px;
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 0.82rem;
    cursor: pointer;
    color: var(--slate);
}
.settings-close:hover { background: var(--bg); }
.settings-note {
    font-size: 0.82rem;
    color: var(--text-2);
    background: var(--amber-l);
    border: 1px solid #fde68a;
    border-radius: 7px;
    padding: 10px 14px;
    margin-top: 12px;
}
.settings-price-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.2px;
    margin-bottom: 16px;
}
.settings-price-badge-derived { background: var(--green-l);  color: var(--green-d); }
.settings-price-badge-list    { background: var(--amber-l);  color: #92400e; }
.settings-price-badge-custom  { background: var(--blue-l);   color: #1e40af; }
.settings-price-input {
    width: 100%;
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 0.92rem;
    padding: 8px 12px;
    outline: none;
    margin-bottom: 12px;
    font-variant-numeric: tabular-nums;
}
.settings-price-input:focus { border-color: var(--green); box-shadow: 0 0 0 3px #05966922; }

/* Scope checklist card ---------------------------------------------------- */
.scope-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 22px;
    margin-bottom: 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.scope-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
    flex-wrap: wrap;
    gap: 8px;
}
.scope-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid #f1f5f9;
    font-size: 0.87rem;
}
.scope-row:last-of-type { border-bottom: none; }
.scope-icon-ok  { color: var(--green); font-size: 1rem; flex-shrink: 0; margin-top: 1px; }
.scope-icon-err { color: #94a3b8;      font-size: 1rem; flex-shrink: 0; margin-top: 1px; }
.scope-name {
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.78rem;
    font-weight: 700;
    color: var(--text);
    min-width: 120px;
    flex-shrink: 0;
}
.scope-desc { color: var(--text-2); flex: 1; font-size: 0.82rem; }
.scope-unlock-note {
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-left: 3px solid #f59e0b;
    border-radius: 7px;
    padding: 12px 16px;
    margin-top: 14px;
    font-size: 0.83rem;
    color: #78350f;
}
.scope-unlock-note ul { margin: 6px 0 0 16px; padding: 0; }
.scope-unlock-note li { margin-bottom: 4px; }
.scope-key-guide {
    margin-top: 12px;
}
.scope-key-guide summary {
    cursor: pointer;
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--blue);
    user-select: none;
    padding: 6px 0;
}
.scope-key-guide-body {
    background: #f8fafc;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    margin-top: 8px;
    font-size: 0.82rem;
    color: var(--text-2);
}
.scope-key-guide-body ol { margin: 8px 0 0 18px; padding: 0; }
.scope-key-guide-body li { margin-bottom: 6px; line-height: 1.55; }
.scope-key-guide-body code {
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.78rem;
    background: #e2e8f0;
    padding: 1px 5px;
    border-radius: 4px;
    color: var(--text);
}
.scope-all-ok {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    background: var(--green-l);
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--green-d);
    margin-top: 4px;
}

/* Detection query collapsible --------------------------------------------- */
.detection-collapsible {
    margin-top: 16px;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
}
.detection-collapsible summary {
    cursor: pointer;
    padding: 10px 14px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--slate);
    background: #f8fafc;
    user-select: none;
    display: flex;
    align-items: center;
    gap: 7px;
}
.detection-collapsible summary:hover { background: #f1f5f9; }
.detection-collapsible-body {
    padding: 16px;
    background: var(--surface);
    border-top: 1px solid var(--border);
}
pre.detection-query {
    margin: 0 0 12px 0;
    padding: 12px 14px;
    background: #0f172a;
    border-radius: 7px;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.78rem;
    line-height: 1.6;
    color: #e2e8f0;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
}
.detection-why {
    font-size: 0.83rem;
    color: var(--text-2);
    line-height: 1.6;
}

/* Summary stats bar ------------------------------------------------------- */
.stats-bar {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 14px;
    margin-bottom: 28px;
}
.stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.stat-label {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--slate);
    margin-bottom: 6px;
}
.stat-value {
    font-size: 1.5rem;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
    line-height: 1;
}
.stat-value-green  { color: var(--green); }
.stat-value-amber  { color: var(--amber); }
.stat-value-purple { color: var(--purple); }
.stat-value-blue   { color: var(--blue); }
.stat-sub {
    font-size: 0.74rem;
    color: var(--slate);
    margin-top: 4px;
}

/* Mobile responsive ------------------------------------------------------- */
@media (max-width: 760px) {
    .hero-card { padding: 28px 22px; }
    .hero-amount { font-size: 2.8rem; }
    .lever-table-wrap th:nth-child(4),
    .lever-table-wrap td:nth-child(4) { display: none; }
    .drilldown-grid { grid-template-columns: 1fr; }
    .stats-bar { grid-template-columns: 1fr 1fr; }
}
"""


# ── minimal JS for interactivity ───────────────────────────────────────────────
DASHBOARD_JS = """
<script>
// ── Expand / collapse drilldowns ─────────────────────────────────────────────
function toggleDrilldown(id) {
    var panel = document.getElementById('dd-' + id);
    var btn   = document.getElementById('btn-' + id);
    if (!panel || !btn) return;
    var isOpen = panel.classList.contains('open');
    // Close all others
    document.querySelectorAll('.drilldown-panel').forEach(function(p) {
        p.classList.remove('open');
    });
    document.querySelectorAll('.btn-viewfix').forEach(function(b) {
        b.classList.remove('open');
        b.querySelector('.arrow').textContent = '▶';
        b.querySelector('.btn-label').textContent = 'View fix';
    });
    if (!isOpen) {
        panel.classList.add('open');
        btn.classList.add('open');
        btn.querySelector('.arrow').textContent = '▼';
        btn.querySelector('.btn-label').textContent = 'Hide';
        // Animate bars
        setTimeout(function() {
            panel.querySelectorAll('.bar-fill').forEach(function(bar) {
                bar.style.width = bar.dataset.width;
            });
        }, 30);
    }
}

// ── Copy-to-clipboard ─────────────────────────────────────────────────────────
function copyConfig(id) {
    var el  = document.getElementById('cfg-' + id);
    var btn = document.getElementById('copybtn-' + id);
    if (!el || !btn) return;
    var text = el.textContent || el.innerText;
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(function() {
            btn.textContent = '✓ Copied';
            btn.classList.add('copied');
            setTimeout(function() {
                btn.textContent = 'Copy';
                btn.classList.remove('copied');
            }, 2000);
        });
    } else {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        btn.textContent = '✓ Copied';
        btn.classList.add('copied');
        setTimeout(function() {
            btn.textContent = 'Copy';
            btn.classList.remove('copied');
        }, 2000);
    }
}

// ── Settings panel ────────────────────────────────────────────────────────────
// Default rates (must match Python _DEFAULTS in render_settings_panel)
var _PRICE_DEFAULTS = {
    'indexed_log_per_million': 0.0125,
    'ingested_log_per_gb':     0.10,
    'custom_metric_per_month': 0.05
};

function openSettings() {
    document.getElementById('settings-panel').classList.add('open');
    document.getElementById('settings-overlay').classList.add('open');
    // Restore values from localStorage
    var keys = ['indexed_log_per_million', 'ingested_log_per_gb', 'custom_metric_per_month'];
    keys.forEach(function(k) {
        var val = localStorage.getItem('price_' + k);
        var el  = document.getElementById('sp-price_' + k);
        if (el && val !== null) el.value = val;
    });
}
function closeSettings() {
    document.getElementById('settings-panel').classList.remove('open');
    document.getElementById('settings-overlay').classList.remove('open');
}
function saveSettings() {
    var keys = ['indexed_log_per_million', 'ingested_log_per_gb', 'custom_metric_per_month'];
    var newRates = {};
    keys.forEach(function(k) {
        var el = document.getElementById('sp-price_' + k);
        if (!el) return;
        var v = parseFloat(el.value);
        if (isNaN(v) || v <= 0) v = _PRICE_DEFAULTS[k];
        newRates[k] = v;
        localStorage.setItem('price_' + k, v);
    });

    // Client-side rescale: walk all [data-usd][data-price-key] elements
    // and recompute displayed $ = data-usd * (new_rate / default_rate)
    document.querySelectorAll('[data-usd][data-price-key]').forEach(function(el) {
        var usd      = parseFloat(el.getAttribute('data-usd'));
        var priceKey = el.getAttribute('data-price-key');
        if (isNaN(usd)) return;

        var newAmt;
        if (priceKey === 'mixed' || !_PRICE_DEFAULTS[priceKey]) {
            // Mixed / unknown: scale by indexed_log_per_million (dominant rate)
            var defaultRate = _PRICE_DEFAULTS['indexed_log_per_million'];
            var newRate     = newRates['indexed_log_per_million'] || defaultRate;
            newAmt = usd * (newRate / defaultRate);
        } else {
            var defaultRate = _PRICE_DEFAULTS[priceKey];
            var newRate     = newRates[priceKey] || defaultRate;
            newAmt = usd * (newRate / defaultRate);
        }
        // Format like Python _fmt_usd
        el.textContent = '$' + newAmt.toLocaleString('en-US', {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        });
    });

    // Update source badge to "Using your custom rates"
    var badge = document.getElementById('price-source-badge');
    if (badge) {
        badge.className = 'settings-price-badge settings-price-badge-custom';
        badge.innerHTML = '&#9654; Using your custom rates';
    }

    // Show confirmation
    var conf = document.getElementById('settings-applied-msg');
    if (conf) {
        conf.style.display = 'block';
        setTimeout(function() { conf.style.display = 'none'; }, 3000);
    }

    closeSettings();
}
document.addEventListener('DOMContentLoaded', function() {
    var overlay = document.getElementById('settings-overlay');
    if (overlay) overlay.addEventListener('click', closeSettings);
    // Pre-populate inputs from localStorage on load
    var keys = ['indexed_log_per_million', 'ingested_log_per_gb', 'custom_metric_per_month'];
    keys.forEach(function(k) {
        var val = localStorage.getItem('price_' + k);
        var el  = document.getElementById('sp-price_' + k);
        if (el && val !== null) el.value = val;
    });
});
</script>
"""


# ── helpers ────────────────────────────────────────────────────────────────────

def _esc(s):
    return html.escape(str(s))


def _fmt_usd(v, decimals=0):
    """Format a float as a compact USD string."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "$?"
    if v < 0:
        return f"-${abs(v):,.{decimals}f}"
    return f"${v:,.{decimals}f}"


def _effort_pill(effort: str) -> str:
    label = {"low": "Low", "medium": "Medium", "high": "High"}.get(effort, effort)
    dot   = {"low": "●", "medium": "●", "high": "●"}.get(effort, "●")
    cls   = {"low": "effort-low", "medium": "effort-med", "high": "effort-high"}.get(effort, "effort-med")
    return f'<span class="effort-pill {_esc(cls)}">{dot} {_esc(label)}</span>'


def _category_badge(cat: str) -> str:
    if cat == "logs":
        return '<span class="badge badge-logs">📋 Logs</span>'
    elif cat == "metrics":
        return '<span class="badge badge-metrics">📊 Metrics</span>'
    return f'<span class="badge" style="background:#f1f5f9;color:#475569;">{_esc(cat)}</span>'


def _conf_chip(conf) -> str:
    return f'<span class="conf-chip">{_esc(str(conf))}</span>'


def _verb_span(verb: str) -> str:
    v = verb.upper()
    cls = {
        "GET": "verb-get", "POST": "verb-post",
        "PUT": "verb-put", "PATCH": "verb-patch", "DELETE": "verb-delete",
    }.get(v, "verb-get")
    return f'<span class="config-verb {cls}">{_esc(v)}</span>'


def _syntax_highlight_json(obj) -> str:
    """Render a dict as syntax-coloured HTML (no external deps)."""
    raw = json.dumps(obj, indent=2)
    lines = []
    for line in raw.split("\n"):
        # Escape the whole line first, then re-colour tokens
        escaped = html.escape(line)

        import re
        # key: "word":
        escaped = re.sub(
            r'&quot;([^&]+)&quot;:',
            r'<span class="syn-key">&quot;\1&quot;</span>:',
            escaped
        )
        # string value (after a colon or standalone in array)
        escaped = re.sub(
            r': &quot;([^&]*)&quot;',
            r': <span class="syn-str">&quot;\1&quot;</span>',
            escaped
        )
        # standalone string in array
        escaped = re.sub(
            r'^(\s*)&quot;([^&]*)&quot;(,?)$',
            r'\1<span class="syn-str">&quot;\2&quot;</span>\3',
            escaped
        )
        # numbers
        escaped = re.sub(
            r': (-?\d+\.?\d*)',
            r': <span class="syn-num">\1</span>',
            escaped
        )
        # booleans
        escaped = re.sub(
            r'\b(true|false)\b',
            r'<span class="syn-bool">\1</span>',
            escaped
        )
        # null
        escaped = re.sub(
            r'\bnull\b',
            r'<span class="syn-null">null</span>',
            escaped
        )
        lines.append(escaped)
    return "\n".join(lines)


def _heatmap_color(ratio: float) -> str:
    """Map 0–1 ratio to a green→amber→red hex color."""
    ratio = max(0.0, min(1.0, ratio))
    if ratio < 0.5:
        # green → amber
        t = ratio * 2
        r = int(5   + t * (217 - 5))
        g = int(150 + t * (119 - 150))
        b = int(105 + t * (6   - 105))
    else:
        # amber → red
        t = (ratio - 0.5) * 2
        r = int(217 + t * (220 - 217))
        g = int(119 + t * (38  - 119))
        b = int(6   + t * (38  -   6))
    return f"#{r:02x}{g:02x}{b:02x}"


# ── render_hero ────────────────────────────────────────────────────────────────

def render_hero(scan: dict) -> str:
    """
    Big "Monthly Estimated Waste" card: huge green $ figure, subtitle,
    inline-SVG sparkline with recent spike highlighted.
    """
    total   = float(scan.get("total_monthly_waste_usd", 0))
    region  = _esc(scan.get("region", "US"))
    opps    = scan.get("opportunities", [])
    notes   = scan.get("notes", [])
    sparkline = scan.get("sparkline", [])

    n_logs    = sum(1 for o in opps if o.get("category") == "logs")
    n_metrics = sum(1 for o in opps if o.get("category") == "metrics")
    opp_desc  = []
    if n_logs:
        opp_desc.append(f"<strong>{n_logs}</strong> log lever{'s' if n_logs != 1 else ''}")
    if n_metrics:
        opp_desc.append(f"<strong>{n_metrics}</strong> metric lever{'s' if n_metrics != 1 else ''}")
    opp_str = " &amp; ".join(opp_desc) if opp_desc else f"<strong>{len(opps)}</strong> levers"

    # SVG sparkline
    svg_html = ""
    if sparkline and len(sparkline) > 1:
        w, h = 220, 48
        vals   = [float(v) for v in sparkline]
        lo, hi = min(vals), max(vals)
        rng    = hi - lo if hi != lo else 1.0
        pad    = 4
        step   = (w - pad * 2) / (len(vals) - 1)

        def px(i, v):
            x = pad + i * step
            y = h - pad - (v - lo) / rng * (h - pad * 2)
            return x, y

        # polyline points
        pts = " ".join(f"{px(i, v)[0]:.1f},{px(i, v)[1]:.1f}" for i, v in enumerate(vals))

        # Area fill
        area_pts = f"{pad},{h} " + pts + f" {pad + (len(vals)-1)*step:.1f},{h}"

        # Highlight highest point (spike)
        max_i  = vals.index(hi)
        spike_x, spike_y = px(max_i, hi)

        svg_html = f"""
<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;">
  <defs>
    <linearGradient id="spk-fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="#34d399" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#34d399" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <polygon points="{_esc(area_pts)}" fill="url(#spk-fill)"/>
  <polyline points="{_esc(pts)}"
            fill="none" stroke="#34d399" stroke-width="2"
            stroke-linejoin="round" stroke-linecap="round"/>
  <!-- spike highlight -->
  <circle cx="{spike_x:.1f}" cy="{spike_y:.1f}" r="5"
          fill="#fbbf24" stroke="#fff" stroke-width="2"/>
</svg>"""

    # Notes chips
    notes_html = ""
    if notes:
        chips = "".join(f'<span class="hero-note-chip">{_esc(n)}</span>' for n in notes)
        notes_html = f'<div class="hero-notes">{chips}</div>'

    return f"""
<div class="hero-card">
  <a class="settings-link" onclick="openSettings(); return false;" href="#" title="Price settings">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2" stroke-linecap="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06
               a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09
               A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83
               l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09
               A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83
               l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09
               a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83
               l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09
               a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
    Price settings
  </a>

  <div class="hero-eyebrow">Monthly Estimated Waste · {region}</div>
  <div class="hero-amount"
       data-usd="{total}"
       data-price-key="mixed">
    <span class="currency">$</span>{total:,.0f}
  </div>
  <div class="hero-sub">
    {opp_desc and (opp_str + " identified") or "No opportunities found"}
  </div>

  {svg_html and f'<div class="hero-sparkline" title="30-day cost trend · spike = highest point">{svg_html}</div>' or ''}

  {notes_html}
</div>"""


# ── render_lever_table ─────────────────────────────────────────────────────────

def render_lever_table(scan: dict, write_enabled: bool, apply_token: str = "") -> str:
    """
    Ranked table: title | category badge | monthly savings $ | effort pill |
    confidence | View fix button (expands drilldown inline).
    """
    opps = scan.get("opportunities", [])
    if not opps:
        return '<div class="card" style="text-align:center;color:#64748b;padding:40px;">No savings opportunities found.</div>'

    # Sum for "total recoverable" callout
    total_recoverable = sum(float(o.get("monthly_savings_usd", 0)) for o in opps)

    rows_html = ""
    for opp in opps:
        oid      = _esc(opp.get("id", ""))
        title    = _esc(opp.get("title", ""))
        cat      = opp.get("category", "")
        savings  = float(opp.get("monthly_savings_usd", 0))
        pct      = _esc(opp.get("savings_pct", ""))
        effort   = opp.get("effort", "medium")
        conf     = opp.get("confidence", "")
        price_key = "custom_metric_per_month" if cat == "metrics" else "indexed_log_per_million"

        dd_html = render_drilldown(opp, write_enabled, apply_token=apply_token)

        rows_html += f"""
<tr>
  <td>
    <div style="font-weight:600;font-size:0.88rem;color:#0f172a;">{title}</div>
    <div style="font-size:0.75rem;color:#64748b;margin-top:2px;">{_esc(opp.get('summary','')[:80])}{'…' if len(opp.get('summary','')) > 80 else ''}</div>
  </td>
  <td style="white-space:nowrap;">{_category_badge(cat)}</td>
  <td>
    <div class="savings-amt"
         data-usd="{savings}"
         data-price-key="{_esc(price_key)}">{_fmt_usd(savings)}</div>
    {f'<div class="savings-pct">↓ {pct}</div>' if pct else ''}
  </td>
  <td>{_effort_pill(effort)}</td>
  <td>{_conf_chip(conf)}</td>
  <td>
    <button class="btn-viewfix" id="btn-{oid}" onclick="toggleDrilldown('{oid}')">
      <span class="arrow">▶</span>
      <span class="btn-label">View fix</span>
    </button>
  </td>
</tr>
<tr>
  <td colspan="6" style="padding:0; border-bottom:1px solid #e2e8f0;">
    <div class="drilldown-panel" id="dd-{oid}">
      {dd_html}
    </div>
  </td>
</tr>"""

    return f"""
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
  <h2 style="margin:0;">Savings Opportunities</h2>
  <span style="font-size:0.82rem;color:#475569;">
    Total recoverable: <strong style="color:#059669;"
      data-usd="{total_recoverable}"
      data-price-key="mixed">{_fmt_usd(total_recoverable)}</strong>/mo
    across <strong>{len(opps)}</strong> lever{'s' if len(opps) != 1 else ''}
  </span>
</div>
<div class="lever-table-wrap">
<table>
  <thead>
    <tr>
      <th>Opportunity</th>
      <th>Category</th>
      <th>Monthly savings</th>
      <th>Effort</th>
      <th>Confidence</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
</div>"""


# ── render_drilldown ───────────────────────────────────────────────────────────

def render_drilldown(opp: dict, write_enabled: bool, apply_token: str = "") -> str:
    """
    Per-lever detail panel:
    (a) horizontal bar chart of evidence
    (b) syntax-highlighted config block + Copy button
    (c) before/after projection card
    (d) Apply to Datadog button (form — opp_id + apply_token only, NO keys)
    """
    oid        = _esc(opp.get("id", ""))
    lever      = _esc(opp.get("lever", ""))
    savings    = float(opp.get("monthly_savings_usd", 0))
    evidence   = opp.get("evidence", [])
    cfg        = opp.get("generated_config", {})
    needs_write = opp.get("needs_write_scope", False)
    cat        = opp.get("category", "")

    # (a) Bar chart of evidence
    bar_html = _render_evidence_bars(evidence, oid)

    # (b) Config block
    cfg_html = _render_config_block(cfg, oid)

    # (c) Before / after projection
    proj_html = _render_projection(opp, savings)

    # (d) Apply form (token only, never any key)
    apply_html = _render_apply_button(oid, write_enabled, needs_write, apply_token=apply_token)

    # Optional heatmap for high_cardinality_metric levers
    heatmap_html = ""
    if lever in ("high_cardinality_metric", "high_cardinality_metrics"):
        heatmap_html = render_heatmap(opp)

    # (e) "How we found this" collapsible — detection_query + why
    detection_html = _render_detection_block(opp)

    return f"""
<div style="margin-bottom:14px;">
  <div class="dd-section-label">Lever: {lever} &nbsp;·&nbsp; {_category_badge(cat)}</div>
  <p style="font-size:0.85rem;color:#475569;margin:0;">{_esc(opp.get('summary',''))}</p>
</div>

{heatmap_html}

<div class="drilldown-grid">
  <div>
    <div class="dd-section-label">Top cost drivers</div>
    {bar_html}
  </div>
  <div>
    <div class="dd-section-label">Projected impact</div>
    {proj_html}
  </div>
</div>

<div class="dd-section-label">Generated remediation config</div>
{cfg_html}

{detection_html}

{apply_html}"""


def _render_evidence_bars(evidence: list, oid: str) -> str:
    if not evidence:
        return '<div class="bar-chart-wrap" style="color:#94a3b8;font-size:0.8rem;">No evidence data.</div>'

    # Sort by cost descending, take top 6
    items = sorted(evidence, key=lambda e: float(e.get("cost_usd", 0)), reverse=True)[:6]
    max_cost = float(items[0].get("cost_usd", 1)) if items else 1.0

    bars = ""
    for item in items:
        label   = str(item.get("label", ""))
        cost    = float(item.get("cost_usd", 0))
        vol     = item.get("volume", "")
        ratio   = cost / max_cost if max_cost else 0
        pct_w   = max(4, int(ratio * 100))
        vol_str = f" · {_esc(str(vol))}" if vol else ""
        is_top  = (cost == max_cost)

        bars += f"""
<div class="bar-row">
  <span class="bar-label" title="{_esc(label)}">{_esc(label[:18])}{'…' if len(label) > 18 else ''}</span>
  <div class="bar-track">
    <div class="bar-fill {'bar-fill-danger' if is_top else ''}"
         style="width:0;"
         data-width="{pct_w}%"></div>
  </div>
  <span class="bar-val" style="{'color:#dc2626;' if is_top else ''}">{_fmt_usd(cost)}{vol_str}</span>
</div>"""

    return f'<div class="bar-chart-wrap">{bars}</div>'


def _render_config_block(cfg: dict, oid: str) -> str:
    if not cfg:
        return '<div style="color:#94a3b8;font-size:0.8rem;margin-bottom:16px;">No config generated.</div>'

    verb     = str(cfg.get("verb", "GET")).upper()
    endpoint = str(cfg.get("endpoint", ""))
    payload  = cfg.get("payload", {})

    payload_json = _syntax_highlight_json(payload) if payload else ""
    payload_block = f"\n{payload_json}" if payload_json else ""

    # Build the plain-text version that gets copied
    plain_cfg = f"{verb} {endpoint}"
    if payload:
        plain_cfg += "\n\n" + json.dumps(payload, indent=2)

    return f"""
<div class="config-wrap" style="margin-bottom:16px;">
  <div class="config-header">
    <div>{_verb_span(verb)}<span class="config-endpoint">{_esc(endpoint)}</span></div>
    <button class="btn-copy" id="copybtn-{oid}"
            onclick="copyConfig('{oid}')">Copy</button>
  </div>
  <pre class="config-body" id="cfg-{oid}">{_esc(plain_cfg)}</pre>
</div>
<div class="config-wrap" style="margin-bottom:16px;">
  <div class="config-header">
    <span style="font-size:0.72rem;color:#64748b;font-family:monospace;">payload</span>
  </div>
  <pre class="config-body">{payload_json if payload_json else '<span style="color:#475569;">// no payload</span>'}</pre>
</div>"""


def _render_projection(opp: dict, savings: float) -> str:
    evidence = opp.get("evidence", [])
    current_cost = sum(float(e.get("cost_usd", 0)) for e in evidence)
    after_cost   = max(0.0, current_cost - savings)
    pct          = opp.get("savings_pct", "")
    cat          = opp.get("category", "")
    price_key    = "custom_metric_per_month" if cat == "metrics" else "indexed_log_per_million"

    return f"""
<div class="projection-card">
  <div class="proj-row">
    <span class="proj-label">Current monthly cost</span>
    <span class="proj-val proj-val-before"
          data-usd="{current_cost}"
          data-price-key="{_esc(price_key)}">{_fmt_usd(current_cost)}</span>
  </div>
  <hr class="proj-divider">
  <div class="proj-row">
    <span class="proj-label">After remediation</span>
    <span class="proj-val proj-val-after"
          data-usd="{after_cost}"
          data-price-key="{_esc(price_key)}">{_fmt_usd(after_cost)}</span>
  </div>
  <div class="proj-saved-row">
    <span class="proj-saved-label">
      Estimated monthly saving{f' ({_esc(pct)})' if pct else ''}
    </span>
    <span class="proj-saved-val"
          data-usd="{savings}"
          data-price-key="{_esc(price_key)}">{_fmt_usd(savings)}</span>
  </div>
  <div style="font-size:0.72rem;color:#94a3b8;">
    Savings are model-estimated. Actual results vary by usage pattern.
  </div>
</div>"""


def _render_detection_block(opp: dict) -> str:
    """
    Collapsible 'How we found this' section. Renders only when detection_query
    or why is present in the opportunity dict. Returns empty string otherwise.
    """
    query = opp.get("detection_query", "")
    why   = opp.get("why", "")
    if not query and not why:
        return ""

    query_html = ""
    if query:
        query_html = f"""
<div style="margin-bottom:10px;">
  <div class="dd-section-label" style="margin-bottom:6px;">Detection query</div>
  <pre class="detection-query">{_esc(query)}</pre>
</div>"""

    why_html = ""
    if why:
        why_html = f"""
<div>
  <div class="dd-section-label" style="margin-bottom:4px;">Why this was flagged</div>
  <div class="detection-why">{_esc(why)}</div>
</div>"""

    return f"""
<details class="detection-collapsible">
  <summary>
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
    </svg>
    How we found this
  </summary>
  <div class="detection-collapsible-body">
    {query_html}
    {why_html}
  </div>
</details>"""


def _render_apply_button(oid: str, write_enabled: bool, needs_write: bool,
                         apply_token: str = "") -> str:
    """
    Render the apply control.

    When can_apply is True: a real <form> POSTing to /apply with ONLY opp_id +
    apply_token hidden fields (NO api_key / app_key / write_key ever in HTML).

    When disabled: a non-submittable button with an explanatory tooltip.
    """
    can_apply = write_enabled and needs_write and bool(apply_token)

    if can_apply:
        # Escape both values for HTML attribute context
        safe_oid   = _esc(oid)
        safe_token = _esc(apply_token)
        return f"""
<div class="apply-row">
  <form method="POST" action="/apply" style="display:inline;">
    <input type="hidden" name="opp_id" value="{safe_oid}">
    <input type="hidden" name="apply_token" value="{safe_token}">
    <button type="submit" class="btn-apply">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="20 6 9 17 4 12"/>
      </svg>
      Apply to Datadog
    </button>
  </form>
</div>"""

    tooltip_text = (
        "Add a write key to enable one-click apply"
        if not write_enabled
        else "This fix does not require API write access"
    )
    return f"""
<div class="apply-row">
  <button class="btn-apply" disabled title="{_esc(tooltip_text)}">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
      <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
    </svg>
    Apply to Datadog
  </button>
  <span class="apply-tooltip">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#94a3b8"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="10"/>
      <line x1="12" y1="8" x2="12" y2="12"/>
      <line x1="12" y1="16" x2="12.01" y2="16"/>
    </svg>
    {_esc(tooltip_text)}
  </span>
</div>"""


# ── render_heatmap ─────────────────────────────────────────────────────────────

def render_heatmap(opp: dict) -> str:
    """
    Metric × tag-cardinality heatmap for high_cardinality_metric levers.
    Uses evidence items as metrics; fabricates tag dimension from label parsing.
    """
    evidence = opp.get("evidence", [])
    if not evidence:
        return ""

    # Use evidence items as rows (metrics), infer tag columns from label structure
    # Evidence label format expected: "metric_name:tag_key" or just "metric_name"
    metrics = []
    tag_keys = set()
    for e in evidence[:8]:
        label = str(e.get("label", ""))
        cost  = float(e.get("cost_usd", 0))
        vol   = e.get("volume", "")
        if ":" in label:
            m, t = label.split(":", 1)
        else:
            m, t = label, "default"
        metrics.append({"metric": m.strip(), "tag": t.strip(), "cost": cost, "vol": vol})
        tag_keys.add(t.strip())

    # Deduplicate metrics, aggregate by metric × tag
    tag_list   = sorted(tag_keys)
    metric_set = list(dict.fromkeys(m["metric"] for m in metrics))

    # Build cost matrix
    matrix = {}
    for m in metrics:
        key = (m["metric"], m["tag"])
        matrix[key] = matrix.get(key, 0) + m["cost"]

    all_costs = [v for v in matrix.values() if v > 0]
    max_c = max(all_costs) if all_costs else 1.0

    # Column headers
    col_width = max(1, len(tag_list))
    header_cells = '<div style="min-width:80px;"></div>' + "".join(
        f'<div style="text-align:center;font-size:0.65rem;color:#64748b;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60px;">{_esc(t)}</div>'
        for t in tag_list
    )

    row_html = ""
    for metric in metric_set[:8]:
        cells = ""
        for tag in tag_list:
            cost  = matrix.get((metric, tag), 0)
            ratio = cost / max_c if max_c else 0
            color = _heatmap_color(ratio)
            alpha = max(0.15, ratio)
            label = f"${cost:,.0f}" if cost > 0 else ""
            cells += f"""<div class="heatmap-cell"
                style="background:{color};opacity:{alpha:.2f};min-width:60px;"
                title="{_esc(metric)} × {_esc(tag)}: ${cost:,.0f}">{label}</div>"""
        row_html += f"""
<div style="display:flex;align-items:center;gap:3px;margin-bottom:3px;">
  <div class="heatmap-row-label" title="{_esc(metric)}">{_esc(metric[:14])}{'…' if len(metric) > 14 else ''}</div>
  {cells}
</div>"""

    return f"""
<div style="margin-bottom:16px;">
  <div class="dd-section-label">Cardinality heatmap — metric × tag</div>
  <div class="heatmap-wrap">
    <div style="display:flex;align-items:center;gap:3px;margin-bottom:4px;">
      {header_cells}
    </div>
    {row_html}
    <div class="heatmap-legend">
      Low cost
      <div class="heatmap-legend-grad"></div>
      High cost (danger)
    </div>
  </div>
</div>"""


# ── render_scope_checklist ─────────────────────────────────────────────────────

def render_scope_checklist(scope_check: dict) -> str:
    """
    Compact card with ✓/✗ per scope. Shows unlock notes for missing scopes
    and a collapsible key-creation guide. Returns empty string for empty input.
    """
    if not scope_check:
        return ""

    _SCOPE_LABELS = {
        "logs_read":    "logs_read",
        "metrics_read": "metrics_read",
        "billing_read": "billing_read",
        "usage_read":   "usage_read",
    }
    _SCOPE_DEFAULT_DESC = {
        "logs_read":    "Scan log indexes, pipelines, and archive costs",
        "metrics_read": "Analyse custom metric cardinality and retention",
        "billing_read": "Read committed spend and on-demand overages",
        "usage_read":   "Access usage data for per-product breakdowns",
    }

    missing  = scope_check.get("missing", [])
    unlocks  = scope_check.get("unlocks", {})

    # Determine all scopes to display (present = True/False flags + missing list)
    scope_flags = {}
    for key in _SCOPE_LABELS:
        flag = scope_check.get(key)
        if flag is not None:
            scope_flags[key] = bool(flag)
        else:
            scope_flags[key] = (key not in missing)

    rows_html = ""
    for key, present in scope_flags.items():
        desc = unlocks.get(key) or _SCOPE_DEFAULT_DESC.get(key, "")
        icon = '<span class="scope-icon-ok">✓</span>' if present else '<span class="scope-icon-err">✗</span>'
        rows_html += f"""
<div class="scope-row">
  {icon}
  <span class="scope-name">{_esc(key)}</span>
  <span class="scope-desc">{_esc(desc)}</span>
</div>"""

    # Unlock note for missing scopes
    unlock_html = ""
    if missing:
        items_html = "".join(
            f"<li><code>{_esc(s)}</code> — {_esc(unlocks.get(s, 'unlocks additional savings analysis'))}</li>"
            for s in missing
        )
        guide_html = f"""
<details class="scope-key-guide">
  <summary>How to create a read-only key with all scopes</summary>
  <div class="scope-key-guide-body">
    <strong>Application Key</strong> (grants scope access):
    <ol>
      <li>Go to <a href="https://app.datadoghq.com/organization-settings/application-keys" target="_blank" rel="noopener">Organization Settings &rarr; Application Keys</a></li>
      <li>Click <strong>New Key</strong> and give it a name (e.g. <em>observabill-readonly</em>).</li>
      <li>Under <strong>Scopes</strong>, tick: <code>usage_read</code>, <code>billing_read</code>, <code>logs_read</code>, <code>metrics_read</code>.</li>
      <li>Click <strong>Create Key</strong> and copy the value — it is shown only once.</li>
    </ol>
    <strong>API Key</strong> (required alongside the Application Key):
    <ol>
      <li>Go to <a href="https://app.datadoghq.com/organization-settings/api-keys" target="_blank" rel="noopener">Organization Settings &rarr; API Keys</a></li>
      <li>Click <strong>New Key</strong>, name it, then copy the value.</li>
    </ol>
    No admin access is needed — any user can create a scoped Application Key for their own org.
  </div>
</details>"""

        unlock_html = f"""
<div class="scope-unlock-note">
  <strong>Unlock more savings</strong> — adding the missing scope{'s' if len(missing) != 1 else ''} reveals:
  <ul>{items_html}</ul>
  {guide_html}
</div>"""

    # Full-access banner
    full_ok_html = ""
    if not missing:
        full_ok_html = '<div class="scope-all-ok">&#10003; Full access &mdash; all savings levers active.</div>'

    return f"""
<div class="scope-card">
  <div class="scope-card-header">
    <h3 style="margin:0;font-size:0.95rem;">API Scope Coverage</h3>
  </div>
  {rows_html}
  {full_ok_html}
  {unlock_html}
</div>"""


# ── render_settings_panel ──────────────────────────────────────────────────────

def render_settings_panel(price_source: str = "list") -> str:
    """
    Slide-in gear panel: editable price assumptions, source badge, Save+Rescan button.
    Defaults are stored in data-attributes and read/written via localStorage in JS.
    Returns overlay + panel HTML (hidden by default, toggled by openSettings()).
    """
    _DEFAULTS = {
        "indexed_log_per_million": "0.0125",
        "ingested_log_per_gb":     "0.10",
        "custom_metric_per_month": "0.05",
    }

    badge_cls  = {
        "derived": "settings-price-badge-derived",
        "list":    "settings-price-badge-list",
        "custom":  "settings-price-badge-custom",
    }.get(price_source, "settings-price-badge-list")

    badge_text = {
        "derived": "&#10003; Rates derived from your real bill",
        "list":    "&#9651; List-price estimate &mdash; enter your real rate for exact $",
        "custom":  "&#9654; Using your custom rates",
    }.get(price_source, "&#9651; List-price estimate &mdash; enter your real rate for exact $")

    return f"""
<div class="settings-overlay" id="settings-overlay"></div>
<div class="settings-panel" id="settings-panel">
  <button class="settings-close" onclick="closeSettings()">&#10005; Close</button>
  <h3 style="margin-bottom:6px;">Price Settings</h3>
  <p style="font-size:0.82rem;color:#475569;margin-bottom:14px;">
    Adjust unit prices used to compute savings estimates. Dollar figures on this page
    update <strong>instantly in your browser</strong> — no re-scan, no API keys involved.
    Rates are saved in localStorage for future visits.
  </p>
  <span class="settings-price-badge {_esc(badge_cls)}" id="price-source-badge">{badge_text}</span>

  <div class="form-group">
    <label class="form-label" for="sp-price_indexed_log_per_million">Indexed logs ($ per million events)</label>
    <input class="settings-price-input" type="number" step="0.0001" min="0"
           id="sp-price_indexed_log_per_million"
           data-default="{_esc(_DEFAULTS['indexed_log_per_million'])}"
           value="{_esc(_DEFAULTS['indexed_log_per_million'])}"
           placeholder="{_esc(_DEFAULTS['indexed_log_per_million'])}">
  </div>
  <div class="form-group">
    <label class="form-label" for="sp-price_ingested_log_per_gb">Ingested logs ($ per GB)</label>
    <input class="settings-price-input" type="number" step="0.001" min="0"
           id="sp-price_ingested_log_per_gb"
           data-default="{_esc(_DEFAULTS['ingested_log_per_gb'])}"
           value="{_esc(_DEFAULTS['ingested_log_per_gb'])}"
           placeholder="{_esc(_DEFAULTS['ingested_log_per_gb'])}">
  </div>
  <div class="form-group">
    <label class="form-label" for="sp-price_custom_metric_per_month">Custom metrics ($ per metric per month)</label>
    <input class="settings-price-input" type="number" step="0.001" min="0"
           id="sp-price_custom_metric_per_month"
           data-default="{_esc(_DEFAULTS['custom_metric_per_month'])}"
           value="{_esc(_DEFAULTS['custom_metric_per_month'])}"
           placeholder="{_esc(_DEFAULTS['custom_metric_per_month'])}">
  </div>

  <button class="btn btn-primary" style="width:100%;margin-top:4px;" onclick="saveSettings()">
    Apply rates locally &rarr;
  </button>
  <div id="settings-applied-msg" style="display:none;margin-top:10px;padding:8px 12px;
       background:#d1fae5;border:1px solid #6ee7b7;border-radius:7px;
       font-size:0.82rem;color:#065f46;font-weight:600;">
    &#10003; Rates applied locally — all dollar figures updated.
  </div>
  <div class="settings-note">
    &#9432; Prices are for estimation only. Actual savings vary by contract tier.
    Verify against your Datadog invoice before committing to a change.
  </div>
</div>"""


# ── render_log_cost_map ────────────────────────────────────────────────────────

def render_log_cost_map(scan: dict) -> str:
    """
    Log Cost Map section: header + total + horizontal bar chart of all buckets.

    Each row: service+status | monthly events | $ | share-of-total bar (green).
    Mobile-ok, clean, escaped HTML.
    """
    cost_map = scan.get("log_cost_map", [])
    total_usd = float(scan.get("log_total_monthly_cost_usd", 0.0))

    if not cost_map:
        return """
<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;
            padding:24px 26px;margin-bottom:28px;">
  <h2 style="margin:0 0 6px 0;font-size:1rem;">Log Cost Map &mdash; Where Does Your Log Spend Go?</h2>
  <p style="color:#64748b;font-size:0.85rem;margin:0;">No log aggregate data available.</p>
</div>"""

    max_cost = max((r["monthly_cost_usd"] for r in cost_map), default=1.0)
    if max_cost <= 0:
        max_cost = 1.0

    rows_html = ""
    for row in cost_map:
        service   = _esc(str(row.get("service", "")))
        status    = _esc(str(row.get("status", "")))
        monthly_e = int(row.get("monthly_events", 0))
        cost      = float(row.get("monthly_cost_usd", 0.0))
        share     = float(row.get("share_pct", 0.0))
        bar_pct   = max(2, int(cost / max_cost * 100))

        # Format events compactly
        if monthly_e >= 1_000_000_000:
            events_str = f"{monthly_e / 1_000_000_000:.1f}B"
        elif monthly_e >= 1_000_000:
            events_str = f"{monthly_e / 1_000_000:.1f}M"
        else:
            events_str = f"{monthly_e:,}"

        rows_html += f"""
<div style="display:flex;align-items:center;gap:10px;padding:7px 0;
            border-bottom:1px solid #f1f5f9;flex-wrap:wrap;">
  <div style="min-width:160px;max-width:200px;font-size:0.82rem;
              font-weight:600;color:#0f172a;overflow:hidden;
              text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;"
       title="{service} [{status}]">{service} <span style="color:#64748b;font-weight:400;">[{status}]</span></div>
  <div style="font-size:0.78rem;color:#64748b;min-width:60px;text-align:right;
              font-variant-numeric:tabular-nums;flex-shrink:0;">{_esc(events_str)}/mo</div>
  <div style="flex:1;min-width:80px;background:#f1f5f9;border-radius:4px;height:10px;overflow:hidden;">
    <div style="width:{bar_pct}%;height:100%;
                background:linear-gradient(90deg,#059669,#10b981);border-radius:4px;"></div>
  </div>
  <div style="font-size:0.82rem;font-weight:700;color:#059669;
              min-width:60px;text-align:right;font-variant-numeric:tabular-nums;
              flex-shrink:0;">{_fmt_usd(cost)}</div>
  <div style="font-size:0.72rem;color:#94a3b8;min-width:36px;text-align:right;
              flex-shrink:0;">{share:.1f}%</div>
</div>"""

    return f"""
<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;
            padding:24px 26px;margin-bottom:28px;
            box-shadow:0 1px 4px rgba(0,0,0,0.06);">
  <div style="display:flex;align-items:baseline;justify-content:space-between;
              flex-wrap:wrap;gap:8px;margin-bottom:16px;">
    <h2 style="margin:0;font-size:1rem;color:#0f172a;">
      Log Cost Map &mdash; Where Does Your Log Spend Go?
    </h2>
    <span style="font-size:0.82rem;color:#64748b;">
      Total: <strong style="color:#059669;font-variant-numeric:tabular-nums;">{_fmt_usd(total_usd)}/mo</strong>
      across <strong>{len(cost_map)}</strong> bucket{'s' if len(cost_map) != 1 else ''}
    </span>
  </div>
  <div style="display:flex;font-size:0.7rem;font-weight:700;letter-spacing:0.8px;
              text-transform:uppercase;color:#94a3b8;padding-bottom:6px;
              border-bottom:2px solid #f1f5f9;gap:10px;flex-wrap:wrap;">
    <div style="min-width:160px;flex-shrink:0;">Service [status]</div>
    <div style="min-width:60px;text-align:right;flex-shrink:0;">Events</div>
    <div style="flex:1;min-width:80px;">Share</div>
    <div style="min-width:60px;text-align:right;flex-shrink:0;">$/mo</div>
    <div style="min-width:36px;text-align:right;flex-shrink:0;">%</div>
  </div>
  {rows_html}
</div>"""


# ── render_dashboard ───────────────────────────────────────────────────────────

def render_dashboard(scan: dict, write_enabled: bool = False, apply_token: str = "") -> str:
    """
    Full dashboard body string. Compose with html_page() from app.py.

    SECURITY: apply_token is the only write-flow credential that reaches this
    renderer. api_key / app_key / write_key must NEVER be passed here.
    """
    opps = scan.get("opportunities", [])

    # Stats bar
    n_low    = sum(1 for o in opps if o.get("effort") == "low")
    n_high   = sum(1 for o in opps if o.get("confidence", "").startswith("high"))
    total_w  = float(scan.get("total_monthly_waste_usd", 0))
    n_opps   = len(opps)

    stats_html = f"""
<div class="stats-bar">
  <div class="stat-card">
    <div class="stat-label">Total waste</div>
    <div class="stat-value stat-value-green">{_fmt_usd(total_w)}<span style="font-size:1rem;font-weight:500;">/mo</span></div>
    <div class="stat-sub">Estimated recoverable</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Opportunities</div>
    <div class="stat-value stat-value-blue">{n_opps}</div>
    <div class="stat-sub">Ranked by savings</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Quick wins</div>
    <div class="stat-value stat-value-green">{n_low}</div>
    <div class="stat-sub">Low-effort levers</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">High confidence</div>
    <div class="stat-value stat-value-amber">{n_high}</div>
    <div class="stat-sub">Strong signal</div>
  </div>
</div>"""

    # Settings panel — uses new render_settings_panel with price_source awareness
    price_source  = scan.get("price_source", "list")
    settings_html = render_settings_panel(price_source)

    hero_html     = render_hero(scan)
    scope_html    = render_scope_checklist(scan.get("scope_check", {}))
    cost_map_html = render_log_cost_map(scan) if scan.get("log_cost_map") else ""
    table_html    = render_lever_table(scan, write_enabled, apply_token=apply_token)

    return f"""
<style>{DASHBOARD_CSS}</style>
{DASHBOARD_JS}
{settings_html}
<div class="container-wide" style="padding-top:32px;">
  {hero_html}
  {scope_html}
  {stats_html}
  {cost_map_html}
  {table_html}
</div>"""


# ── demo ───────────────────────────────────────────────────────────────────────

_SAMPLE_SCAN = {
    "total_monthly_waste_usd": 4_830.0,
    "currency": "USD",
    "region": "US",
    "price_source": "list",
    "sparkline": [1200, 1350, 1280, 1420, 1390, 1470, 1520, 1600, 1590, 2480, 2310, 2100, 1980, 2050],
    "notes": [
        "Spike on 2026-06-22 (+54% vs baseline)",
        "EU region not yet scanned",
        "3 levers need write scope",
    ],
    "scope_check": {
        "logs_read":    True,
        "metrics_read": True,
        "billing_read": True,
        "usage_read":   False,
        "missing": ["usage_read"],
        "unlocks": {
            "usage_read": "Per-team cost attribution and on-demand overage breakdown by service",
        },
    },
    "opportunities": [
        {
            "id": "opp-1",
            "lever": "noisy_log_index",
            "category": "logs",
            "title": "Prune high-volume debug index",
            "summary": "The 'debug' log index ingests 38 GB/day of DEBUG-level messages never queried in dashboards or alerts. Excluding them saves ~$1,140/mo.",
            "monthly_savings_usd": 1_140.0,
            "savings_pct": "47%",
            "effort": "low",
            "confidence": "high — 30-day query audit",
            "detection_query": "GET /api/v1/logs/config/indexes\n# Then for each index, query usage:\nGET /api/v1/usage/logs?start_hr=2026-06-01T00:00:00Z&end_hr=2026-07-01T00:00:00Z\n# Cross-reference index ingestion volume against dashboard/monitor query audit logs",
            "why": "The 'debug' index accounts for 38 GB/day of ingestion but had zero queries from dashboards, monitors, or notebooks over the last 30 days. DEBUG-level logs are rarely actionable in production — they should be excluded at the pipeline filter layer rather than indexed at full cost.",
            "evidence": [
                {"label": "debug index",    "volume": "38 GB/day",  "cost_usd": 1_140.0},
                {"label": "trace index",    "volume": "12 GB/day",  "cost_usd": 360.0},
                {"label": "app-errors idx", "volume": "4 GB/day",   "cost_usd": 120.0},
            ],
            "generated_config": {
                "endpoint": "https://api.datadoghq.com/api/v1/logs/config/indexes/debug",
                "verb": "PUT",
                "payload": {
                    "filter": {
                        "query": "NOT status:debug"
                    },
                    "daily_limit": 10_000_000,
                    "retention_days": 3,
                },
            },
            "needs_write_scope": True,
        },
        {
            "id": "opp-2",
            "lever": "high_cardinality_metrics",
            "category": "metrics",
            "title": "Drop unbounded pod-label cardinality",
            "summary": "14 custom metrics tagged with pod_id (rotates every deploy) generate 2.1M unique timeseries. Aggregating at the namespace level removes 94% of custom-metric volume.",
            "monthly_savings_usd": 980.0,
            "savings_pct": "38%",
            "effort": "medium",
            "confidence": "high — cardinality API",
            "detection_query": "GET /api/v1/metrics/{metric_name}/tags\n# Returns tag keys and their cardinality estimates.\n# Flag metrics where any single tag key has cardinality > 10,000\nGET /api/v1/metrics/pod_id/tags\n# Compare tag-key cardinality against /api/v1/metrics/summary for volume",
            "why": "The tag key 'pod_id' has ~150,000 unique values and rotates with every Kubernetes deployment. Each unique tag combination creates a separate timeseries, leading to 2.1M active series across 14 metrics. Datadog bills per active custom metric timeseries — removing or aggregating this tag collapses 94% of the volume to namespace-level granularity, which covers all legitimate alerting use cases.",
            "evidence": [
                {"label": "request_duration:pod_id",  "volume": "840K series", "cost_usd": 420.0},
                {"label": "memory_rss:pod_id",         "volume": "620K series", "cost_usd": 310.0},
                {"label": "cpu_throttle:pod_id",       "volume": "480K series", "cost_usd": 240.0},
                {"label": "gc_pause:pod_id",           "volume": "160K series", "cost_usd": 10.0},
            ],
            "generated_config": {
                "endpoint": "https://api.datadoghq.com/api/v1/metrics/pod_id/tags",
                "verb": "DELETE",
                "payload": {},
            },
            "needs_write_scope": True,
        },
        {
            "id": "opp-3",
            "lever": "duplicate_log_pipeline",
            "category": "logs",
            "title": "Merge redundant Kubernetes pipelines",
            "summary": "Two log pipelines both match 'kube.*' and forward to the same archive. Deduplicating reduces pipeline processing cost by ~$760/mo.",
            "monthly_savings_usd": 760.0,
            "savings_pct": "29%",
            "effort": "low",
            "confidence": "medium — pipeline diff",
            "evidence": [
                {"label": "pipeline-kube-a", "volume": "22 GB/day", "cost_usd": 660.0},
                {"label": "pipeline-kube-b", "volume": "3 GB/day",  "cost_usd": 90.0},
                {"label": "archive egress",  "volume": "1.2 TB/mo", "cost_usd": 10.0},
            ],
            "generated_config": {
                "endpoint": "https://api.datadoghq.com/api/v1/logs/config/pipelines",
                "verb": "POST",
                "payload": {
                    "name": "kube-unified",
                    "filter": {"query": "source:kube*"},
                    "processors": [{"type": "grok-parser", "name": "Kube parser"}],
                },
            },
            "needs_write_scope": True,
        },
        {
            "id": "opp-4",
            "lever": "stale_metric_retention",
            "category": "metrics",
            "title": "Cut stale metric retention from 15 to 3 months",
            "summary": "62 metrics have not been queried in > 90 days but retain 15 months of history. Trimming retention frees ~$950/mo in storage.",
            "monthly_savings_usd": 950.0,
            "savings_pct": "41%",
            "effort": "high",
            "confidence": "medium — last-query timestamp",
            "evidence": [
                {"label": "legacy.payment.*", "volume": "18 metrics", "cost_usd": 380.0},
                {"label": "old.batch.*",      "volume": "22 metrics", "cost_usd": 300.0},
                {"label": "infra.deprecated", "volume": "22 metrics", "cost_usd": 270.0},
            ],
            "generated_config": {
                "endpoint": "https://api.datadoghq.com/api/v1/metrics/retention",
                "verb": "PATCH",
                "payload": {
                    "metrics": ["legacy.payment.*", "old.batch.*", "infra.deprecated"],
                    "retention_months": 3,
                },
            },
            "needs_write_scope": False,
        },
        {
            "id": "opp-5",
            "lever": "archive_tiering",
            "category": "logs",
            "title": "Enable S3 tiering for cold log archive",
            "summary": "The production-logs archive stores 11 TB of data older than 30 days in Datadog-managed storage. Routing to S3 Glacier saves ~$1,000/mo.",
            "monthly_savings_usd": 1_000.0,
            "savings_pct": "52%",
            "effort": "medium",
            "confidence": "high — storage API",
            "evidence": [
                {"label": "prod-logs (>30d)",   "volume": "8.2 TB",  "cost_usd": 820.0},
                {"label": "audit-logs (>30d)",  "volume": "2.1 TB",  "cost_usd": 140.0},
                {"label": "access-logs (>30d)", "volume": "0.7 TB",  "cost_usd": 40.0},
            ],
            "generated_config": {
                "endpoint": "https://api.datadoghq.com/api/v1/logs/config/archives/prod-logs",
                "verb": "PUT",
                "payload": {
                    "destination": {
                        "type": "s3",
                        "bucket": "my-org-dd-archive",
                        "path": "/cold/",
                        "region": "us-east-1",
                    },
                    "rehydration_tags": ["env:prod"],
                },
            },
            "needs_write_scope": True,
        },
    ],
    "log_cost_map": [
        {"service": "aws",         "status": "debug", "monthly_events": 1_100_000_000, "monthly_cost_usd": 1870.0,  "share_pct": 38.7},
        {"service": "nginx",       "status": "200",   "monthly_events": 343_000_000,   "monthly_cost_usd": 583.1,   "share_pct": 12.1},
        {"service": "payment",     "status": "debug", "monthly_events": 150_000_000,   "monthly_cost_usd": 255.0,   "share_pct": 5.3},
        {"service": "health",      "status": "200",   "monthly_events": 128_000_000,   "monthly_cost_usd": 217.6,   "share_pct": 4.5},
        {"service": "auth",        "status": "201",   "monthly_events": 85_000_000,    "monthly_cost_usd": 144.5,   "share_pct": 3.0},
        {"service": "api",         "status": "error", "monthly_events": 42_000_000,    "monthly_cost_usd": 71.4,    "share_pct": 1.5},
        {"service": "api",         "status": "info",  "monthly_events": 38_000_000,    "monthly_cost_usd": 64.6,    "share_pct": 1.3},
        {"service": "api",         "status": "warn",  "monthly_events": 12_000_000,    "monthly_cost_usd": 20.4,    "share_pct": 0.4},
        {"service": "scheduler",   "status": "debug", "monthly_events": 9_000_000,     "monthly_cost_usd": 15.3,    "share_pct": 0.3},
        {"service": "worker",      "status": "200",   "monthly_events": 6_000_000,     "monthly_cost_usd": 10.2,    "share_pct": 0.2},
        {"service": "cdn",         "status": "200",   "monthly_events": 4_500_000,     "monthly_cost_usd": 7.65,    "share_pct": 0.2},
        {"service": "metrics-svc", "status": "info",  "monthly_events": 2_000_000,     "monthly_cost_usd": 3.4,     "share_pct": 0.1},
    ],
    "log_total_monthly_cost_usd": 4830.0,
}


if __name__ == "__main__":
    import sys

    # Try to reuse html_page from app.py if importable
    try:
        sys.path.insert(0, "/Users/jleizerovich/workspace/ai/revenue/prototypes/observabill")
        from app import html_page, CSS  # noqa: F401
        _have_app = True
    except ImportError:
        _have_app = False

    if not _have_app:
        def html_page(title, body, extra_head=""):  # type: ignore[misc]
            return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)} — ObservaBill</title>
{extra_head}
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             background:#f8fafc;color:#0f172a;margin:0;">
{body}
</body></html>"""

    body = render_dashboard(_SAMPLE_SCAN, write_enabled=False)
    page = html_page("Savings Dashboard — ObservaBill", body)

    out_path = "/tmp/ui_preview.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)

    print(f"[ObservaBill ui.py] Preview written to {out_path}")
    print(f"  Total waste displayed : {_fmt_usd(_SAMPLE_SCAN['total_monthly_waste_usd'])}/mo")
    print(f"  Opportunities         : {len(_SAMPLE_SCAN['opportunities'])}")
    print(f"  File size             : {len(page):,} bytes")
    print()
    print("Functions exported:")
    print("  render_dashboard(scan, write_enabled, apply_token) -> str")
    print("  render_hero(scan) -> str")
    print("  render_lever_table(scan, write_enabled, apply_token) -> str")
    print("  render_drilldown(opp, write_enabled, apply_token) -> str  [+detection block]")
    print("  render_heatmap(opp) -> str")
    print("  render_scope_checklist(scope_check) -> str  [NEW]")
    print("  render_settings_panel(price_source) -> str  [NEW]")
