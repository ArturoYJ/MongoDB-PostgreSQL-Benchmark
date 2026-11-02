"""Generate publication-ready figures for BDaaS contention benchmark report."""
import csv, json
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# Set publication style
plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.size': 10,
    'axes.labelsize': 11,
    'axes.titlesize': 12,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 9,
    'figure.titlesize': 13,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'axes.grid': True,
    'grid.alpha': 0.3,
    'grid.linestyle': '--'
})

WORKSPACE_ROOT = Path('/Users/arturoyj/Universidad Politecnica/7 cuatrimestre/BDaaS/U2/playbook')
FINAL_DIR = WORKSPACE_ROOT / 'results/final'
FIG_DIR = FINAL_DIR / 'figures'
FIG_DIR.mkdir(parents=True, exist_ok=True)

# Load 54 conditions
conds = list(csv.DictReader(open(FINAL_DIR / 'condition_summary_54.csv', 'r', encoding='utf-8')))

# Engine & Profile styling
ENGINE_COLORS = {
    'mongodb': '#13aa52',    # MongoDB Green
    'postgresql': '#336791'  # PostgreSQL Blue
}

PROFILE_STYLES = {
    'PROFILE_A_BASELINE': {'ls': '-', 'marker': 'o', 'label': 'Baseline (4 CPU, 4GB)'},
    'PROFILE_B_MEDIUM':   {'ls': '--', 'marker': 's', 'label': 'Medium (2 CPU, 2GB)'},
    'PROFILE_C_SEVERE':   {'ls': ':', 'marker': '^', 'label': 'Severe (1 CPU, 1GB)'}
}

# ==============================================================================
# Figure 1: P99 Latency vs Concurrency
# ==============================================================================
fig, axes = plt.subplots(2, 2, figsize=(11, 8.5), sharex=True, sharey=True)
fig.suptitle('Figure 1: P99 Latency vs Concurrency (Median & Range across repetitions)', fontsize=13, y=0.98)

query_groups = [
    ('Q1', 'Q1: Unwind, Filter & Group (CPU Bound)', axes[0, 0]),
    ('Q2_allowDiskUse_false', 'MongoDB Q2: allowDiskUse=False (Top-K)', axes[0, 1]),
    ('Q2_work_mem_4MB', 'PostgreSQL Q2: work_mem=4MB (Top-K)', axes[1, 0]),
    ('Q2_disk_enabled', 'Q2 Memory Tolerant (Mongo true / PG 64MB)', axes[1, 1]),
]

concurrencies = [1, 10, 50]

for q_id, title, ax in query_groups:
    ax.set_title(title, fontsize=10, pad=8)
    ax.set_yscale('log')
    ax.set_xticks([1, 10, 50])
    ax.set_ylabel('Latency P99 (ms) [Log Scale]')
    
    # Filter conditions
    for engine in ['mongodb', 'postgresql']:
        for profile in ['PROFILE_A_BASELINE', 'PROFILE_B_MEDIUM', 'PROFILE_C_SEVERE']:
            x_vals = []
            y_meds = []
            y_mins = []
            y_maxs = []
            labels = []
            
            for c in concurrencies:
                # Find matching row
                match = None
                for r in conds:
                    if r['engine'] == engine and r['profile'] == profile and int(r['concurrency']) == c:
                        if q_id == 'Q2_disk_enabled':
                            if engine == 'mongodb' and r['query_id'] == 'Q2_allowDiskUse_true':
                                match = r
                            elif engine == 'postgresql' and r['query_id'] == 'Q2_work_mem_64MB':
                                match = r
                        elif r['query_id'] == q_id:
                            match = r
                
                if match:
                    if match['n_valid_perf'] != '0' and match['latency_p99_ms_median'] != 'ND':
                        med = float(match['latency_p99_ms_median'])
                        v_min = float(match['latency_p99_ms_min'])
                        v_max = float(match['latency_p99_ms_max'])
                        x_vals.append(c)
                        y_meds.append(med)
                        y_mins.append(med - v_min)
                        y_maxs.append(v_max - med)
                        labels.append(f"n={match['n_valid_perf']}")
                    else:
                        # Stability failure point
                        ax.scatter([c], [120000], color='red', marker='x', s=60, zorder=5)
                        ax.annotate('Fail (n=0)', (c, 120000), textcoords="offset points", xytext=(0, 6),
                                    ha='center', fontsize=7, color='red', weight='bold')

            if x_vals:
                p_style = PROFILE_STYLES[profile]
                yerr = [y_mins, y_maxs] if any(y_mins) or any(y_maxs) else None
                lbl = f"{engine.title()} {profile.split('_')[1]}"
                ax.errorbar(x_vals, y_meds, yerr=yerr, fmt=p_style['marker'],
                            color=ENGINE_COLORS[engine], linestyle=p_style['ls'],
                            linewidth=1.5, markersize=6, capsize=4, label=lbl, alpha=0.9)
                for x, y, lab in zip(x_vals, y_meds, labels):
                    ax.annotate(lab, (x, y), textcoords="offset points", xytext=(0, -12),
                                ha='center', fontsize=7, color=ENGINE_COLORS[engine], alpha=0.8)

