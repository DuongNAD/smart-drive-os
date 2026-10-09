"""smart_drive.ui.dashboard - Embedded Dark Mode Single Page Application.

Zero external dependencies. No CDN. No external scripts or fonts. 100% Offline.
Provides the HTML/CSS/JS source for the SmartDrive-OS Web Dashboard.
"""

from __future__ import annotations


def get_dashboard_html() -> str:
    """Generates the complete self-contained HTML/CSS/JS document string for the Web Dashboard."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SmartDrive-OS Dashboard</title>
  <style>
    :root {
      --bg-base: #0b0f19;
      --bg-surface: #111827;
      --bg-card: #1e293b;
      --bg-card-hover: #283548;
      --border-base: #334155;
      --border-subtle: #1e293b;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-faint: #64748b;
      --accent-cyan: #38bdf8;
      --accent-emerald: #10b981;
      --accent-amber: #f59e0b;
      --accent-rose: #f43f5e;
      --accent-indigo: #818cf8;
      --accent-purple: #c084fc;
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 16px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-base);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      line-height: 1.5;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border-base);
      padding: 14px 28px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-icon {
      width: 34px;
      height: 34px;
      background: linear-gradient(135deg, var(--accent-cyan), var(--accent-indigo));
      border-radius: var(--radius-sm);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #0b0f19;
      font-weight: 800;
      font-size: 19px;
      box-shadow: 0 0 12px rgba(56, 189, 248, 0.4);
    }
    .brand-title {
      font-size: 18px;
      font-weight: 700;
      letter-spacing: -0.02em;
    }
    .brand-badge {
      font-size: 11px;
      background: rgba(56, 189, 248, 0.15);
      color: var(--accent-cyan);
      padding: 2px 8px;
      border-radius: 9999px;
      border: 1px solid rgba(56, 189, 248, 0.3);
      font-weight: 600;
    }
    .badge-zero {
      font-size: 11px;
      background: rgba(16, 185, 129, 0.15);
      color: var(--accent-emerald);
      padding: 2px 8px;
      border-radius: 9999px;
      border: 1px solid rgba(16, 185, 129, 0.3);
      font-weight: 600;
    }
    .nav-tabs {
      display: flex;
      gap: 8px;
    }
    .nav-tab {
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      padding: 8px 16px;
      border-radius: var(--radius-sm);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s ease;
    }
    .nav-tab:hover {
      background: var(--bg-card);
      color: var(--text-main);
    }
    .nav-tab.active {
      background: var(--bg-card);
      color: var(--accent-cyan);
      border-color: var(--border-base);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
    }
    main {
      flex: 1;
      max-width: 1440px;
      width: 100%;
      margin: 0 auto;
      padding: 24px;
    }
    .drive-banner {
      background: linear-gradient(180deg, var(--bg-surface), var(--bg-card));
      border: 1px solid var(--border-base);
      border-radius: var(--radius-lg);
      padding: 20px 24px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
    }
    .drive-meta h2 {
      font-size: 14px;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 4px;
    }
    .drive-meta .path {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 16px;
      color: var(--accent-cyan);
      word-break: break-all;
      font-weight: 600;
    }
    .metrics-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .metric-card {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-md);
      padding: 18px 20px;
      position: relative;
      transition: transform 0.15s ease, border-color 0.15s ease;
    }
    .metric-card:hover {
      border-color: var(--accent-cyan);
      transform: translateY(-2px);
    }
    .metric-card .label {
      font-size: 13px;
      color: var(--text-muted);
      font-weight: 500;
      margin-bottom: 6px;
    }
    .metric-card .val {
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.02em;
    }
    .metric-card .sub {
      font-size: 12px;
      color: var(--text-faint);
      margin-top: 4px;
    }
    .metric-slack {
      border-color: rgba(245, 158, 11, 0.4);
      background: linear-gradient(135deg, var(--bg-card), rgba(245, 158, 11, 0.08));
    }
    .metric-slack .val {
      color: var(--accent-amber);
    }
    .section-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-lg);
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
    }
    .section-title {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 18px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
    }
    /* Taxonomy Bars */
    .tax-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 16px;
    }
    .tax-card {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-md);
      padding: 16px 18px;
      transition: border-color 0.15s;
    }
    .tax-card:hover {
      border-color: rgba(56, 189, 248, 0.4);
    }
    .tax-head {
      display: flex;
      justify-content: space-between;
      margin-bottom: 10px;
      font-size: 14px;
      font-weight: 600;
    }
    .progress-bar-bg {
      height: 10px;
      background: var(--bg-surface);
      border-radius: 5px;
      overflow: hidden;
      margin-bottom: 10px;
      position: relative;
    }
    .progress-bar-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--accent-cyan), var(--accent-indigo));
      border-radius: 5px;
      transition: width 0.4s ease;
    }
    .progress-bar-slack {
      background: linear-gradient(90deg, var(--accent-amber), var(--accent-rose));
    }
    .tax-metrics {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      color: var(--text-muted);
    }
    /* Tables */
    .table-container {
      overflow-x: auto;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-base);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      text-align: left;
    }
    th {
      background: var(--bg-card);
      color: var(--text-muted);
      font-weight: 600;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-base);
      white-space: nowrap;
    }
    td {
      padding: 11px 16px;
      border-bottom: 1px solid var(--border-subtle);
      color: var(--text-main);
    }
    tr:hover td {
      background: rgba(255, 255, 255, 0.03);
    }
    .code-font {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
    }
    /* Search Bar & Filters */
    .search-box-row {
      display: flex;
      gap: 12px;
      margin-bottom: 14px;
      flex-wrap: wrap;
    }
    .search-input-wrap {
      flex: 1;
      position: relative;
      min-width: 280px;
    }
    .search-input {
      width: 100%;
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-sm);
      padding: 12px 18px;
      color: var(--text-main);
      font-size: 15px;
      outline: none;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .search-input:focus {
      border-color: var(--accent-cyan);
      box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2);
    }
    .filter-btn-group {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }
    .filter-chip {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      color: var(--text-muted);
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
    }
    .filter-chip:hover {
      color: var(--text-main);
      border-color: var(--text-muted);
    }
    .filter-chip.active {
      background: rgba(56, 189, 248, 0.15);
      border-color: var(--accent-cyan);
      color: var(--accent-cyan);
    }
    /* Cleanup Tiers */
    .tier-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }
    .tier-box {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-md);
      padding: 20px;
      position: relative;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .tier-box.selected {
      border-color: var(--accent-cyan);
      box-shadow: 0 0 0 1px var(--accent-cyan), 0 4px 12px rgba(56, 189, 248, 0.15);
    }
    .tier-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }
    .tier-title {
      font-size: 15px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .tier-checkbox {
      width: 18px;
      height: 18px;
      cursor: pointer;
      accent-color: var(--accent-cyan);
    }
    .badge {
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 4px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .badge-safe { background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-dev { background: rgba(56, 189, 248, 0.15); color: var(--accent-cyan); border: 1px solid rgba(56, 189, 248, 0.3); }
    .badge-optin { background: rgba(245, 158, 11, 0.15); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.3); }
    /* Buttons */
    .btn {
      padding: 10px 18px;
      border-radius: var(--radius-sm);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid transparent;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s ease;
    }
    .btn-primary {
      background: var(--accent-cyan);
      color: #0b0f19;
    }
    .btn-primary:hover {
      background: #7dd3fc;
    }
    .btn-danger {
      background: var(--accent-rose);
      color: #fff;
    }
    .btn-danger:hover {
      background: #fb7185;
      box-shadow: 0 0 12px rgba(244, 63, 94, 0.4);
    }
    .btn-secondary {
      background: var(--bg-card);
      border-color: var(--border-base);
      color: var(--text-main);
    }
    .btn-secondary:hover {
      background: var(--bg-card-hover);
      border-color: var(--text-muted);
    }
    /* Modal Dialog */
    .modal-backdrop {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.8);
      backdrop-filter: blur(5px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 100;
    }
    .modal-box {
      background: var(--bg-surface);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-lg);
      max-width: 540px;
      width: 92%;
      padding: 26px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 12px;
      margin-top: 24px;
    }
    /* Toast System */
    .toast-container {
      position: fixed;
      bottom: 24px;
      right: 24px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      z-index: 200;
      pointer-events: none;
    }
    .toast {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      color: var(--text-main);
      padding: 12px 18px;
      border-radius: var(--radius-sm);
      font-size: 13px;
      font-weight: 500;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
      animation: fadeIn 0.2s ease forwards;
      pointer-events: auto;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .hidden { display: none !important; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-icon">S</div>
      <div>
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="brand-title">SmartDrive-OS</span>
          <span class="brand-badge">v1.1.0</span>
          <span class="badge-zero">Zero-Dependency</span>
        </div>
      </div>
    </div>
    <div class="nav-tabs">
      <button class="nav-tab active" onclick="switchTab('overview')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        Overview & Slack
      </button>
      <button class="nav-tab" onclick="switchTab('search')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        Instant Search
      </button>
      <button class="nav-tab" onclick="switchTab('cleanup')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
        Safe Cleanup
      </button>
    </div>
  </header>

  <main>
    <!-- Drive Info Banner -->
    <div class="drive-banner">
      <div class="drive-meta">
        <h2>Target SSD Storage Root</h2>
        <div class="path" id="drive-root-display">Loading...</div>
      </div>
      <div style="display: flex; gap: 20px; align-items: center;">
        <div style="text-align: right;">
          <div style="font-size: 12px; color: var(--text-muted);">Cluster Allocation Unit</div>
          <div style="font-weight: 700; color: var(--accent-cyan); font-size: 16px;" id="cluster-size-display">512 KB</div>
        </div>
        <button class="btn btn-secondary" onclick="refreshAll()" title="Refresh Dashboard Data">⟳ Refresh</button>
      </div>
    </div>

    <!-- Global Metrics Cards -->
    <div class="metrics-row">
      <div class="metric-card">
        <div class="label">Total Files</div>
        <div class="val" id="metric-total-files">--</div>
        <div class="sub" id="metric-total-dirs">-- directories</div>
      </div>
      <div class="metric-card">
        <div class="label">Nominal Data Size</div>
        <div class="val" id="metric-nominal-bytes">--</div>
        <div class="sub">Logical byte length</div>
      </div>
      <div class="metric-card">
        <div class="label">Physical Allocated Space</div>
        <div class="val" id="metric-allocated-bytes">--</div>
        <div class="sub" id="metric-total-clusters">-- clusters (512KB)</div>
      </div>
      <div class="metric-card metric-slack">
        <div class="label">Wasted Cluster Slack</div>
        <div class="val" id="metric-slack-bytes">--</div>
        <div class="sub" id="metric-slack-pct">-- overhead</div>
      </div>
    </div>

    <!-- Tab 1: Overview & Cluster Slack -->
    <div id="tab-overview">
      <div class="section-card">
        <div class="section-title">
          <span>Canonical Taxonomy Storage Allocation</span>
          <span style="font-size: 13px; font-weight: 500; color: var(--text-muted);">Nominal vs 512KB Cluster Slack Waste</span>
        </div>
        <div class="tax-grid" id="taxonomy-grid">
          <!-- Populated dynamically -->
        </div>
      </div>

      <div class="section-card">
        <div class="section-title">
          <span>Top Cluster Slack Hotspots</span>
          <span style="font-size: 13px; font-weight: 500; color: var(--text-muted);">Directories with highest small file overhead</span>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Subtree Directory</th>
                <th>Files</th>
                <th>Nominal Size</th>
                <th>Allocated Space</th>
                <th>Wasted Slack</th>
                <th>Waste %</th>
              </tr>
            </thead>
            <tbody id="slack-hotspots-table">
              <!-- Populated dynamically -->
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Tab 2: Instant Search -->
    <div id="tab-search" class="hidden">
      <div class="section-card">
        <div class="section-title">
          <span>Instant SQLite FTS5 Search</span>
          <span id="search-latency-badge" style="font-size: 12px; color: var(--accent-emerald); font-weight: 600;">⚡ Ready</span>
        </div>
        <div class="search-box-row">
          <div class="search-input-wrap">
            <input type="text" id="search-input" class="search-input" placeholder="Type keyword or query syntax (e.g. llama, ext:gguf, size:>100MB, cat:AI)..." oninput="onSearchInput()">
          </div>
          <button class="btn btn-primary" onclick="executeSearch()">Search</button>
        </div>
        <div class="filter-btn-group" id="category-filter-chips">
          <button class="filter-chip active" onclick="setCategoryFilter('')">All Categories</button>
          <button class="filter-chip" onclick="setCategoryFilter('AI Models')">AI Models</button>
          <button class="filter-chip" onclick="setCategoryFilter('Code')">Code</button>
          <button class="filter-chip" onclick="setCategoryFilter('Books/Learning')">Books/Learning</button>
          <button class="filter-chip" onclick="setCategoryFilter('Docs')">Docs</button>
          <button class="filter-chip" onclick="setCategoryFilter('Media')">Media</button>
          <button class="filter-chip" onclick="setCategoryFilter('Archives')">Archives</button>
        </div>
        <div id="search-warning" class="hidden" style="color: var(--accent-amber); font-size: 12px; margin: 8px 0;"></div>
        <div class="table-container" id="search-results-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Relative Path</th>
                <th>Category</th>
                <th>Nominal Size</th>
                <th>Allocated Space</th>
                <th>Cluster Slack</th>
                <th>BM25 Rank</th>
              </tr>
            </thead>
            <tbody id="search-results-body">
              <tr><td colspan="7" style="text-align: center; color: var(--text-faint);">Enter a keyword or click a category filter to search.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Tab 3: Safe Cleanup -->
    <div id="tab-cleanup" class="hidden">
      <div class="section-card">
        <div class="section-title">
          <span>Safe 3-Tier Storage Cleanup Dashboard</span>
          <button class="btn btn-secondary" onclick="loadJunkPreview()">⟳ Rescan Junk</button>
        </div>
        <div class="tier-grid">
          <!-- Tier 1 -->
          <div class="tier-box selected" id="tier-box-1">
            <div class="tier-header">
              <div class="tier-title">
                <input type="checkbox" id="chk-tier-1" class="tier-checkbox" checked onchange="updateTierSelection()">
                <span>Tier 1: Safe OS Junk</span>
              </div>
              <span class="badge badge-safe">Recommended</span>
            </div>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
              macOS AppleDouble (._*), .DS_Store, Windows Thumbs.db, and Python bytecode caches (__pycache__, *.pyc).
            </p>
            <div style="font-size: 12px; color: var(--text-faint);">
              Items: <strong id="t1-count" style="color: var(--text-main);">0</strong> | 
              Slack: <strong id="t1-slack" style="color: var(--accent-amber);">0 B</strong>
            </div>
          </div>
          <!-- Tier 2 -->
          <div class="tier-box" id="tier-box-2">
            <div class="tier-header">
              <div class="tier-title">
                <input type="checkbox" id="chk-tier-2" class="tier-checkbox" onchange="updateTierSelection()">
                <span>Tier 2: Dev & Test Caches</span>
              </div>
              <span class="badge badge-dev">Regenerable</span>
            </div>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
              Pytest, Ruff, Mypy caches, and packaging build artifacts (build, dist, *.egg-info).
            </p>
            <div style="font-size: 12px; color: var(--text-faint);">
              Items: <strong id="t2-count" style="color: var(--text-main);">0</strong> | 
              Slack: <strong id="t2-slack" style="color: var(--accent-amber);">0 B</strong>
            </div>
          </div>
          <!-- Tier 3 -->
          <div class="tier-box" id="tier-box-3">
            <div class="tier-header">
              <div class="tier-title">
                <input type="checkbox" id="chk-tier-3" class="tier-checkbox" onchange="updateTierSelection()">
                <span>Tier 3: Temporary & Dumps</span>
              </div>
              <span class="badge badge-optin">Opt-in</span>
            </div>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
              Crash dumps (*.dmp, core.*), temporary scratch files (*.tmp), and editor swap files (*.swp).
            </p>
            <div style="font-size: 12px; color: var(--text-faint);">
              Items: <strong id="t3-count" style="color: var(--text-main);">0</strong> | 
              Slack: <strong id="t3-slack" style="color: var(--accent-amber);">0 B</strong>
            </div>
          </div>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 12px;">
          <div style="font-size: 14px;">
            Selected for Purge: <strong id="selected-summary-count">0</strong> items (<strong id="selected-summary-nominal" style="color: var(--accent-cyan);">0 B</strong> nominal, <strong id="selected-summary-slack" style="color: var(--accent-amber);">0 B</strong> cluster slack)
          </div>
          <div style="display: flex; gap: 12px;">
            <button class="btn btn-secondary" onclick="simulateDryRun()">Dry-Run Simulation</button>
            <button class="btn btn-danger" onclick="openPurgeModal()">1-Click Clean Now</button>
          </div>
        </div>

        <div class="table-container" id="junk-items-preview">
          <table>
            <thead>
              <tr>
                <th>Tier</th>
                <th>File / Folder Path</th>
                <th>Rule Description</th>
                <th>Nominal Size</th>
                <th>Allocated Space</th>
                <th>Cluster Slack</th>
              </tr>
            </thead>
            <tbody id="junk-preview-tbody">
              <!-- Populated dynamically -->
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </main>

  <!-- Confirmation Modal -->
  <div id="purge-modal" class="modal-backdrop hidden">
    <div class="modal-box">
      <h3 style="font-size: 18px; margin-bottom: 12px; color: var(--accent-rose);">⚠️ Confirm Permanent Purge</h3>
      <p style="font-size: 14px; color: var(--text-muted); margin-bottom: 16px;">
        You are about to permanently purge <strong id="modal-item-count" style="color: var(--text-main);">0</strong> junk items across selected tiers.
        Root configuration files (<code>GEMINI.md</code>, <code>README.md</code>) and anti-indexing shields are protected by <code>SecurityGuard</code>.
      </p>
      <div style="background: var(--bg-card); padding: 14px; border-radius: var(--radius-sm); font-size: 13px; margin-bottom: 16px; border: 1px solid var(--border-base);">
        <div style="margin-bottom: 4px;">Reclaimable Nominal Size: <strong id="modal-nominal-size" style="color: var(--accent-cyan);">0 B</strong></div>
        <div>Reclaimable Cluster Slack: <strong id="modal-slack-size" style="color: var(--accent-amber);">0 B</strong></div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" onclick="closePurgeModal()">Cancel</button>
        <button class="btn btn-danger" onclick="executeConfirmedPurge()">Yes, Purge Now</button>
      </div>
    </div>
  </div>

  <!-- Toast Container -->
  <div class="toast-container" id="toast-container"></div>

  <script>
    let activeCategory = '';
    let searchTimeout = null;
    let cachedJunkData = null;

    function showToast(message) {
      const container = document.getElementById('toast-container');
      const toast = document.createElement('div');
      toast.className = 'toast';
      toast.innerText = message;
      container.appendChild(toast);
      setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
      }, 4000);
    }

    function formatBytes(bytes) {
      if (!bytes || bytes === 0) return '0 B';
      if (bytes < 1024) return bytes + ' B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
    }

    // File and folder names come from the drive, so they are untrusted: escape before using innerHTML.
    function esc(value) {
      const entities = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
      return String(value == null ? '' : value).replace(/[&<>"']/g, ch => entities[ch]);
    }

    function switchTab(tabId) {
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      ['overview', 'search', 'cleanup'].forEach(id => {
        const el = document.getElementById('tab-' + id);
        if (el) el.classList.add('hidden');
      });
      const activeBtn = Array.from(document.querySelectorAll('.nav-tab')).find(b => b.innerText.toLowerCase().includes(tabId));
      if (activeBtn) activeBtn.classList.add('active');
      const target = document.getElementById('tab-' + tabId);
      if (target) target.classList.remove('hidden');

      if (tabId === 'cleanup' && !cachedJunkData) {
        loadJunkPreview();
      }
    }

    async function loadStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        document.getElementById('drive-root-display').innerText = data.root || 'Unknown';
        document.getElementById('cluster-size-display').innerText = (data.cluster_size_kb || 512) + ' KB';
      } catch (err) {
        console.error('Failed to load status:', err);
      }
    }

    async function loadAudit() {
      try {
        const res = await fetch('/api/audit');
        const data = await res.json();
        const summary = data.summary || {};
        document.getElementById('metric-total-files').innerText = (summary.total_files || 0).toLocaleString();
        document.getElementById('metric-total-dirs').innerText = (summary.total_directories || 0).toLocaleString() + ' directories';
        document.getElementById('metric-nominal-bytes').innerText = formatBytes(summary.total_logical_bytes || 0);
        document.getElementById('metric-allocated-bytes').innerText = formatBytes(summary.total_allocated_bytes || 0);
        document.getElementById('metric-total-clusters').innerText = (summary.total_clusters || 0).toLocaleString() + ' clusters';
        document.getElementById('metric-slack-bytes').innerText = formatBytes(summary.total_slack_bytes || 0);
        document.getElementById('metric-slack-pct').innerText = (summary.total_slack_percentage || 0) + '% overhead';

        // Render Taxonomies
        const taxGrid = document.getElementById('taxonomy-grid');
        taxGrid.innerHTML = '';
        const taxonomies = data.taxonomies || {};
        for (const [name, stat] of Object.entries(taxonomies)) {
          const card = document.createElement('div');
          card.className = 'tax-card';
          const pct = stat.slack_percentage || 0;
          card.innerHTML = `
            <div class="tax-head">
              <span>${esc(name)}</span>
              <span style="color: var(--accent-amber);">${pct.toFixed(1)}% waste</span>
            </div>
            <div class="progress-bar-bg">
              <div class="progress-bar-fill progress-bar-slack" style="width: ${Math.min(100, Math.max(4, pct))}%;"></div>
            </div>
            <div class="tax-metrics">
              <span>${(stat.file_count || 0).toLocaleString()} files</span>
              <span>${formatBytes(stat.nominal_bytes)} (alloc ${formatBytes(stat.allocated_bytes)})</span>
            </div>
          `;
          taxGrid.appendChild(card);
        }

        // Render Hotspots
        const hotspotsTable = document.getElementById('slack-hotspots-table');
        hotspotsTable.innerHTML = '';
        const topSlack = data.top_slack_directories || [];
        if (topSlack.length === 0) {
          hotspotsTable.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-faint);">No slack hotspots found.</td></tr>';
        } else {
          for (const d of topSlack.slice(0, 10)) {
            const row = document.createElement('tr');
            row.innerHTML = `
              <td class="code-font">${esc(d.rel_path)}</td>
              <td>${(d.recursive_files || 0).toLocaleString()}</td>
              <td>${formatBytes(d.recursive_bytes || 0)}</td>
              <td>${formatBytes(d.recursive_allocated || 0)}</td>
              <td style="color: var(--accent-amber); font-weight: 600;">${formatBytes(d.recursive_slack || 0)}</td>
              <td>${(d.recursive_slack_percentage || 0).toFixed(1)}%</td>
            `;
            hotspotsTable.appendChild(row);
          }
        }
      } catch (err) {
        console.error('Failed to load audit:', err);
      }
    }

    function onSearchInput() {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(executeSearch, 150);
    }

    function setCategoryFilter(cat) {
      activeCategory = cat;
      document.querySelectorAll('#category-filter-chips .filter-chip').forEach(b => {
        b.classList.toggle('active', (cat === '' && b.innerText.includes('All')) || (cat && b.innerText === cat));
      });
      executeSearch();
    }

    async function executeSearch() {
      const q = document.getElementById('search-input').value.trim();
      const badge = document.getElementById('search-latency-badge');
      badge.innerText = '⚡ Searching...';

      let url = '/api/search?limit=100';
      if (q) url += '&q=' + encodeURIComponent(q);
      if (activeCategory) url += '&category=' + encodeURIComponent(activeCategory);

      try {
        const t0 = performance.now();
        const res = await fetch(url);
        const data = await res.json();
        const latency = data.latency_ms || Math.round(performance.now() - t0);
        badge.innerText = `⚡ ${latency}ms | ${data.total || data.total_count || 0} matches`;

        // Filters the server could not apply (e.g. size:>abc); textContent, so nothing here is parsed as HTML.
        const warnings = data.warnings || [];
        const warningBox = document.getElementById('search-warning');
        warningBox.textContent = warnings.length ? '⚠ ' + warnings.join(' | ') : '';
        warningBox.classList.toggle('hidden', warnings.length === 0);

        const tbody = document.getElementById('search-results-body');
        tbody.innerHTML = '';
        const results = data.results || data.matches || [];
        if (results.length === 0) {
          const msg = data.index_exists === false 
            ? 'Search index does not exist yet. Run `smart-drive index` to build index.'
            : 'No matching files found.';
          tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-faint);">${msg}</td></tr>`;
          return;
        }

        for (const m of results) {
          const row = document.createElement('tr');
          const rankVal = typeof m.rank === 'number' ? m.rank.toFixed(2) : (m.rank || '0.00');
          row.innerHTML = `
            <td style="font-weight: 600; color: var(--accent-cyan);">${esc(m.name)}</td>
            <td class="code-font" style="color: var(--text-muted);">${esc(m.path)}</td>
            <td><span class="badge badge-dev">${esc(m.category || 'Other')}</span></td>
            <td>${formatBytes(m.size)}</td>
            <td>${formatBytes(m.allocated_size || 0)}</td>
            <td style="color: var(--accent-amber);">${formatBytes(m.slack_bytes || 0)}</td>
            <td style="color: var(--text-faint);">${esc(rankVal)}</td>
          `;
          tbody.appendChild(row);
        }
      } catch (err) {
        badge.innerText = 'Error';
        console.error('Search failed:', err);
      }
    }

    async function loadJunkPreview() {
      try {
        const res = await fetch('/api/junk');
        const data = await res.json();
        cachedJunkData = data;

        const tiers = data.tiers || {};
        document.getElementById('t1-count').innerText = (tiers['1']?.count || 0) + ' items';
        document.getElementById('t1-slack').innerText = formatBytes(tiers['1']?.slack_bytes || 0);

        document.getElementById('t2-count').innerText = (tiers['2']?.count || 0) + ' items';
        document.getElementById('t2-slack').innerText = formatBytes(tiers['2']?.slack_bytes || 0);

        document.getElementById('t3-count').innerText = (tiers['3']?.count || 0) + ' items';
        document.getElementById('t3-slack').innerText = formatBytes(tiers['3']?.slack_bytes || 0);

        updateTierSelection();
        showToast('Junk detection scan complete.');
      } catch (err) {
        console.error('Failed to load junk:', err);
      }
    }

    function getSelectedTiers() {
      const selected = [];
      if (document.getElementById('chk-tier-1').checked) selected.push(1);
      if (document.getElementById('chk-tier-2').checked) selected.push(2);
      if (document.getElementById('chk-tier-3').checked) selected.push(3);
      return selected;
    }

    function updateTierSelection() {
      const selected = getSelectedTiers();
      [1, 2, 3].forEach(t => {
        const box = document.getElementById('tier-box-' + t);
        if (box) box.classList.toggle('selected', selected.includes(t));
      });

      if (!cachedJunkData) return;
      const tiers = cachedJunkData.tiers || {};
      let totalCount = 0;
      let totalNominal = 0;
      let totalSlack = 0;
      const previewItems = [];

      selected.forEach(t => {
        const stat = tiers[String(t)];
        if (stat) {
          totalCount += stat.count;
          totalNominal += stat.nominal_bytes;
          totalSlack += stat.slack_bytes;
          previewItems.push(...(stat.items || []));
        }
      });

      document.getElementById('selected-summary-count').innerText = totalCount.toLocaleString();
      document.getElementById('selected-summary-nominal').innerText = formatBytes(totalNominal);
      document.getElementById('selected-summary-slack').innerText = formatBytes(totalSlack);

      const tbody = document.getElementById('junk-preview-tbody');
      tbody.innerHTML = '';
      if (previewItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-faint);">No junk items in selected tiers.</td></tr>';
        return;
      }

      for (const item of previewItems.slice(0, 100)) {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td><span class="badge ${item.tier === 1 ? 'badge-safe' : item.tier === 2 ? 'badge-dev' : 'badge-optin'}">T${item.tier}</span></td>
          <td class="code-font">${esc(item.rel_path)}</td>
          <td>${esc(item.description || item.rule || '')}</td>
          <td>${formatBytes(item.size)}</td>
          <td>${formatBytes(item.allocated_size)}</td>
          <td style="color: var(--accent-amber);">${formatBytes(item.slack_bytes || 0)}</td>
        `;
        tbody.appendChild(row);
      }
    }

    function openPurgeModal() {
      const selected = getSelectedTiers();
      if (selected.length === 0) {
        alert('Please select at least one tier to clean.');
        return;
      }
      document.getElementById('modal-item-count').innerText = document.getElementById('selected-summary-count').innerText;
      document.getElementById('modal-nominal-size').innerText = document.getElementById('selected-summary-nominal').innerText;
      document.getElementById('modal-slack-size').innerText = document.getElementById('selected-summary-slack').innerText;
      document.getElementById('purge-modal').classList.remove('hidden');
    }

    function closePurgeModal() {
      document.getElementById('purge-modal').classList.add('hidden');
    }

    async function simulateDryRun() {
      const selected = getSelectedTiers();
      if (selected.length === 0) {
        alert('Please select at least one tier for dry-run simulation.');
        return;
      }
      try {
        const res = await fetch('/api/junk/clean', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tiers: selected, dry_run: true })
        });
        const result = await res.json();
        showToast(`Dry-Run: ${result.total_attempted} simulated, ${formatBytes(result.nominal_bytes_reclaimed)} reclaimable.`);
        alert(`[Dry-Run Simulation Complete]\\nAttempted: ${result.total_attempted}\\nSucceeded: ${result.total_succeeded}\\nNominal Space: ${formatBytes(result.nominal_bytes_reclaimed)}\\nAllocated Cluster Space: ${formatBytes(result.allocated_bytes_reclaimed)}\\nFiles remain untouched on disk.`);
      } catch (err) {
        alert('Dry run simulation failed: ' + err);
      }
    }

    async function executeConfirmedPurge() {
      closePurgeModal();
      const selected = getSelectedTiers();
      try {
        const res = await fetch('/api/junk/clean', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tiers: selected, dry_run: false })
        });
        const result = await res.json();
        showToast(`Purge complete: ${formatBytes(result.nominal_bytes_reclaimed)} reclaimed!`);
        alert(`✓ Purge Execution Complete!\\nReclaimed: ${formatBytes(result.nominal_bytes_reclaimed)} (${result.total_succeeded} files unlinked).\\nAllocated cluster slack freed: ${formatBytes(result.allocated_bytes_reclaimed)}`);
        refreshAll();
      } catch (err) {
        alert('Purge execution failed: ' + err);
      }
    }

    function refreshAll() {
      loadStatus();
      loadAudit();
      loadJunkPreview();
    }

    window.addEventListener('DOMContentLoaded', () => {
      refreshAll();
    });
  </script>
</body>
</html>
"""
