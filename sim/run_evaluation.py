"""
Full evaluation suite v2 - uses both ring and tree all-reduce.
"""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')

from topology import make_icosphere, make_torus, make_dragonfly, topology_stats
from fabric import (FabricSim, LatencyModel, uniform_random_traffic,
                    hotspot_traffic, ring_all_reduce_time,
                    tree_all_reduce_time, run_traffic)

FIG_DIR = '/home/claude/sphere_paper/figures'
DATA_DIR = '/home/claude/sphere_paper/artifacts'
os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

plt.rcParams['font.family'] = 'DejaVu Serif'
plt.rcParams['font.size'] = 11
plt.rcParams['figure.dpi'] = 150

COLORS = {
    'sphere': '#1f77b4',
    'sphere_chord': '#ff7f0e',
    'torus': '#2ca02c',
    'dragonfly': '#d62728',
}

print("Building topologies...")
SPHERE = make_icosphere(subdivisions=2, radius_mm=40.0)
SPHERE_CHORD = make_icosphere(subdivisions=2, radius_mm=40.0,
                              add_chords=True, n_chords=10)
TORUS = make_torus(13, 13, link_length_mm=4.0)
DRAGON = make_dragonfly(9, 6)

TOPOS = [
    ('Sphere (162)', SPHERE, COLORS['sphere'], '-'),
    ('Sphere+chords (162)', SPHERE_CHORD, COLORS['sphere_chord'], '--'),
    ('Torus 13x13 (169)', TORUS, COLORS['torus'], '-.'),
    ('Dragonfly 9x6 (54)', DRAGON, COLORS['dragonfly'], ':'),
]

# 1. Hop CDF
print("Exp 1: Hop CDF")
fig, ax = plt.subplots(figsize=(7, 4.5))
hop_stats_all = {}
for name, topo, color, ls in TOPOS:
    s = topology_stats(topo)
    hop_stats_all[name] = s
    hops = sorted(s['hop_dist'])
    cdf = np.arange(1, len(hops) + 1) / len(hops)
    ax.plot(hops, cdf, label=f"{name} (max {s['max_hops']})",
            color=color, linestyle=ls, linewidth=2)
ax.set_xlabel('Hop count'); ax.set_ylabel('CDF')
ax.set_title('Hop Count Distribution')
ax.grid(True, alpha=0.3); ax.legend(loc='lower right')
ax.set_xlim(0, 14)
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig1_hop_cdf.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig1_hop_cdf.pdf'), bbox_inches='tight')
plt.close()

# 2. Uncontended latency
print("Exp 2: Uncontended latency")
fig, ax = plt.subplots(figsize=(7, 4.5))
results_uncontended = {}
for name, topo, color, ls in TOPOS:
    pairs = uniform_random_traffic(topo['nodes'], 5000)
    res = run_traffic(topo, pairs)
    results_uncontended[name] = res
    lats = sorted(res['latencies_ns'])
    cdf = np.arange(1, len(lats) + 1) / len(lats)
    ax.plot(lats, cdf, label=f"{name}  (avg {res['avg_latency_ns']:.0f}, p99 {res['p99_latency_ns']:.0f})",
            color=color, linestyle=ls, linewidth=2)
ax.set_xlabel('End-to-end latency (ns)'); ax.set_ylabel('CDF')
ax.set_title('Packet Latency CDF - Uncontended Uniform Random')
ax.grid(True, alpha=0.3); ax.legend(loc='lower right')
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig2_latency_cdf.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig2_latency_cdf.pdf'), bbox_inches='tight')
plt.close()

# 3. Latency vs load
print("Exp 3: Latency vs load")
fig, ax = plt.subplots(figsize=(7, 4.5))
load_results = {}
inj_rates = [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0]
for name, topo, color, ls in TOPOS:
    avg_lats, p99_lats = [], []
    for rate in inj_rates:
        pairs = uniform_random_traffic(topo['nodes'], 2000)
        res = run_traffic(topo, pairs, injection_rate_pkts_per_ns=rate)
        if res:
            avg_lats.append(res['avg_latency_ns'])
            p99_lats.append(res['p99_latency_ns'])
        else:
            avg_lats.append(np.nan); p99_lats.append(np.nan)
    load_results[name] = {'rates': inj_rates, 'avg': avg_lats, 'p99': p99_lats}
    ax.plot(inj_rates, avg_lats, label=name, color=color, linestyle=ls,
            linewidth=2, marker='o', markersize=4)