for ax in axes[1, :]:
    ax.set_xlabel('Concurrency (clients)')

# Unified legend in first subplot
axes[0, 0].legend(loc='upper left', fontsize=7.5, framealpha=0.9)
plt.tight_layout(rect=[0, 0.03, 1, 0.95])
fig1_path = FIG_DIR / 'fig1_p99_latency_vs_concurrency.png'
plt.savefig(fig1_path)
plt.close()
print(f"Saved {fig1_path}")

# ==============================================================================
# Figure 2: Throughput (QPS) vs Concurrency
# ==============================================================================
fig, axes = plt.subplots(1, 2, figsize=(11, 5), sharey=True)
fig.suptitle('Figure 2: Throughput (QPS) vs Concurrency (Valid Repetitions vs Aborted Runs)', fontsize=13, y=0.98)

for idx, (engine, title) in enumerate([('mongodb', 'MongoDB (WiredTiger)'), ('postgresql', 'PostgreSQL (JSONB)')]):
    ax = axes[idx]
    ax.set_title(title, fontsize=11)
    ax.set_xlabel('Concurrency (clients)')
    ax.set_xticks([1, 10, 50])
    ax.set_ylabel('Throughput (QPS)')
    
    for profile in ['PROFILE_A_BASELINE', 'PROFILE_B_MEDIUM', 'PROFILE_C_SEVERE']:
        p_style = PROFILE_STYLES[profile]
        
        # Plot Q1
        x_q1, y_q1 = [], []
        for c in concurrencies:
            match = next((r for r in conds if r['engine'] == engine and r['profile'] == profile and r['query_id'] == 'Q1' and int(r['concurrency']) == c), None)
            if match and match['throughput_qps_median'] != 'ND':
                x_q1.append(c)
                y_q1.append(float(match['throughput_qps_median']))
            elif match and match['n_valid_perf'] == '0':
                ax.scatter([c], [0.0], color='crimson', marker='X', s=70, zorder=5)
                ax.annotate('Timeout/Crash', (c, 0.05), fontsize=7, color='crimson', ha='center')
                
        if x_q1:
            ax.plot(x_q1, y_q1, marker=p_style['marker'], linestyle=p_style['ls'],
                    color='#d95f02' if profile == 'PROFILE_C_SEVERE' else ('#7570b3' if profile == 'PROFILE_B_MEDIUM' else '#1b9e77'),
                    linewidth=1.8, label=f"Q1 {profile.split('_')[1]}")

        # Plot Q2 (default memory variant)
        q2_id = 'Q2_allowDiskUse_false' if engine == 'mongodb' else 'Q2_work_mem_4MB'
        x_q2, y_q2 = [], []
        for c in concurrencies:
            match = next((r for r in conds if r['engine'] == engine and r['profile'] == profile and r['query_id'] == q2_id and int(r['concurrency']) == c), None)
            if match and match['throughput_qps_median'] != 'ND':
                x_q2.append(c)
                y_q2.append(float(match['throughput_qps_median']))
            elif match and match['n_valid_perf'] == '0':
                ax.scatter([c], [0.0], color='crimson', marker='X', s=70, zorder=5)
                
        if x_q2:
            ax.plot(x_q2, y_q2, marker=p_style['marker'], linestyle=':',
                    color='#d95f02' if profile == 'PROFILE_C_SEVERE' else ('#7570b3' if profile == 'PROFILE_B_MEDIUM' else '#1b9e77'),
                    linewidth=1.2, alpha=0.7, label=f"Q2 {profile.split('_')[1]}")

    ax.legend(loc='upper right', fontsize=8, framealpha=0.9)

plt.tight_layout(rect=[0, 0.03, 1, 0.95])
fig2_path = FIG_DIR / 'fig2_throughput_vs_concurrency.png'
plt.savefig(fig2_path)
plt.close()
print(f"Saved {fig2_path}")

