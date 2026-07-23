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

/* ── Findings cards (new content-intelligence UI) ──────────────────────── */

/* Action badge for recommended_action */
.action-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 0.76rem;
    font-weight: 800;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    white-space: nowrap;
}
.action-exclude       { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
.action-sample        { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
.action-to_metric     { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
.action-trim_fields   { background: #ede9fe; color: #5b21b6; border: 1px solid #c4b5fd; }
.action-reduce_cardinality { background: #ede9fe; color: #5b21b6; border: 1px solid #c4b5fd; }
.action-review        { background: #f3e8ff; color: #6b21a8; border: 1px solid #e9d5ff; }
.action-keep          { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }

/* Metered pill */
.metered-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: #fef3c7;
    border: 1px solid #fcd34d;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 0.72rem;
    font-weight: 700;
    color: #78350f;
    white-space: nowrap;
    cursor: default;
}

/* Finding card */
.finding-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    overflow: hidden;
    margin-bottom: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    transition: box-shadow 0.15s;
}
.finding-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.10); }

.finding-card-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    padding: 20px 22px 14px 22px;
    gap: 12px;
    flex-wrap: wrap;
}
.finding-card-title {
    font-size: 0.97rem;
    font-weight: 700;
    color: var(--text);
    margin: 0 0 6px 0;
    line-height: 1.35;
}
.finding-card-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 8px;
}
.finding-card-savings {
    text-align: right;
    flex-shrink: 0;
}

/* Template code block */
.template-block {
    margin: 0 22px 14px 22px;
    border-radius: 8px;
    overflow: hidden;
    border: 2px solid #e0e7ff;
}
.template-block-header {
    background: #1e293b;
    padding: 7px 14px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: #7dd3fc;
    display: flex;
    align-items: center;
    gap: 6px;
}
.template-block-body {
    background: #0f172a;
    padding: 12px 16px;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.88rem;
    line-height: 1.6;
    color: #e2e8f0;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
    margin: 0;
}
/* Highlight placeholders like <NUM> <UUID> <IP> <*> <ID> */
.tpl-placeholder {
    color: #fbbf24;
    font-weight: 700;
    background: rgba(251,191,36,0.12);
    border-radius: 3px;
    padding: 0 2px;
}

/* Redacted sample line */
.sample-line {
    margin: 0 22px 14px 22px;
    padding: 9px 14px;
    background: #f8fafc;
    border: 1px solid var(--border);
    border-left: 3px solid #94a3b8;
    border-radius: 0 7px 7px 0;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.78rem;
    color: #64748b;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
    line-height: 1.5;
}
.sample-label {
    font-size: 0.65rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #94a3b8;
    margin-right: 8px;
}