ax.set_xlabel('Injection rate (pkts/ns aggregate)'); ax.set_ylabel('Avg latency (ns)')
ax.set_title('Latency vs Offered Load')
ax.grid(True, alpha=0.3); ax.legend(loc='upper left')
ax.set_xscale('log'); ax.set_yscale('log')
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig3_latency_load.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig3_latency_load.pdf'), bbox_inches='tight')
plt.close()

# 4. Tail ratio
print("Exp 4: Tail ratio")
fig, ax = plt.subplots(figsize=(7, 4.5))
tail_data, labels = [], []
for name, _, _, _ in TOPOS:
    res = results_uncontended[name]
    tail_data.append(res['p99_latency_ns'] / res['p50_latency_ns'])
    labels.append(name)
colors_list = [COLORS['sphere'], COLORS['sphere_chord'],
               COLORS['torus'], COLORS['dragonfly']]
bars = ax.bar(labels, tail_data, color=colors_list)
ax.set_ylabel('P99 / P50 ratio')
ax.set_title('Tail Latency Ratio (lower = more uniform)')
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, tail_data):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
            f'{val:.2f}', ha='center', fontsize=10)
plt.xticks(rotation=15, ha='right')
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig4_tail_ratio.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig4_tail_ratio.pdf'), bbox_inches='tight')
plt.close()

# 5. All-reduce
print("Exp 5: All-reduce")
ar_results = {}
for name, topo, _, _ in TOPOS:
    ring_t = ring_all_reduce_time(topo, message_flits=8)
    tree_t = tree_all_reduce_time(topo, message_flits=8)
    ar_results[name] = {
        'ring_us': ring_t / 1000,
        'tree_us': tree_t / 1000,
        'best_us': min(ring_t, tree_t) / 1000,
        'n_nodes': len(topo['nodes']),
    }
    print(f"  {name:30s} ring={ring_t/1000:.2f}us tree={tree_t/1000:.2f}us")

fig, ax = plt.subplots(figsize=(8, 4.5))
x = np.arange(len(TOPOS))
width = 0.35
ring_vals = [ar_results[name]['ring_us'] for name, _, _, _ in TOPOS]
tree_vals = [ar_results[name]['tree_us'] for name, _, _, _ in TOPOS]
b1 = ax.bar(x - width/2, ring_vals, width, label='Ring', color='steelblue')
b2 = ax.bar(x + width/2, tree_vals, width, label='Tree (BFS)', color='coral')
ax.set_xticks(x)
ax.set_xticklabels([name for name, _, _, _ in TOPOS], rotation=15, ha='right')
ax.set_ylabel('All-reduce time (us)')
ax.set_title('All-Reduce: Ring vs Tree (8-flit messages)')
ax.legend(); ax.grid(True, alpha=0.3, axis='y')
mx = max(max(ring_vals), max(tree_vals))
for bars in [b1, b2]:
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + mx*0.01,
                f'{bar.get_height():.1f}', ha='center', fontsize=8)
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig5_allreduce.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig5_allreduce.pdf'), bbox_inches='tight')
plt.close()