# ==============================================================================
# Figure 3: Peak RSS Memory vs Concurrency & Docker Cgroup Limits
# ==============================================================================
fig, axes = plt.subplots(1, 2, figsize=(11, 5), sharey=True)
fig.suptitle('Figure 3: Peak RSS Memory Footprint (MB) vs Docker Memory Limits', fontsize=13, y=0.98)

mem_limits = {
    'PROFILE_A_BASELINE': 4096,
    'PROFILE_B_MEDIUM': 2048,
    'PROFILE_C_SEVERE': 1024
}

for idx, (engine, title) in enumerate([('mongodb', 'MongoDB (WiredTiger)'), ('postgresql', 'PostgreSQL (JSONB)')]):
    ax = axes[idx]
    ax.set_title(title, fontsize=11)
    ax.set_xlabel('Concurrency (clients)')
    ax.set_xticks([1, 10, 50])
    ax.set_ylabel('Peak Container RSS (MB)')
    
    # Plot memory limits as horizontal guide lines
    ax.axhline(4096, color='gray', linestyle='--', alpha=0.5, label='Limit A (4096 MB)')
    ax.axhline(2048, color='orange', linestyle='--', alpha=0.5, label='Limit B (2048 MB)')
    ax.axhline(1024, color='red', linestyle='--', alpha=0.5, label='Limit C (1024 MB)')
    
    for profile in ['PROFILE_A_BASELINE', 'PROFILE_B_MEDIUM', 'PROFILE_C_SEVERE']:
        p_style = PROFILE_STYLES[profile]
        x_vals, y_vals = [], []
        
        for c in concurrencies:
            match = next((r for r in conds if r['engine'] == engine and r['profile'] == profile and r['query_id'] == 'Q1' and int(r['concurrency']) == c), None)
            if match and match['max_rss_mb_median'] != 'ND':
                x_vals.append(c)
                y_vals.append(float(match['max_rss_mb_median']))
            elif match and match['n_valid_perf'] == '0':
                # Warmup crash point
                ax.scatter([c], [float(match['max_rss_mb_median']) if match['max_rss_mb_median'] != 'ND' else 5.1],
                           color='red', marker='v', s=60, zorder=5)
                ax.annotate('SIGKILL/OOM', (c, 50), fontsize=7, color='red', ha='center')
                
        if x_vals:
            ax.plot(x_vals, y_vals, marker=p_style['marker'], linestyle=p_style['ls'],
                    color=ENGINE_COLORS[engine], linewidth=1.8,
                    label=f"Q1 {profile.split('_')[1]}")

    ax.legend(loc='upper left', fontsize=7.5, framealpha=0.9)

plt.tight_layout(rect=[0, 0.03, 1, 0.95])
fig3_path = FIG_DIR / 'fig3_rss_memory_vs_concurrency.png'
plt.savefig(fig3_path)
plt.close()
print(f"Saved {fig3_path}")

# ==============================================================================
# Figure 4: CFS CPU Throttling vs QPS
# ==============================================================================
fig, axes = plt.subplots(1, 2, figsize=(11, 5), sharey=True)
fig.suptitle('Figure 4: CFS Throttling vs Throughput (Q1 medians)', fontsize=13, y=0.98)

for idx, (engine, title) in enumerate([('mongodb', 'MongoDB (WiredTiger)'), ('postgresql', 'PostgreSQL (JSONB)')]):
    ax = axes[idx]
    ax.set_title(title, fontsize=11)
    ax.set_xlabel('Throughput QPS (median)')
    ax.set_ylabel('CFS Throttled Seconds (300s window)')
    
    for profile in ['PROFILE_A_BASELINE', 'PROFILE_B_MEDIUM', 'PROFILE_C_SEVERE']:
        p_style = PROFILE_STYLES[profile]
        x_vals, y_vals, labels = [], [], []
        
        for c in concurrencies:
            match = next((r for r in conds if r['engine'] == engine and r['profile'] == profile and r['query_id'] == 'Q1' and int(r['concurrency']) == c), None)
            if (match and match['n_valid_perf'] != '0'
                    and match['throughput_qps_median'] != 'ND'
                    and match['cfs_throttled_sec_median'] != 'ND'):
                x_vals.append(float(match['throughput_qps_median']))
                y_vals.append(float(match['cfs_throttled_sec_median']))
                labels.append(f"c={c}, n={match['n_valid_perf']}")
                
        if x_vals:
            ax.scatter(x_vals, y_vals, marker=p_style['marker'],
                       color=ENGINE_COLORS[engine], s=55, label=f"{profile.split('_')[1]}")
            for x, y, label in zip(x_vals, y_vals, labels):
                ax.annotate(label, (x, y), textcoords='offset points', xytext=(4, 4), fontsize=7)
    ax.set_ylabel('CFS throttled seconds (median)')

    handles, legend_labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc='upper left', fontsize=8, framealpha=0.9)
    else:
        ax.text(0.5, 0.5, 'No Q1 rows eligible after adjudication', transform=ax.transAxes, ha='center', va='center', fontsize=8)