/* Query excerpt block */
.query-block {
    margin: 0 22px 14px 22px;
}
.query-block-header {
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--slate);
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 7px;
}
.query-code {
    background: #0f172a;
    border-radius: 7px;
    padding: 10px 14px;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.80rem;
    color: #86efac;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
    margin: 0;
    display: flex;
    align-items: flex-start;
    gap: 10px;
}
.btn-copy-query {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 0.68rem;
    font-weight: 600;
    color: #94a3b8;
    cursor: pointer;
    flex-shrink: 0;
    margin-top: 1px;
    transition: all 0.15s;
    white-space: nowrap;
}
.btn-copy-query:hover { background: rgba(255,255,255,0.15); color: #e2e8f0; }
.btn-copy-query.copied { color: #34d399; border-color: #34d399; }

/* Why-safe note */
.why-safe-note {
    margin: 0 22px 14px 22px;
    padding: 9px 14px;
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    border-left: 3px solid var(--green);
    border-radius: 0 7px 7px 0;
    font-size: 0.82rem;
    color: #166534;
    line-height: 1.55;
}

/* Finding card footer */
.finding-card-footer {
    padding: 14px 22px;
    border-top: 1px solid var(--border);
    background: #f8fafc;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
}

/* Section heading */
.section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
    flex-wrap: wrap;
    gap: 8px;
}
.section-heading h2 { margin: 0; }
.section-sub {
    font-size: 0.82rem;
    color: var(--slate);
}

/* Anomaly watchdog section */
.anomaly-section {
    background: var(--surface);
    border: 1px solid #fde68a;
    border-radius: 14px;
    overflow: hidden;
    margin-bottom: 28px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}
.anomaly-section-header {
    background: #fffbeb;
    border-bottom: 1px solid #fde68a;
    padding: 14px 22px;
    display: flex;
    align-items: center;
    gap: 10px;
}
.anomaly-section-header h3 {
    margin: 0;
    font-size: 0.95rem;
    color: #78350f;
}
.anomaly-section-body {
    padding: 14px 22px;
}
.anomaly-row {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 12px 0;
    border-bottom: 1px solid #fef3c7;
    line-height: 1.55;
    font-size: 0.88rem;
    color: var(--text);
}
.anomaly-row:last-child { border-bottom: none; }
.anomaly-icon {
    font-size: 1.2rem;
    flex-shrink: 0;
    margin-top: 1px;
}
.anomaly-text { flex: 1; }
.anomaly-text strong { color: var(--text); }
.anomaly-cost {
    font-size: 0.82rem;
    font-weight: 700;
    color: var(--amber);
    white-space: nowrap;
    flex-shrink: 0;
    margin-top: 2px;
}
.anomaly-empty {
    padding: 18px 0;
    font-size: 0.85rem;
    color: var(--slate);
    text-align: center;
}

/* Collapsed cost map */
.cost-map-details {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 28px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.cost-map-details summary {
    cursor: pointer;
    padding: 14px 22px;
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--slate);
    background: #f8fafc;
    user-select: none;
    display: flex;
    align-items: center;
    gap: 8px;
    list-style: none;
}
.cost-map-details summary::-webkit-details-marker { display: none; }
.cost-map-details summary::before {
    content: '▶';
    font-size: 0.68rem;
    color: #94a3b8;
    transition: transform 0.18s;
}
.cost-map-details[open] summary::before { transform: rotate(90deg); }
.cost-map-details summary:hover { background: #f1f5f9; }
.cost-map-details-body { padding: 0 0 4px 0; }

/* Sampled-false notice */
.no-content-notice {
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 20px;
    font-size: 0.85rem;
    color: #78350f;
    display: flex;
    align-items: flex-start;
    gap: 10px;
}
.no-content-notice .notice-icon { flex-shrink: 0; font-size: 1.1rem; }

@media print {
    .nav { display: none; }
    footer { display: none; }
    button { display: none; }
    .settings-link { display: none; }
    body { background: white; }
    .hero-card { break-inside: avoid; }
    .lever-table-wrap { break-inside: avoid; page-break-after: auto; }
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

// ── Expand / collapse a leaderboard row's detail row ─────────────────────────
function toggleLbRow(id) {
    var row = document.getElementById('lbdd-' + id);
    var caret = document.getElementById('lbcaret-' + id);
    if (!row) return;
    var open = row.style.display !== 'none';
    if (open) {
        row.style.display = 'none';
        if (caret) caret.textContent = '▸';
    } else {
        row.style.display = 'table-row';
        if (caret) caret.textContent = '▾';
    }
}

// ── Copy query block ──────────────────────────────────────────────────────────
function copyQueryBlock(id, btn) {
    var el = document.getElementById(id);
    if (!el || !btn) return;
    // Extract text from the span (first child), not the button
    var span = el.querySelector('span');
    var text = span ? (span.textContent || span.innerText) : (el.textContent || el.innerText);
    // Strip trailing button text
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text.trim()).then(function() {
            btn.textContent = '✓ Copied';
            btn.classList.add('copied');
            setTimeout(function() {
                btn.textContent = 'Copy';
                btn.classList.remove('copied');
            }, 2000);
        });
    } else {
        var ta = document.createElement('textarea');
        ta.value = text.trim();
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

// ── Export leaderboard to CSV ─────────────────────────────────────────────────
function exportLeaderboardCSV() {
    var table = document.getElementById('lb-table');
    if (!table) return;

    var rows = [];
    var headerRow = ['Rank', 'Template', 'Services', '$/mo', '$/yr', '% Bill', 'Action'];
    rows.push(headerRow);

    // Extract visible rows from the table body
    var tbody = table.querySelector('tbody');
    if (!tbody) return;

    var trs = tbody.querySelectorAll('tr:not([id^="lbdd-"])');
    trs.forEach(function(tr, idx) {
        var cells = tr.querySelectorAll('td');
        if (cells.length >= 7) {
            var row = [
                (idx + 1).toString(),  // Rank
                (cells[1].textContent || '').trim(),  // Template
                (cells[2].textContent || '').trim(),  // Services
                (cells[3].textContent || '').trim(),  // $/mo
                (cells[4].textContent || '').trim(),  // $/yr
                (cells[5].textContent || '').trim(),  // % Bill
                (cells[6].textContent || '').trim()   // Action
            ];
            rows.push(row);
        }
    });

    // Convert to CSV
    var csv = rows.map(function(r) {
        return r.map(function(cell) {
            // Escape quotes and wrap in quotes if contains comma
            var escaped = cell.replace(/"/g, '""');
            if (escaped.indexOf(',') >= 0 || escaped.indexOf('"') >= 0 || escaped.indexOf('\\n') >= 0) {
                return '"' + escaped + '"';
            }
            return escaped;
        }).join(',');
    }).join('\\n');

    // Download
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    var link = document.createElement('a');
    link.setAttribute('href', URL.createObjectURL(blob));
    link.setAttribute('download', 'observabill-leaderboard.csv');
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
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
    Big "Monthly Estimated Waste" card: huge green $ figure, subtitle with
    lines_examined + noisy pattern count, inline-SVG sparkline with spike highlight.
    When sampled=False, shows a note that content sampling didn't run.
    """
    total         = float(scan.get("total_monthly_waste_usd", 0))
    region        = _esc(scan.get("region", "US"))
    opps          = scan.get("opportunities", [])
    notes         = scan.get("notes", [])
    sparkline     = scan.get("sparkline", [])
    lines_examined = int(scan.get("lines_examined", 0))
    sampled       = scan.get("sampled", True)

    # Count noisy template + field_bloat opportunities (the "content intelligence" finds)
    noisy_opps = [o for o in opps if o.get("lever") in ("pattern_exclusion", "field_bloat")]
    n_noisy    = len(noisy_opps)

    # Build hero sub-line
    if lines_examined and n_noisy:
        hero_sub = (
            f"We examined <strong>{lines_examined:,}</strong> of your log lines and found "
            f"<strong>{n_noisy}</strong> noisy pattern{'s' if n_noisy != 1 else ''} "
            f"costing <strong style='color:#ecfdf5;'>"
            + _fmt_usd(total)
            + "</strong>/mo."
        )
    else:
        n_logs    = sum(1 for o in opps if o.get("category") == "logs")
        n_metrics = sum(1 for o in opps if o.get("category") == "metrics")
        opp_desc  = []
        if n_logs:
            opp_desc.append(f"<strong>{n_logs}</strong> log lever{'s' if n_logs != 1 else ''}")
        if n_metrics:
            opp_desc.append(f"<strong>{n_metrics}</strong> metric lever{'s' if n_metrics != 1 else ''}")
        opp_str  = " &amp; ".join(opp_desc) if opp_desc else f"<strong>{len(opps)}</strong> levers"
        hero_sub = opp_str + " identified" if opps else "No opportunities found"

    # Sampled-false notice (missing logs_read_data scope)
    no_sample_note = ""
    if not sampled:
        no_sample_note = (
            '<div style="margin-top:14px;padding:10px 14px;background:rgba(251,191,36,0.18);'
            'border:1px solid rgba(251,191,36,0.4);border-radius:8px;'
            'font-size:0.82rem;color:#fef3c7;">'
            '<strong>Note:</strong> Content sampling did not run — the <code style="background:rgba(255,255,255,0.12);'
            'padding:1px 5px;border-radius:3px;">logs_read_data</code> scope was not granted. '
            'Showing anomaly detection and cost-map analysis only.'
            '</div>'
        )

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

        pts      = " ".join(f"{px(i, v)[0]:.1f},{px(i, v)[1]:.1f}" for i, v in enumerate(vals))
        area_pts = f"{pad},{h} " + pts + f" {pad + (len(vals)-1)*step:.1f},{h}"
        max_i    = vals.index(hi)
        spike_x, spike_y = px(max_i, hi)

        svg_html = (
            f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="display:block;">'
            f'<defs><linearGradient id="spk-fill" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%"   stop-color="#34d399" stop-opacity="0.35"/>'
            f'<stop offset="100%" stop-color="#34d399" stop-opacity="0"/>'
            f'</linearGradient></defs>'
            f'<polygon points="{_esc(area_pts)}" fill="url(#spk-fill)"/>'
            f'<polyline points="{_esc(pts)}" fill="none" stroke="#34d399" stroke-width="2"'
            f' stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{spike_x:.1f}" cy="{spike_y:.1f}" r="5"'
            f' fill="#fbbf24" stroke="#fff" stroke-width="2"/>'
            f'</svg>'
        )

    # Notes chips
    notes_html = ""
    if notes:
        chips = "".join(f'<span class="hero-note-chip">{_esc(n)}</span>' for n in notes)
        notes_html = f'<div class="hero-notes">{chips}</div>'

    sparkline_block = (
        f'<div class="hero-sparkline" title="30-day cost trend · spike = highest point">{svg_html}</div>'
        if svg_html else ""
    )

    return (
        f'<div class="hero-card">'
        f'<a class="settings-link" onclick="openSettings(); return false;" href="#" title="Price settings">'
        f'<svg width="14" height="14" viewBox="0 0 24 24" fill="none"'
        f' stroke="currentColor" stroke-width="2" stroke-linecap="round">'
        f'<circle cx="12" cy="12" r="3"/>'
        f'<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06'
        f'a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09'
        f'A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83'
        f'l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09'
        f'A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83'
        f'l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09'
        f'a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83'
        f'l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09'
        f'a1.65 1.65 0 0 0-1.51 1z"/>'
        f'</svg>Price settings</a>'
        f'<div class="hero-eyebrow">Monthly Estimated Waste &middot; {region}</div>'
        f'<div class="hero-amount" data-usd="{total}" data-price-key="mixed">'
        f'<span class="currency">$</span>{total:,.0f}</div>'
        f'<div class="hero-sub">{hero_sub}</div>'
        f'{sparkline_block}'
        f'{no_sample_note}'
        f'{notes_html}'
        f'</div>'
    )


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


# ── render_findings_cards ─────────────────────────────────────────────────────

def _highlight_template(tpl: str) -> str:
    """Highlight placeholders like <NUM>, <UUID>, <IP>, <*>, <ID> in amber."""
    import re
    escaped = html.escape(tpl)
    # Match <WORD> or <*> — already html-escaped so < = &lt; > = &gt;
    escaped = re.sub(
        r'&lt;([A-Z0-9*_]+)&gt;',
        r'<span class="tpl-placeholder">&lt;\1&gt;</span>',
        escaped
    )
    return escaped


def _action_badge(action: str) -> str:
    labels = {
        "exclude":           ("EXCLUDE",           "action-exclude"),
        "sample":            ("SAMPLE 10%",        "action-sample"),
        "to_metric":         ("CONVERT TO METRIC", "action-to_metric"),
        "trim_fields":       ("TRIM FIELD",        "action-trim_fields"),
        "reduce_cardinality":("REDUCE CARDINALITY","action-reduce_cardinality"),
        "review":            ("REVIEW",            "action-review"),
        "keep":              ("KEEP",               "action-keep"),
    }
    label, cls = labels.get(action, (_esc(action).upper(), "action-keep"))
    return f'<span class="action-badge {cls}">{label}</span>'


def _extract_query(opp: dict) -> str:
    """Extract the exact DD exclusion query from generated_config, or '' if absent."""
    cfg = opp.get("generated_config", {})
    payload = cfg.get("payload", {})
    filters = payload.get("exclusion_filters", [])
    if filters and isinstance(filters, list):
        filt = filters[0].get("filter", {})
        return str(filt.get("query", ""))
    # field_bloat: render field name + action
    if opp.get("lever") == "field_bloat":
        field = opp.get("field_name", "")
        action = opp.get("recommended_action", "trim_fields")
        endpoint = cfg.get("endpoint", "")
        if field:
            return f"# Pipeline action: {action}\n# Field: {field}\n# Endpoint: {endpoint}"
    return ""


def render_finding_card(opp: dict, idx: int, write_enabled: bool, apply_token: str = "") -> str:
    """Render a single rich finding card for pattern_exclusion or field_bloat."""
    oid       = _esc(opp.get("id", f"opp-{idx}"))
    lever     = opp.get("lever", "")
    title     = _esc(opp.get("title", "Untitled"))
    summary   = _esc(opp.get("summary", ""))
    action    = opp.get("recommended_action", "keep")
    savings   = float(opp.get("monthly_savings_usd", 0))
    cost      = float(opp.get("monthly_cost_usd", savings))
    events    = int(opp.get("monthly_events", 0))
    conf      = opp.get("confidence", "")
    effort    = opp.get("effort", "medium")
    metered   = bool(opp.get("metered", False))
    why_safe  = _esc(opp.get("why_safe", opp.get("why", "")))
    template  = opp.get("template", "")
    sample    = opp.get("sample_redacted", "")
    needs_write = opp.get("needs_write_scope", False)
    cat       = opp.get("category", "logs")
    price_key = "custom_metric_per_month" if cat == "metrics" else "indexed_log_per_million"

    # Template block (pattern_exclusion) or field summary (field_bloat)
    if lever == "pattern_exclusion" and template:
        tpl_highlighted = _highlight_template(template)
        template_html = (
            f'<div class="template-block">'
            f'<div class="template-block-header">'
            f'<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
            f' stroke-width="2.5" stroke-linecap="round"><polyline points="16 18 22 12 16 6"/>'
            f'<polyline points="8 6 2 12 8 18"/></svg>'
            f'LOG TEMPLATE (mined from {_esc(str(opp.get("service","")))} logs)'
            f'</div>'
            f'<pre class="template-block-body">{tpl_highlighted}</pre>'
            f'</div>'
        )
    elif lever == "field_bloat":
        field_name  = _esc(opp.get("field_name", ""))
        kind        = _esc(opp.get("recommended_action", "trim_fields"))
        cardinality = opp.get("cardinality", "")
        card_str    = f", {cardinality:,} distinct values" if isinstance(cardinality, int) and cardinality > 1 else ""
        template_html = (
            f'<div class="template-block">'
            f'<div class="template-block-header">'
            f'<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
            f' stroke-width="2.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18"'
            f' rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/>'
            f'</svg>'
            f'FIELD: {field_name}{card_str}'
            f'</div>'
            f'<pre class="template-block-body">'
            f'<span class="tpl-placeholder">{field_name}</span>'
            f'  &rarr;  {kind}'
            f'</pre>'
            f'</div>'
        )
    else:
        template_html = ""

    # Redacted sample line
    sample_html = ""
    if sample and lever == "pattern_exclusion":
        sample_html = (
            f'<div class="sample-line">'
            f'<span class="sample-label">sample (redacted)</span>'
            f'{_esc(sample)}'
            f'</div>'
        )

    # Exact query / config block
    query_str  = _extract_query(opp)
    query_html = ""
    if query_str:
        qid = f"qry-{oid}"
        query_html = (
            f'<div class="query-block">'
            f'<div class="query-block-header">'
            f'<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
            f' stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/>'
            f'<line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
            f'EXACT DATADOG QUERY / CONFIG'
            f'</div>'
            f'<pre class="query-code" id="{qid}">'
            f'<span style="flex:1;white-space:pre-wrap;word-break:break-all;">{_esc(query_str)}</span>'
            f'<button class="btn-copy-query" onclick="copyQueryBlock(\'{qid}\',this)">Copy</button>'
            f'</pre>'
            f'</div>'
        )

    # Why-safe note
    why_html = ""
    if why_safe:
        why_html = f'<div class="why-safe-note">&#10003; <strong>Safe to apply:</strong> {why_safe}</div>'

    # Metered pill
    metered_html = ""
    if metered:
        metered_html = (
            '<span class="metered-pill" title="Datadog already meters this pattern — convert, don\'t drop">'
            '&#9889; metered'
            '</span>'
        )

    # Events/mo compact
    if events >= 1_000_000_000:
        events_str = f"{events / 1_000_000_000:.1f}B events/mo"
    elif events >= 1_000_000:
        events_str = f"{events / 1_000_000:.1f}M events/mo"
    elif events > 0:
        events_str = f"{events:,} events/mo"
    else:
        events_str = ""

    # Apply button (reuse existing helper)
    apply_html = _render_apply_button(oid, write_enabled, needs_write, apply_token=apply_token)

    return (
        f'<div class="finding-card">'
        f'<div class="finding-card-header">'
        f'<div style="flex:1;min-width:0;">'
        f'<div class="finding-card-title">{title}</div>'
        f'<div class="finding-card-meta">'
        f'{_action_badge(action)}'
        f'{metered_html}'
        f'{_effort_pill(effort)}'
        f'{_conf_chip(conf)}'
        f'</div>'
        f'<div style="font-size:0.80rem;color:#64748b;">{summary}</div>'
        f'</div>'
        f'<div class="finding-card-savings">'
        f'<div class="savings-amt" data-usd="{savings}" data-price-key="{_esc(price_key)}">'
        f'{_fmt_usd(savings)}</div>'
        f'<div style="font-size:0.72rem;color:#64748b;margin-top:2px;text-align:right;">savings/mo</div>'
        f'{f"""<div style="font-size:0.72rem;color:#94a3b8;margin-top:1px;text-align:right;">{_esc(events_str)}</div>""" if events_str else ""}'
        f'</div>'
        f'</div>'
        f'{template_html}'
        f'{sample_html}'
        f'{query_html}'
        f'{why_html}'
        f'<div class="finding-card-footer">'
        f'{apply_html.replace(chr(10), " ")}'
        f'</div>'
        f'</div>'
    )


def render_findings_cards(scan: dict, write_enabled: bool, apply_token: str = "") -> str:
    """
    Primary findings section: rich cards for each pattern_exclusion and field_bloat opportunity.
    Legacy levers (high_cardinality_metric, index_quota) fall through to render_lever_table.
    Includes an honesty note (from scan.notes) near the top.
    """
    opps = scan.get("opportunities", [])
    sampled = scan.get("sampled", True)
    notes   = scan.get("notes", [])

    # Split: rich cards vs legacy table
    card_opps   = [o for o in opps if o.get("lever") in ("pattern_exclusion", "field_bloat")]
    legacy_opps = [o for o in opps if o.get("lever") not in ("pattern_exclusion", "field_bloat")]

    if not card_opps and not legacy_opps:
        if not sampled:
            return (
                '<div class="no-content-notice">'
                '<div class="notice-icon">&#9888;</div>'
                '<div>Content sampling did not run (missing <code>logs_read_data</code> scope). '
                'No pattern findings to show. See anomaly watchdog and cost map below.</div>'
                '</div>'
            )
        return '<div class="card" style="text-align:center;color:#64748b;padding:40px;">No savings opportunities found.</div>'

    total_savings = sum(float(o.get("monthly_savings_usd", 0)) for o in card_opps + legacy_opps)

    # Honesty note from notes list
    notes_html = ""
    if notes:
        notes_items = "".join(f"<li>{_esc(n)}</li>" for n in notes)
        notes_html = (
            f'<div style="margin-bottom:18px;padding:10px 16px;background:#fffbeb;'
            f'border:1px solid #fde68a;border-radius:8px;font-size:0.82rem;color:#78350f;">'
            f'<strong>Methodology:</strong> <ul style="margin:4px 0 0 16px;padding:0;">{notes_items}</ul>'
            f'</div>'
        )

    # Not-sampled notice
    no_sample_html = ""
    if not sampled and not card_opps:
        no_sample_html = (
            '<div class="no-content-notice">'
            '<div class="notice-icon">&#9888;</div>'
            '<div>Content sampling did not run (missing <code>logs_read_data</code> scope). '
            'Showing cost-map and anomaly findings only.</div>'
            '</div>'
        )

    cards_html = "".join(render_finding_card(o, i, write_enabled, apply_token) for i, o in enumerate(card_opps))

    # Legacy table (if any)
    legacy_html = ""
    if legacy_opps:
        legacy_scan = dict(scan)
        legacy_scan["opportunities"] = legacy_opps
        legacy_html = (
            f'<div style="margin-top:28px;">'
            f'<h3 style="margin:0 0 14px 0;font-size:0.95rem;color:#475569;">Additional levers</h3>'
            f'{render_lever_table(legacy_scan, write_enabled, apply_token=apply_token)}'
            f'</div>'
        )

    n_cards = len(card_opps)
    section_sub = (
        f'Total recoverable: <strong style="color:#059669;"'
        f' data-usd="{total_savings}" data-price-key="mixed">{_fmt_usd(total_savings)}</strong>/mo'
        f' across <strong>{n_cards}</strong> pattern finding{"s" if n_cards != 1 else ""}'
    )

    return (
        f'<div class="section-heading">'
        f'<h2 style="margin:0;">Log Intelligence Findings</h2>'
        f'<span class="section-sub">{section_sub}</span>'
        f'</div>'
        f'{notes_html}'
        f'{no_sample_html}'
        f'{cards_html}'
        f'{legacy_html}'
    )


# ── render_anomaly_watchdog ────────────────────────────────────────────────────

def render_anomaly_watchdog(scan: dict) -> str:
    """
    Anomaly Watchdog section: spike + new_pattern anomalies as a clean card list.
    Shows a subtle empty state if no anomalies.
    """
    anomalies = scan.get("anomalies", [])

    rows = []
    for a in anomalies:
        kind   = a.get("kind", "")
        series = _esc(str(a.get("series", "")))
        onset  = _esc(str(a.get("onset_date", "")))
        cost   = a.get("monthly_cost_usd")

        cost_html = ""
        if cost is not None:
            cost_html = (
                f'<div class="anomaly-cost">'
                f'~{_fmt_usd(float(cost))}/mo</div>'
            )

        if kind == "spike":
            latest        = int(a.get("latest", 0))
            baseline_mean = int(a.get("baseline_mean", 0))
            sigma         = a.get("sigma", 0)
            sigma_str     = f"{float(sigma):.1f}"
            icon  = "&#9888;"  # warning triangle
            text  = (
                f'<strong>{series}</strong>: volume spiked to '
                f'<strong>{latest:,}/day</strong> '
                f'({sigma_str}&sigma; above {baseline_mean:,} baseline) '
                f'starting <strong>{onset}</strong>'
            )
        elif kind == "new_pattern":
            monthly_events   = int(a.get("monthly_events", 0))
            recent_daily_avg = int(a.get("recent_daily_avg", 0))
            icon  = "&#127373;"  # NEW box emoji fallback — use text
            icon  = "NEW"
            text  = (
                f'<strong>{series}</strong>: a new log pattern started '
                f'<strong>{onset}</strong>, now '
                f'~<strong>{monthly_events:,}/mo</strong> '
                f'(&asymp;{recent_daily_avg:,}/day) &mdash; '
                f'likely a recent deploy.'
            )
        else:
            icon = "&#9432;"
            text = f'<strong>{series}</strong>: {_esc(str(a))}'

        rows.append(
            f'<div class="anomaly-row">'
            f'<div class="anomaly-icon">{icon}</div>'
            f'<div class="anomaly-text">{text}</div>'
            f'{cost_html}'
            f'</div>'
        )

    if not rows:
        body_html = '<div class="anomaly-empty">&#10003; No anomalies detected &mdash; ingest patterns look stable.</div>'
    else:
        body_html = "".join(rows)

    return (
        f'<div class="anomaly-section">'
        f'<div class="anomaly-section-header">'
        f'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#d97706"'
        f' stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;">'
        f'<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86'
        f'a2 2 0 0 0-3.42 0z"/>'
        f'<line x1="12" y1="9" x2="12" y2="13"/>'
        f'<line x1="12" y1="17" x2="12.01" y2="17"/>'
        f'</svg>'
        f'<h3>Anomaly Watchdog</h3>'
        f'</div>'
        f'<div class="anomaly-section-body">{body_html}</div>'
        f'</div>'
    )


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


def _apply_form(opp: dict, apply_token: str) -> str:
    """
    Build an apply form for a given opportunity.
    Returns HTML form with opp_id and apply_token hidden fields, ready to POST to /apply.
    """
    oid = _esc(opp.get("id", ""))
    safe_token = _esc(apply_token)
    return f"""<form method="POST" action="/apply" style="display:inline;">
  <input type="hidden" name="opp_id" value="{oid}">
  <input type="hidden" name="apply_token" value="{safe_token}">
  <button type="submit" class="btn-apply">Kill it</button>
</form>"""


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
              flex-shrink:0;" data-usd="{cost}" data-price-key="indexed_log_per_million">{_fmt_usd(cost)}</div>
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
      Total: <strong style="color:#059669;font-variant-numeric:tabular-nums;"><span data-usd="{total_usd}" data-price-key="indexed_log_per_million">{_fmt_usd(total_usd)}</span>/mo</strong>
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

def _phrase_from_template(template: str) -> str:
    """Build a quoted phrase query from a template's leading literal words."""
    words = []
    for w in template.split():
        if w.startswith("<") and w.endswith(">"):
            continue
        cw = w.replace('"', "").replace("\\", "")
        if cw:
            words.append(cw)
        if len(words) >= 4:
            break
    return '"' + " ".join(words) + '"' if words else ""


def _query_for_row(row: dict, opp: "dict | None") -> str:
    """The exact Datadog query for a leaderboard pattern (from its opp, else a phrase)."""
    if opp:
        try:
            gc = opp.get("generated_config", {}).get("payload", {})
            q = gc.get("exclusion_filters", [{}])[0].get("filter", {}).get("query")
            if q:
                return q
        except (KeyError, IndexError, TypeError):
            pass
    return _phrase_from_template(row.get("template", ""))


def _leaderboard_detail(rank, row: dict, opp: "dict | None",
                        write_enabled: bool, apply_token: str) -> str:
    """Expanded detail panel for one leaderboard pattern — always actionable."""
    action = row.get("recommended_action", "keep")
    cls = row.get("classification", {}) or {}
    why = cls.get("why_safe") or (opp.get("why_safe") if opp else "") or ""
    monthly_cost = float(row.get("monthly_cost_usd", 0))
    monthly_events = int(row.get("monthly_events", 0))
    services = row.get("services", [])

    # Full services breakdown
    svc_rows = "".join(
        f'<li style="margin:2px 0;">{_esc(str(s.get("service","")))} '
        f'— {float(s.get("share_pct",0)):.0f}% ({int(s.get("count",0)):,} in sample)</li>'
        for s in services
    ) or "<li>—</li>"

    # Exact query + copy button
    query = _query_for_row(row, opp)
    qid = f"lbq-{rank}"
    query_block = ""
    if query:
        query_block = (
            '<div style="margin-top:10px;">'
            '<div style="font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase;'
            'letter-spacing:0.4px;margin-bottom:4px;">Exact Datadog query</div>'
            f'<div id="{qid}" style="display:flex;align-items:center;gap:8px;background:#0f172a;'
            'border-radius:6px;padding:8px 10px;font-family:monospace;font-size:0.8rem;color:#e2e8f0;">'
            f'<span style="flex:1;word-break:break-all;">{_esc(query)}</span>'
            f'<button class="btn-copy-query" onclick="copyQueryBlock(\'{qid}\',this)" '
            'style="background:#334155;color:#fff;border:none;border-radius:4px;padding:3px 10px;'
            'font-size:0.72rem;cursor:pointer;">Copy</button></div></div>'
        )

    # Action control: apply form for applyable opps, advisory for review/keep
    applyable = bool(opp and opp.get("id") and opp.get("needs_write_scope"))
    if applyable and write_enabled and apply_token:
        action_ctl = (
            '<div style="margin-top:12px;">' + _apply_form(opp, apply_token) + '</div>'
        )
    elif action == "review":
        action_ctl = (
            '<div style="margin-top:12px;padding:10px 12px;background:#faf5ff;border:1px solid #e9d5ff;'
            'border-radius:6px;font-size:0.82rem;color:#6b21a8;">'
            '<strong>Advisory — you decide.</strong> This is a high-volume, non-error log. It isn\'t '
            'auto-excluded (it may be intentional), but it\'s a top cost driver. To cut it, add an '
            'exclusion or sampling filter for the query above, or convert it to a metric.'
            '</div>'
        )
    elif applyable:  # applyable but read-only session
        action_ctl = (
            '<div style="margin-top:12px;font-size:0.82rem;color:#64748b;">'
            'Re-scan with a write key to apply this exclusion in one click.</div>'
        )
    else:  # keep
        action_ctl = (
            '<div style="margin-top:12px;padding:10px 12px;background:#f0fdf4;border:1px solid #bbf7d0;'
            'border-radius:6px;font-size:0.82rem;color:#166534;">'
            'Kept — this pattern carries error/signal value, so it\'s left indexed.</div>'
        )

    return (
        '<div style="padding:14px 4px 4px 4px;">'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">'
        '<div>'
        '<div style="font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase;'
        'letter-spacing:0.4px;margin-bottom:4px;">Where it fires</div>'
        f'<ul style="margin:0 0 0 16px;padding:0;font-size:0.82rem;color:#334155;">{svc_rows}</ul>'
        f'<div style="margin-top:8px;font-size:0.8rem;color:#475569;">{monthly_events:,} events/mo '
        f'· {_fmt_usd(monthly_cost)}/mo · {_fmt_usd(monthly_cost*12)}/yr</div>'
        '</div>'
        '<div>'
        '<div style="font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase;'
        'letter-spacing:0.4px;margin-bottom:4px;">Why</div>'
        f'<div style="font-size:0.82rem;color:#334155;">{_esc(why) or "—"}</div>'
        '</div>'
        '</div>'
        f'{query_block}{action_ctl}'
        '</div>'
    )


def render_pattern_leaderboard(scan: dict, write_enabled: bool, apply_token: str) -> str:
    """
    Pattern leaderboard centerpiece:
    Rank | Template | Services | $/mo | $/yr | % bill | Action
    Shows all rows from pattern_leaderboard, sorted by cost.

    When write_enabled and apply_token are True, renders apply forms for applyable opportunities.
    """
    leaderboard = scan.get("pattern_leaderboard", [])
    opps_list = scan.get("opportunities", [])

    # Lookup every opportunity (review + applyable) by template so each row can
    # expand into an actionable drilldown.
    opps_by_template = {}
    for o in opps_list:
        t = o.get("template")
        if t and t not in opps_by_template:
            opps_by_template[t] = o

    if not leaderboard:
        return '<div style="text-align:center;color:#64748b;padding:40px;">No patterns found.</div>'

    rows_html = ""
    for rank, row in enumerate(leaderboard, 1):
        template = row.get("template", "")
        sample = row.get("sample_redacted", "")
        services = row.get("services", [])
        monthly_cost = float(row.get("monthly_cost_usd", 0))
        monthly_events = int(row.get("monthly_events", 0))
        share_pct = float(row.get("share_pct", 0))
        action = row.get("recommended_action", "keep")
        opp = opps_by_template.get(template)

        tpl_html = _highlight_template(template) if template else _esc(sample)

        # Services chips — service NAME + its share of this pattern; "+N more" for extras.
        def _svc_chip(s):
            share = s.get("share_pct")
            label = _esc(str(s.get("service", "")))
            if share is not None:
                label += f' <span style="color:#94a3b8;">{float(share):.0f}%</span>'
            return (
                '<span style="display:inline-block;background:#f1f5f9;border-radius:12px;'
                'padding:2px 8px;font-size:0.7rem;margin:0 4px 4px 0;color:#475569;">' + label + '</span>'
            )
        services_html = ""
        if services:
            shown = services[:3]
            services_html = "".join(_svc_chip(s) for s in shown)
            if len(services) > 3:
                services_html += (
                    '<span style="display:inline-block;background:#f1f5f9;border-radius:12px;'
                    f'padding:2px 8px;font-size:0.7rem;color:#64748b;">+{len(services) - 3} more service'
                    f'{"s" if len(services) - 3 != 1 else ""}</span>'
                )

        action_html = _action_badge(action)
        bar_width = min(share_pct, 100)
        bar_html = (
            f'<div class="bar-track" style="width:100%;height:8px;background:#f1f5f9;border-radius:4px;overflow:hidden;">'
            f'<div class="bar-fill" style="width:{bar_width}%;height:100%;background:linear-gradient(90deg,#059669,#10b981);'
            f'border-radius:4px;"></div></div>'
        )

        detail_html = _leaderboard_detail(rank, row, opp, write_enabled, apply_token)

        rows_html += f"""
<tr style="border-bottom:1px solid #e2e8f0;cursor:pointer;" onclick="toggleLbRow('{rank}')">
  <td style="padding:12px 14px;text-align:center;font-weight:700;color:#475569;width:40px;">
    <span id="lbcaret-{rank}" style="color:#94a3b8;">▸</span> {rank}
  </td>
  <td style="padding:12px 14px;">
    <div style="font-family:'SF Mono','Fira Code','Consolas',monospace;font-size:0.82rem;color:#0f172a;">{tpl_html}</div>
    <div style="font-size:0.72rem;color:#94a3b8;margin-top:3px;">{_esc(sample[:60])}{'…' if len(sample) > 60 else ''}</div>
  </td>
  <td style="padding:12px 14px;font-size:0.80rem;color:#475569;">{services_html}</td>
  <td style="padding:12px 14px;text-align:right;font-weight:600;font-size:0.85rem;color:#0f172a;white-space:nowrap;"
      data-usd="{monthly_cost}" data-price-key="indexed_log_per_million">{_fmt_usd(monthly_cost)}</td>
  <td style="padding:12px 14px;text-align:right;font-size:0.75rem;color:#94a3b8;white-space:nowrap;"
      data-usd="{monthly_cost * 12}" data-price-key="indexed_log_per_million">{_fmt_usd(monthly_cost * 12)}</td>
  <td style="padding:12px 14px;text-align:center;font-weight:600;font-size:0.82rem;color:#0f172a;width:60px;">
    {bar_html}<div style="font-size:0.72rem;color:#64748b;margin-top:2px;">{share_pct:.1f}%</div>
  </td>
  <td style="padding:12px 14px;text-align:right;white-space:nowrap;">{action_html}</td>
</tr>
<tr id="lbdd-{rank}" style="display:none;background:#f8fafc;">
  <td colspan="7" style="padding:0 14px 16px 14px;">{detail_html}</td>
</tr>"""

    return f"""
<div style="margin-bottom:16px; display:flex; gap:8px; flex-wrap:wrap;">
  <button type="button" onclick="exportLeaderboardCSV()" class="btn btn-outline" style="font-size:0.85rem; padding:7px 14px;">⬇ Download CSV</button>
  <button type="button" onclick="window.print()" class="btn btn-outline" style="font-size:0.85rem; padding:7px 14px;">🖨 Print report</button>
</div>
<div class="lever-table-wrap">
<table id="lb-table" style="width:100%;border-collapse:collapse;">
  <thead>
    <tr style="background:#f1f5f9;border-bottom:1px solid #e2e8f0;">
      <th style="padding:11px 14px;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;text-align:center;width:40px;">#</th>
      <th style="padding:11px 14px;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;text-align:left;">Pattern</th>
      <th style="padding:11px 14px;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;text-align:left;">Services</th>
      <th style="padding:11px 14px;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;text-align:right;">$/mo</th>
      <th style="padding:11px 14px;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;text-align:right;">$/yr</th>
      <th style="padding:11px 14px;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;text-align:center;">% Bill</th>
      <th style="padding:11px 14px;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;text-align:right;">Action</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
</div>"""


def render_surge_section(scan: dict) -> str:
    """
    Surge Detector section: patterns growing or newly appeared.
    If empty, show a small green "stable" line.
    """
    surges = scan.get("surges", [])

    if not surges:
        return (
            f'<div style="padding:18px 22px;background:#f0fdf4;border:1px solid #bbf7d0;'
            f'border-radius:10px;font-size:0.85rem;color:#166534;margin-bottom:28px;">'
            f'<strong>✓</strong> No surges — ingest is stable.</div>'
        )

    cards = []
    for surge in surges:
        kind = surge.get("kind", "")
        series = _esc(str(surge.get("series", "")))
        template = surge.get("template", "")
        onset_date = _esc(str(surge.get("onset_date", "")))
        monthly_cost = float(surge.get("monthly_cost_usd", 0))

        # Kind badge + color
        kind_map = {
            "spike": ("VOLUME SPIKE", "red"),
            "level_shift": ("STEP-UP", "orange"),
            "wow_growth": ("GROWING", "orange"),
            "new_pattern": ("NEW PATTERN", "amber"),
        }
        kind_label, color = kind_map.get(kind, ("SURGE", "slate"))

        color_map = {
            "red": "#dc2626",
            "orange": "#ea580c",
            "amber": "#f59e0b",
            "slate": "#64748b",
        }
        border_color = color_map.get(color, "#64748b")

        # Human line per kind
        if kind == "spike":
            latest = int(surge.get("latest", 0))
            baseline_mean = int(surge.get("baseline_mean", 0))
            sigma = float(surge.get("sigma", 0))
            human_line = (
                f'Spiked to <strong>{latest:,}/day</strong>, '
                f'{sigma:.1f}σ above baseline of {baseline_mean:,}/day'
            )
        elif kind == "level_shift":
            ratio = float(surge.get("ratio", 1.0))
            human_line = f'Stepped up <strong>{ratio:.1f}×</strong> vs baseline'
        elif kind == "wow_growth":
            growth_pct = float(surge.get("growth_pct", 0))
            human_line = f'Up <strong>{growth_pct:.1f}%</strong> week-over-week'
        elif kind == "new_pattern":
            monthly_events = int(surge.get("monthly_events", 0))
            human_line = f'Appeared {onset_date}, now ~<strong>{monthly_events:,}/mo</strong>'
        else:
            human_line = _esc(str(surge))

        tpl_html = _highlight_template(template) if template else f'<em>{series}</em>'

        card = f"""
<div style="background:var(--surface);border:1px solid var(--border);border-left:3px solid {border_color};
border-radius:10px;padding:16px 18px;margin-bottom:12px;">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
    <span class="action-badge" style="background:{color_map[color]}22;color:{border_color};border:1px solid {border_color}44;">{kind_label}</span>
    <span style="font-size:0.78rem;color:#64748b;">started <strong>{onset_date}</strong></span>
  </div>
  <div style="font-family:'SF Mono','Fira Code','Consolas',monospace;font-size:0.82rem;
  color:#0f172a;margin-bottom:8px;word-break:break-all;">{tpl_html}</div>
  <div style="font-size:0.85rem;color:#475569;line-height:1.5;margin-bottom:8px;">{human_line}</div>
  <div style="font-size:0.82rem;font-weight:700;color:{border_color};text-align:right;">+{_fmt_usd(monthly_cost)}/mo</div>
</div>"""
        cards.append(card)

    return (
        f'<div style="margin-bottom:28px;">'
        f'<h2 style="margin:0 0 18px 0;font-size:1.1rem;font-weight:700;color:#0f172a;">Surge Detector — patterns growing or newly appeared</h2>'
        f'{"".join(cards)}'
        f'</div>'
    )


def render_roi_banner(scan: dict) -> str:
    """
    ROI banner: placed right below hero. Shows annual savings recovery potential
    and subscription cost comparison. Friendly empty state when total is 0.

    Compute: annual = total_monthly_waste_usd * 12
    Copy: "💰 You could recover **{annual}/yr** — ObservaBill continuous protection is $99/mo.
    This scan alone found {N}× your subscription." (N = round(annual / (99*12)) if ≥1)

    Data-usd on $ for client-side price rescale.
    """
    total = float(scan.get("total_monthly_waste_usd", 0))
    annual = total * 12

    if annual == 0:
        # Friendly "lean" empty state
        return (
            f'<div style="padding:18px 22px;background:#f0fdf4;border:1px solid #bbf7d0;'
            f'border-radius:10px;font-size:0.85rem;color:#166534;margin-bottom:28px;">'
            f'<strong>✓</strong> No recoverable waste found — your logging looks lean.</div>'
        )

    # Compute N (subscription multiples)
    sub_annual_cost = 99 * 12  # $1188/yr
    n_multiples = round(annual / sub_annual_cost) if annual > 0 else 0

    # Build the copy
    if n_multiples >= 1:
        multiple_text = f" This scan alone found <strong>{n_multiples}×</strong> your subscription."
    else:
        multiple_text = ""

    roi_copy = (
        f"💰 You could recover <strong>{_fmt_usd(annual)}/yr</strong> — "
        f"ObservaBill continuous protection is $99/mo.{multiple_text}"
    )

    # Green gradient styling (consistent with hero)
    return (
        f'<div style="padding:16px 20px;background:linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);'
        f'border:1px solid #6ee7b7;border-radius:10px;margin-bottom:28px;">'
        f'<div style="font-size:0.88rem;color:#065f46;line-height:1.5;">'
        f'{roi_copy}'
        f'<span class="data-usd" data-usd="{annual}" data-price-key="indexed_log_per_million" '
        f'style="display:none;"></span>'
        f'</div>'
        f'</div>'
    )


def render_trust_nudge(scan: dict) -> str:
    """
    Trust nudge: muted line near stats/ROI.
    If price_source != 'derived' and no billing → "Estimated at list price ($1.70/M).
    ⚙ Set your real rate for exact dollars." (link to settings)
    If price_source == 'derived' → "Dollars derived from your actual Datadog bill."

    Returns empty string if price_source is 'derived' (or omitted).
    """
    price_source = scan.get("price_source", "list")

    if price_source == "derived":
        return (
            f'<div style="font-size:0.80rem;color:#64748b;margin-bottom:16px;text-align:center;">'
            f'✓ Dollars derived from your actual Datadog bill.'
            f'</div>'
        )

    # List price or custom — show nudge
    return (
        f'<div style="font-size:0.80rem;color:#64748b;margin-bottom:16px;text-align:center;">'
        f'Estimated at list price ($1.70 / million indexed logs). '
        f'<a href="#" onclick="openSettings(); return false;" style="color:#059669;font-weight:600;'
        f'text-decoration:none;">⚙ Set your real rate</a> for exact dollars.'
        f'</div>'
    )


def render_methodology_line(scan: dict) -> str:
    """
    Methodology one-liner under leaderboard heading.
    "We sampled {lines_examined:,} live log lines, clustered them into templates,
    and ranked by cost. Representative sample — big services dominate cost."
    """
    lines_examined = scan.get("lines_examined", 0)
    return (
        f'<div style="font-size:0.82rem;color:#64748b;margin-bottom:16px;font-style:italic;">'
        f'We sampled {lines_examined:,} live log lines, clustered them into templates, '
        f'and ranked by cost. Representative sample — big services dominate cost.'
        f'</div>'
    )


def render_similar_families(scan: dict) -> str:
    """
    Similar pattern families section.
    Only render if similar_families non-empty.
    """
    families = scan.get("similar_families", [])

    if not families:
        return ""

    family_cards = []
    for fam in families:
        family_terms = fam.get("family_terms", [])
        members = fam.get("members", [])
        member_count = fam.get("member_count", 0)
        combined_cost = float(fam.get("combined_monthly_cost_usd", 0))
        combined_query = fam.get("combined_query", "")

        # List members
        member_lines = []
        for member in members:
            tpl = member.get("template", "")
            if tpl:
                member_lines.append(f'<div style="font-family:\'SF Mono\',\'Fira Code\',\'Consolas\',monospace;font-size:0.78rem;color:#64748b;margin-bottom:6px;">{_highlight_template(tpl)}</div>')

        member_html = "".join(member_lines)

        query_html = ""
        if combined_query:
            query_html = f"""
<div style="margin-top:14px;padding:10px 14px;background:#0f172a;border-radius:7px;font-family:'SF Mono','Fira Code','Consolas',monospace;
font-size:0.78rem;color:#86efac;overflow-x:auto;word-break:break-all;">{_esc(combined_query)}</div>"""

        family_card = f"""
<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin-bottom:12px;">
  <div style="font-size:0.82rem;font-weight:600;color:#0f172a;margin-bottom:10px;">
    <strong>{member_count}</strong> templates · <span style="color:#059669;font-weight:700;"
      data-usd="{combined_cost}" data-price-key="indexed_log_per_million">{_fmt_usd(combined_cost)}/mo</span> combined
  </div>
  {member_html}
  {query_html}
</div>"""
        family_cards.append(family_card)

    return (
        f'<div style="margin-bottom:28px;">'
        f'<h2 style="margin:0 0 18px 0;font-size:1.1rem;font-weight:700;color:#0f172a;">Similar Pattern Families — handle as one</h2>'
        f'{"".join(family_cards)}'
        f'</div>'
    )


def render_dashboard(scan: dict, write_enabled: bool = False, apply_token: str = "") -> str:
    """
    Full dashboard body string. Reframed PATTERN-FIRST.

    NEW layout (v3 pattern-intelligence):
      1. HERO (rewritten): lead with most-expensive leaderboard row
      2. SCOPE-GATE BANNER (only if scope_gate True)
      3. STATS BAR: patterns found, recoverable, bill share, surges caught
      4. LEADERBOARD CENTERPIECE: all pattern rows sorted by cost
      5. SURGE SECTION: growing/new patterns
      6. SIMILAR FAMILIES: grouped pattern families (if any)
      7. SCOPE CHECKLIST: collapsed <details>
      8. BY-SERVICE COST MAP: collapsed <details> at bottom

    SECURITY: apply_token is the only write-flow credential that reaches this
    renderer. api_key / app_key / write_key must NEVER be passed here.
    """
    # Settings panel (always)
    price_source  = scan.get("price_source", "list")
    settings_html = render_settings_panel(price_source)

    # 1. HERO: find star row (most expensive with actionable recommendation)
    leaderboard = scan.get("pattern_leaderboard", [])
    opps_by_template = {o.get("template"): o for o in scan.get("opportunities", [])}

    star_row = None
    for row in leaderboard:
        action = row.get("recommended_action", "keep")
        if action in ("exclude", "sample", "review"):
            if star_row is None or float(row.get("monthly_cost_usd", 0)) > float(star_row.get("monthly_cost_usd", 0)):
                star_row = row

    # Build lookup: template -> opportunity (only for applyable opps)
    opps_by_template_star = {}
    for o in scan.get("opportunities", []):
        if o.get("id") and o.get("needs_write_scope"):
            opps_by_template_star[o.get("template")] = o

    hero_html = ""
    if star_row:
        template = star_row.get("template", "")
        sample = star_row.get("sample_redacted", "")
        monthly_cost = float(star_row.get("monthly_cost_usd", 0))
        monthly_events = int(star_row.get("monthly_events", 0))
        share_pct = float(star_row.get("share_pct", 0))
        services = star_row.get("services", [])
        action = star_row.get("recommended_action", "")
        classification = star_row.get("classification", {})
        why_safe = classification.get("why_safe", "")

        # Services line (escape each service name to prevent XSS)
        service_names = ", ".join(_esc(s["service"]) for s in services)

        # CTA button
        cta_button = ""
        opp = opps_by_template_star.get(template)
        if opp and write_enabled and bool(apply_token):
            # Render as inline apply form
            oid = _esc(opp.get("id", ""))
            safe_token = _esc(apply_token)
            cta_button = (
                f'<form method="POST" action="/apply" style="display:inline;">'
                f'<input type="hidden" name="opp_id" value="{oid}">'
                f'<input type="hidden" name="apply_token" value="{safe_token}">'
                f'<button type="submit" '
                f'style="display:inline-flex;align-items:center;gap:6px;background:#059669;'
                f'color:#fff;border:none;text-decoration:none;border-radius:7px;padding:9px 20px;'
                f'font-size:0.88rem;font-weight:700;transition:background 0.15s;cursor:pointer;"'
                f' onmouseover="this.style.background=\'#047857\'" onmouseout="this.style.background=\'#059669\'">'
                f'Kill it — Exclude</button>'
                f'</form>'
            )
        elif action in ("exclude", "sample", "review"):
            action_cls_map = {"exclude": "action-exclude", "sample": "action-sample", "review": "action-review"}
            action_label_map = {"exclude": "EXCLUDE", "sample": "SAMPLE", "review": "REVIEW"}
            cta_button = f'<span class="action-badge {action_cls_map.get(action)}">{action_label_map.get(action)}</span>'

        hero_html = f"""
<div class="hero-card">
  <a class="settings-link" onclick="openSettings(); return false;" href="#" title="Price settings">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>Price settings
  </a>
  <div class="hero-eyebrow">MOST EXPENSIVE REPEATED PATTERN · ACCOUNT-WIDE</div>
  <div class="hero-amount" data-usd="{monthly_cost * 12}" data-price-key="indexed_log_per_million">
    <span class="currency">$</span>{monthly_cost * 12:,.0f}/yr
  </div>
  <div style="font-family:'SF Mono','Fira Code','Consolas',monospace;font-size:0.92rem;color:#e2e8f0;margin-bottom:20px;word-break:break-all;">{_highlight_template(template)}</div>
  <div style="font-size:0.80rem;color:#a7f3d0;margin-bottom:8px;">sample (redacted): <em>{_esc(sample[:80])}</em></div>
  <div class="hero-sub">{monthly_events:,}/mo across {service_names} · {_fmt_usd(monthly_cost)}/mo · {share_pct:.1f}% of your log bill</div>
  <div style="color:#a7f3d0;font-size:0.82rem;margin-bottom:20px;line-height:1.55;">{_esc(why_safe)}</div>
  <div class="hero-notes">
    {cta_button}
    <a href="#leaderboard" style="display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.2);color:#d1fae5;text-decoration:none;border-radius:7px;padding:9px 16px;font-size:0.88rem;font-weight:600;transition:background 0.15s;" onmouseover="this.style.background='rgba(255,255,255,0.18)'" onmouseout="this.style.background='rgba(255,255,255,0.1)';">See all {len(leaderboard)} patterns ↓</a>
  </div>
</div>"""
    else:
        hero_html = f'<div class="hero-card"><div class="hero-eyebrow">PATTERN ANALYSIS</div><div class="hero-amount" style="font-size:2.2rem;">No patterns</div><div class="hero-sub">Your logs are optimized or sampling is disabled.</div></div>'

    # 2. SCOPE-GATE BANNER (only if scope_gate True)
    scope_gate_html = ""
    if scan.get("scope_gate", False):
        scope_gate_html = (
            f'<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:14px 18px;'
            f'margin-bottom:28px;font-size:0.85rem;color:#78350f;display:flex;align-items:flex-start;gap:10px;">'
            f'<span style="flex-shrink:0;font-size:1.1rem;">⚠</span>'
            f'<div><strong>Add the <code style="background:rgba(0,0,0,0.08);padding:1px 5px;border-radius:3px;">logs_read_data</code> scope</strong> '
            f'to unlock pattern intelligence — you\'re seeing the by-service cost map only.</div>'
            f'</div>'
        )

    # ROI BANNER (new)
    roi_html = render_roi_banner(scan)

    # TRUST NUDGE (new)
    trust_nudge_html = render_trust_nudge(scan)

    # 3. STATS BAR
    n_patterns = len(leaderboard)
    total_waste = float(scan.get("total_monthly_waste_usd", 0))
    bill_share = float(scan.get("bill_share_pct", 0))
    n_surges = len(scan.get("surges", []))

    stats_html = (
        f'<div class="stats-bar">'
        f'<div class="stat-card">'
        f'<div class="stat-label">Patterns found</div>'
        f'<div class="stat-value stat-value-blue">{n_patterns}</div>'
        f'</div>'
        f'<div class="stat-card">'
        f'<div class="stat-label">Recoverable</div>'
        f'<div class="stat-value stat-value-green">{_fmt_usd(total_waste)}/mo</div>'
        f'<div class="stat-sub">{_fmt_usd(total_waste * 12)}/yr</div>'
        f'</div>'
        f'<div class="stat-card">'
        f'<div class="stat-label">Bill share</div>'
        f'<div class="stat-value stat-value-purple">{bill_share:.0f}%</div>'
        f'<div class="stat-sub">top patterns = this much of cost</div>'
        f'</div>'
        f'<div class="stat-card">'
        f'<div class="stat-label">Surges caught</div>'
        f'<div class="stat-value stat-value-amber">{n_surges}</div>'
        f'</div>'
        f'</div>'
    )

    # 4. LEADERBOARD CENTERPIECE
    leaderboard_html = ""
    if leaderboard:
        # METHODOLOGY LINE (new)
        methodology_html = render_methodology_line(scan)
        leaderboard_heading = (
            f'<div style="margin-bottom:18px;">'
            f'<h2 style="margin:0;font-size:1.05rem;font-weight:700;color:#0f172a;">These {len(leaderboard)} lines are {bill_share:.0f}% of your log bill</h2>'
            f'{methodology_html}'
            f'</div>'
        )
        leaderboard_html = leaderboard_heading + render_pattern_leaderboard(scan, write_enabled, apply_token)

    # 5. SURGE SECTION
    surge_html = render_surge_section(scan)

    # 6. SIMILAR FAMILIES
    families_html = render_similar_families(scan)

    # 7. SCOPE CHECKLIST (collapsed)
    scope_check = scan.get("scope_check", {})
    scope_collapsible_html = ""
    if scope_check:
        scope_inner = render_scope_checklist(scope_check)
        scope_collapsible_html = (
            f'<details style="background:var(--surface);border:1px solid var(--border);border-radius:12px;'
            f'overflow:hidden;margin-bottom:28px;box-shadow:0 1px 4px rgba(0,0,0,0.04);">'
            f'<summary style="cursor:pointer;padding:14px 22px;font-size:0.88rem;font-weight:600;'
            f'color:#64748b;background:#f8fafc;user-select:none;display:flex;align-items:center;gap:8px;'
            f'list-style:none;">'
            f'<span style="display:inline-block;font-size:0.68rem;color:#94a3b8;transition:transform 0.18s;'
            f'will-change:transform;">▶</span>Scope Checklist</summary>'
            f'<div style="padding:0;">{scope_inner}</div></details>'
        )

    # 8. COST MAP (collapsed at bottom)
    cost_map_html = ""
    if scan.get("log_cost_map"):
        cost_map_inner = render_log_cost_map(scan)
        cost_map_html = (
            f'<details style="background:var(--surface);border:1px solid var(--border);border-radius:12px;'
            f'overflow:hidden;margin-bottom:28px;box-shadow:0 1px 4px rgba(0,0,0,0.04);">'
            f'<summary style="cursor:pointer;padding:14px 22px;font-size:0.88rem;font-weight:600;'
            f'color:#64748b;background:#f8fafc;user-select:none;display:flex;align-items:center;gap:8px;'
            f'list-style:none;">'
            f'<span style="display:inline-block;font-size:0.68rem;color:#94a3b8;transition:transform 0.18s;'
            f'will-change:transform;">▶</span>By-service cost map (secondary view)</summary>'
            f'<div style="padding:0;">{cost_map_inner}</div></details>'
        )

    parts = [
        f"<style>{DASHBOARD_CSS}</style>",
        DASHBOARD_JS,
        settings_html,
        '<div class="container-wide" style="padding-top:32px;">',
        hero_html,
        roi_html,
        trust_nudge_html,
        scope_gate_html,
        stats_html,
        '<div id="leaderboard"></div>',
        leaderboard_html,
        surge_html,
        families_html,
        scope_collapsible_html,
        cost_map_html,
        "</div>",
    ]
    return "\n".join(parts)


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


# ── render_protection_page ─────────────────────────────────────────────────────

def render_protection_page(policy: dict, audit_lines: list = None) -> str:
    """
    Render a full HTML form for configuring ObservaBill's automated protection.

    The page includes:
    1. Intro copy explaining the 3 dispositions (Recommend, Alert, Auto)
    2. Master toggles: enabled, dry_run
    3. Per-finding mode selects (7 kinds grouped into Pattern findings and Surges)
    4. Thresholds: numeric inputs and confidence dropdown
    5. Guardrails: max actions/day + action whitelist checkboxes
    6. Channels: email and slack_webhook text inputs
    7. Credentials: write-only password inputs for api_key, app_key, write_key, site
    8. Recent automated actions (audit_lines if provided)

    All values are HTML-escaped to prevent XSS. Credentials are never rendered as values.

    Args:
        policy: dict with shape { enabled, dry_run, modes{}, thresholds{}, guardrails{}, channels{} }
        audit_lines: Optional list of masked audit strings to display in recent actions section

    Returns:
        Inner HTML body string (caller wraps with html_page)
    """
    if audit_lines is None:
        audit_lines = []

    # Extract policy values with defaults
    enabled = policy.get("enabled", False)
    dry_run = policy.get("dry_run", True)
    modes = policy.get("modes", {})
    thresholds = policy.get("thresholds", {})
    guardrails = policy.get("guardrails", {})
    channels = policy.get("channels", {})

    # Group finding kinds
    pattern_kinds = ["exclude", "sample", "to_metric", "review"]
    surge_kinds = ["new_pattern", "cost_surge", "volume_surge"]

    # Render mode select HTML
    def _mode_select(kind: str) -> str:
        current = modes.get(kind, "recommend")
        options = "\n".join(
            f'    <option value="{opt}" {"selected" if opt == current else ""}>{opt.capitalize()}</option>'
            for opt in ["recommend", "alert", "auto"]
        )
        label = kind.replace("_", " ").title()
        return f"""<div style="margin-bottom:12px;">
  <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">{_esc(label)}</label>
  <select name="mode_{_esc(kind)}" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;">
{options}
  </select>
</div>"""

    pattern_selects = "\n".join(_mode_select(k) for k in pattern_kinds)
    surge_selects = "\n".join(_mode_select(k) for k in surge_kinds)

    # Confidence dropdown
    current_confidence = thresholds.get("min_confidence_for_auto", "high")
    confidence_options = "\n".join(
        f'    <option value="{opt}" {"selected" if opt == current_confidence else ""}>{opt.capitalize()}</option>'
        for opt in ["high", "medium", "low"]
    )

    # Guardrails checkboxes
    auto_only_actions = guardrails.get("auto_only_actions", [])
    auto_only_checkboxes = ""
    for action in ["exclude", "sample", "to_metric"]:
        checked = "checked" if action in auto_only_actions else ""
        auto_only_checkboxes += f"""  <label style="display:flex;align-items:center;gap:6px;margin-bottom:8px;">
    <input type="checkbox" name="auto_only_{_esc(action)}" {checked} style="width:16px;height:16px;">
    <span style="font-size:0.84rem;">{_esc(action).replace('_', ' ').title()}</span>
  </label>
"""

    # Audit lines section
    audit_html = ""
    if audit_lines:
        audit_list = "\n".join(
            f"  <li style=\"margin-bottom:8px;font-size:0.82rem;color:#475569;\">{_esc(line)}</li>"
            for line in audit_lines
        )
        audit_html = f"""<div style="margin-top:32px;padding:20px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">
  <h3 style="margin:0 0 12px 0;font-size:0.95rem;color:#0f172a;">Recent automated actions</h3>
  <ul style="margin:0;padding:0 0 0 16px;">
{audit_list}
  </ul>
</div>"""
    else:
        audit_html = """<div style="margin-top:32px;padding:20px;background:#fef3c7;border:1px solid #fde68a;border-radius:10px;">
  <p style="margin:0;font-size:0.82rem;color:#78350f;">No automated actions yet.</p>
</div>"""

    # Has credentials stored?
    has_creds = policy.get("_has_creds", False)
    creds_note = ""
    if has_creds:
        creds_note = '<div style="font-size:0.72rem;color:#64748b;margin-top:4px;">✓ Credentials stored (leave blank to keep)</div>'

    body = f"""<div style="max-width:900px;margin:0 auto;padding:40px 24px;">
  <h1 style="margin:0 0 12px 0;font-size:1.85rem;font-weight:800;color:#0f172a;">Protection Settings</h1>
  <p style="margin:0 0 28px 0;font-size:0.95rem;color:#475569;line-height:1.6;">
    Configure how ObservaBill responds to cost anomalies and savings opportunities. Choose from three dispositions per finding type:
  </p>
  <div style="padding:16px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;margin-bottom:24px;font-size:0.84rem;color:#166534;line-height:1.6;">
    <p style="margin:6px 0;"><strong>Recommend</strong> — Show it in the dashboard only, never alert or auto-apply.</p>
    <p style="margin:6px 0;"><strong>Alert</strong> — Email or Slack you immediately (if it meets cost threshold).</p>
    <p style="margin:6px 0;"><strong>Auto</strong> — Apply the fix automatically, with guardrails (confidence, daily caps, whitelist).</p>
  </div>

  <form method="POST" action="/protection" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:28px;box-shadow:0 1px 4px rgba(0,0,0,0.06);">

    <!-- Master toggles -->
    <fieldset style="border:none;padding:0;margin:0 0 24px 0;">
      <legend style="font-size:0.78rem;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:#64748b;margin-bottom:12px;">Master Controls</legend>
      <label style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
        <input type="checkbox" name="enabled" {"checked" if enabled else ""} style="width:18px;height:18px;cursor:pointer;">
        <span style="font-size:0.88rem;font-weight:500;color:#0f172a;">Enable automated protection</span>
      </label>
      <label style="display:flex;align-items:center;gap:8px;">
        <input type="checkbox" name="dry_run" {"checked" if dry_run else ""} style="width:18px;height:18px;cursor:pointer;">
        <span style="font-size:0.88rem;font-weight:500;color:#0f172a;">Dry-run mode</span>
      </label>
      <p style="margin:8px 0 0 26px;font-size:0.75rem;color:#64748b;">Simulates auto-fixes without writing to Datadog.</p>
    </fieldset>

    <!-- Pattern findings modes -->
    <fieldset style="border:none;padding:0;margin:0 0 24px 0;">
      <legend style="font-size:0.78rem;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:#64748b;margin-bottom:14px;">Pattern Findings</legend>
{pattern_selects}
    </fieldset>

    <!-- Surge findings modes -->
    <fieldset style="border:none;padding:0;margin:0 0 24px 0;">
      <legend style="font-size:0.78rem;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:#64748b;margin-bottom:14px;">Surge Detections</legend>
{surge_selects}
    </fieldset>

    <!-- Thresholds -->
    <fieldset style="border:none;padding:0;margin:0 0 24px 0;">
      <legend style="font-size:0.78rem;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:#64748b;margin-bottom:14px;">Detection Thresholds</legend>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
        <div>
          <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Min cost (USD/month)</label>
          <input type="number" name="min_cost_usd" value="{_esc(str(thresholds.get('min_cost_usd', 100.0)))}" step="10" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
        </div>
        <div>
          <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Surge ratio</label>
          <input type="number" name="surge_ratio" value="{_esc(str(thresholds.get('surge_ratio', 1.30)))}" step="0.05" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
        </div>
        <div>
          <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">WoW growth %</label>
          <input type="number" name="wow_growth_pct" value="{_esc(str(thresholds.get('wow_growth_pct', 15.0)))}" step="1" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
        </div>
        <div>
          <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">New pattern min cost (USD/month)</label>
          <input type="number" name="new_pattern_min_cost_usd" value="{_esc(str(thresholds.get('new_pattern_min_cost_usd', 100.0)))}" step="10" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
        </div>
      </div>
      <div style="margin-top:14px;">
        <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Min confidence for auto</label>
        <select name="min_confidence_for_auto" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;">
{confidence_options}
        </select>
      </div>
    </fieldset>

    <!-- Guardrails -->
    <fieldset style="border:none;padding:0;margin:0 0 24px 0;">
      <legend style="font-size:0.78rem;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:#64748b;margin-bottom:14px;">Guardrails</legend>
      <div style="margin-bottom:14px;">
        <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Max auto-actions per day</label>
        <input type="number" name="auto_max_actions_per_day" value="{_esc(str(guardrails.get('auto_max_actions_per_day', 5)))}" step="1" min="1" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;">
      </div>
      <div>
        <p style="margin:0 0 10px 0;font-size:0.82rem;font-weight:600;color:#0f172a;">Only these action types may auto-apply:</p>
{auto_only_checkboxes}
        <p style="margin:8px 0 0 0;font-size:0.72rem;color:#64748b;">Restricts auto-remediation to safe, reversible actions.</p>
      </div>
    </fieldset>

    <!-- Channels -->
    <fieldset style="border:none;padding:0;margin:0 0 24px 0;">
      <legend style="font-size:0.78rem;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:#64748b;margin-bottom:14px;">Notification Channels</legend>
      <div style="margin-bottom:14px;">
        <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Email address</label>
        <input type="email" name="email" value="{_esc(channels.get('email', ''))}" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
      </div>
      <div>
        <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Slack webhook URL</label>
        <input type="url" name="slack_webhook" value="{_esc(channels.get('slack_webhook', ''))}" placeholder="https://hooks.slack.com/services/..." style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
      </div>
    </fieldset>

    <!-- Credentials (write-only) -->
    <fieldset style="border:none;padding:0;margin:0 0 24px 0;">
      <legend style="font-size:0.78rem;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:#64748b;margin-bottom:14px;">Datadog Credentials (Watchdog)</legend>
      <p style="margin:0 0 14px 0;font-size:0.82rem;color:#475569;">Used by the watchdog to run protection workflows continuously.</p>
      <div style="margin-bottom:14px;">
        <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">API key</label>
        <input type="password" name="api_key" placeholder="Leave blank to keep existing" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
        {creds_note}
      </div>
      <div style="margin-bottom:14px;">
        <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Application key</label>
        <input type="password" name="app_key" placeholder="Leave blank to keep existing" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
      </div>
      <div style="margin-bottom:14px;">
        <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Write key</label>
        <input type="password" name="write_key" placeholder="Leave blank to keep existing" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;box-sizing:border-box;">
      </div>
      <div>
        <label style="display:block;font-size:0.82rem;font-weight:600;margin-bottom:4px;">Site</label>
        <select name="site" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.88rem;">
          <option value="us1" {"selected" if channels.get('site', 'us1') == 'us1' else ''}>us1</option>
          <option value="eu1" {"selected" if channels.get('site', 'us1') == 'eu1' else ''}>eu1</option>
        </select>
      </div>
    </fieldset>

    <!-- Submit -->
    <div style="margin-top:28px;padding-top:20px;border-top:1px solid #e2e8f0;">
      <button type="submit" style="background:#059669;color:#ffffff;border:none;border-radius:7px;padding:10px 24px;font-size:0.92rem;font-weight:700;cursor:pointer;transition:background 0.15s;">
        Save protection settings
      </button>
    </div>
  </form>

{audit_html}

</div>"""

    return body


if __name__ == "__main__":
    import sys

    # Try to reuse html_page from app.py if importable
    try:
        import os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
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