# 6. Bisection
print("Exp 6: Bisection")
def bisection_bandwidth(topo, link_bw_gbps=256.0):
    coords = topo['coords']
    nodes = topo['nodes']
    if topo['topology_type'] == 'icosphere':
        side = {n: (1 if coords[n][2] >= 0 else -1) for n in nodes}
    elif topo['topology_type'] == 'torus':
        max_x = max(c[0] for c in coords.values())
        side = {n: (1 if coords[n][0] >= max_x/2 else -1) for n in nodes}
    else:
        side = {n: (1 if (n // 6) < 4 else -1) for n in nodes}
    cut = 0; counted = set()
    for u, neighbors in topo['adj'].items():
        for v in neighbors:
            if (v, u) in counted: continue
            if side[u] != side[v]: cut += 1
            counted.add((u, v))
    return cut * link_bw_gbps

bisection_results = {}
fig, ax = plt.subplots(figsize=(7, 4.5))
for name, topo, _, _ in TOPOS:
    bisection_results[name] = bisection_bandwidth(topo)
labels = [name for name, _, _, _ in TOPOS]
vals = [bisection_results[k]/1000 for k in labels]
bars = ax.bar(labels, vals, color=colors_list)
ax.set_ylabel('Bisection bandwidth (Tbps)')
ax.set_title('Bisection Bandwidth (256 Gbps/link)')
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, vals):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(vals)*0.01,
            f'{val:.1f}', ha='center', fontsize=10)
plt.xticks(rotation=15, ha='right')
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig6_bisection.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig6_bisection.pdf'), bbox_inches='tight')
plt.close()

# 7. Hotspot
print("Exp 7: Hotspot")
fig, ax = plt.subplots(figsize=(7, 4.5))
hotspot_results = {}
for name, topo, color, ls in TOPOS:
    pairs = hotspot_traffic(topo['nodes'], 3000, hotspot_frac=0.05)
    res = run_traffic(topo, pairs, injection_rate_pkts_per_ns=0.5)
    hotspot_results[name] = res
    lats = sorted(res['latencies_ns'])
    cdf = np.arange(1, len(lats) + 1) / len(lats)
    ax.plot(lats, cdf, label=f"{name}  (p99 {res['p99_latency_ns']:.0f}ns)",
            color=color, linestyle=ls, linewidth=2)
ax.set_xlabel('End-to-end latency (ns)'); ax.set_ylabel('CDF')
ax.set_title('Hotspot Traffic (5% nodes get 70% of traffic)')
ax.grid(True, alpha=0.3); ax.legend(loc='lower right')
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig7_hotspot.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig7_hotspot.pdf'), bbox_inches='tight')
plt.close()

# 8. Energy
print("Exp 8: Energy")
def avg_path_energy_pj_per_bit(topo, link_pj=1.5, router_pj=0.5):
    s = topology_stats(topo)
    return s['avg_hops'] * (link_pj + router_pj) + router_pj
energy_data = {name: avg_path_energy_pj_per_bit(topo) for name, topo, _, _ in TOPOS}
fig, ax = plt.subplots(figsize=(7, 4.5))
labels = list(energy_data.keys())
vals = list(energy_data.values())
bars = ax.bar(labels, vals, color=colors_list)
ax.set_ylabel('pJ/bit (avg path)')
ax.set_title('Estimated Average Path Energy per Bit')
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, vals):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(vals)*0.01,
            f'{val:.1f}', ha='center', fontsize=10)
plt.xticks(rotation=15, ha='right')
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig8_energy.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig8_energy.pdf'), bbox_inches='tight')
plt.close()

# Save
all_results = {
    'topology_stats': {k: {kk: vv for kk, vv in v.items() if kk != 'hop_dist'}
                       for k, v in hop_stats_all.items()},
    'uncontended_latency': {k: {kk: vv for kk, vv in v.items()
                                if kk not in ('latencies_ns', 'hops')}
                            for k, v in results_uncontended.items()},
    'load_sweep': load_results,
    'all_reduce': ar_results,
    'bisection_gbps': bisection_results,
    'hotspot': {k: {kk: vv for kk, vv in v.items()
                    if kk not in ('latencies_ns', 'hops')}
                for k, v in hotspot_results.items()},
    'energy_pj_per_bit': energy_data,
}
with open(os.path.join(DATA_DIR, 'results.json'), 'w') as f:
    json.dump(all_results, f, indent=2, default=str)

print("\n=== SUMMARY ===")
print(f"{'Topology':30s} {'N':>4s} {'avgH':>6s} {'p99H':>6s} "
      f"{'avgL':>8s} {'p99L':>8s} {'tree(us)':>10s} {'bisec(T)':>10s}")
for name, topo, _, _ in TOPOS:
    s = hop_stats_all[name]; r = results_uncontended[name]
    ar = ar_results[name]['tree_us']; bi = bisection_results[name] / 1000
    print(f"{name:30s} {s['nodes']:4d} {s['avg_hops']:6.2f} {s['p99_hops']:6d} "
          f"{r['avg_latency_ns']:7.0f}n {r['p99_latency_ns']:7.0f}n {ar:9.1f}u {bi:9.1f}T")
print("\nDone.")