plt.tight_layout(rect=[0, 0.03, 1, 0.95])
fig4_path = FIG_DIR / 'fig4_cfs_throttling_vs_throughput.png'
plt.savefig(fig4_path)
plt.close()
print(f"Saved {fig4_path}")

# ==============================================================================
# Figure 5: Comprehensive State & Outcome Matrix (54 Conditions)
# ==============================================================================
fig, ax = plt.subplots(figsize=(11, 7))
ax.set_title('Figure 5: Derived Performance Eligibility (54 Conditions × 3 Attempts)', fontsize=12, pad=12)

# Grid layout:
# Y: 18 Query/Engine Combinations
# X: 3 Profiles × 3 Concurrencies = 9 columns
engine_queries = [
    ('mongodb', 'Q1'),
    ('mongodb', 'Q2_allowDiskUse_false'),
    ('mongodb', 'Q2_allowDiskUse_true'),
    ('postgresql', 'Q1'),
    ('postgresql', 'Q2_work_mem_4MB'),
    ('postgresql', 'Q2_work_mem_64MB'),
]

x_labels = [
    'A (c=1)', 'A (c=10)', 'A (c=50)',
    'B (c=1)', 'B (c=10)', 'B (c=50)',
    'C (c=1)', 'C (c=10)', 'C (c=50)'
]

matrix_data = np.zeros((len(engine_queries), 9))
cell_texts = []

# Colors show derived performance eligibility only, not historical run status.
for y_idx, (engine, query_id) in enumerate(engine_queries):
    row_texts = []
    col_idx = 0
    for profile in ['PROFILE_A_BASELINE', 'PROFILE_B_MEDIUM', 'PROFILE_C_SEVERE']:
        for c in [1, 10, 50]:
            match = next((r for r in conds if r['engine'] == engine and r['profile'] == profile and r['query_id'] == query_id and int(r['concurrency']) == c), None)
            val, txt = 0, ''
            if match:
                n_val = int(match['n_valid_perf'])
                if n_val == 3:
                    val, txt = 1, '3/3\neligible'
                elif n_val > 0:
                    val, txt = 2, f'{n_val}/3\npartial'
                else:
                    val, txt = 3, '0/3\nexcluded'
            matrix_data[y_idx, col_idx] = val
            row_texts.append(txt)
            col_idx += 1
    cell_texts.append(row_texts)

from matplotlib.colors import ListedColormap
cmap = ListedColormap(['#ffffff', '#2ca02c', '#ffbf00', '#d62728'])
cax = ax.imshow(matrix_data, cmap=cmap, vmin=0, vmax=3, aspect='auto')
for y in range(len(engine_queries)):
    for x in range(9):
        ax.text(x, y, cell_texts[y][x], ha='center', va='center', fontsize=7.5,
                color='white' if matrix_data[y, x] in [1, 3] else 'black', weight='bold')

ax.set_xticks(range(9))
ax.set_xticklabels(x_labels, rotation=30, ha='right', fontsize=9)
y_labels = [f"{e.title()} {q}" for e, q in engine_queries]
ax.set_yticks(range(len(engine_queries)))
ax.set_yticklabels(y_labels, fontsize=9)
legend_patches = [
    mpatches.Patch(color='#2ca02c', label='3/3 elegibles (no revalidados)'),
    mpatches.Patch(color='#ffbf00', label='1–2/3 elegibles (parcial)'),
    mpatches.Patch(color='#d62728', label='0/3 elegibles; excluidos/no concluyentes'),
]
ax.legend(handles=legend_patches, bbox_to_anchor=(0.5, -0.2), loc='upper center', ncol=3, fontsize=8)

plt.tight_layout(rect=[0, 0.05, 1, 0.98])
fig5_path = FIG_DIR / 'fig5_state_matrix_heatmap.png'
plt.savefig(fig5_path)
plt.close()
print(f"Saved {fig5_path}")
print("All 5 figures generated successfully.")

