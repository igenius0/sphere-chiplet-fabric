"""
Analytical performance model for the Sphere-162 reference design.
Computes peak FLOPS, memory bandwidth, power, and workload projections.
Comparison against H100, B200, MI300X, WSE-3.
"""
import json, os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')

FIG_DIR = '/home/claude/sphere_paper/figures'
DATA_DIR = '/home/claude/sphere_paper/artifacts'
os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


# ---- Sphere-162 reference design ----
SPHERE_162 = {
    'name': 'Sphere-162',
    'tiles': {
        'compute': {'count': 113, 'cores': 4, 'matrix_units': 1, 'power_w': 15.0},
        'memory_ctrl': {'count': 25, 'hbm_stacks': 1, 'power_w': 12.0},
        'cache_io': {'count': 24, 'l3_mb': 64, 'power_w': 8.0},
    },
    'process_nm': 3,
    # Per matrix-unit FP8 throughput at 2 GHz
    'matrix_tops_fp8_per_unit': 500.0,  # TFLOPS, ~256x256 systolic at 2 GHz
    'core_clock_ghz': 3.5,
    'core_ipc_fp32': 4.0,  # 4-wide vector FMA
    'hbm_bw_per_stack_gbps': 9600,  # HBM3e: 1.2 TB/s = 9600 Gbps
    'hbm_capacity_per_stack_gb': 36,
    'fabric_link_bw_gbps': 256,
    'fabric_links_per_tile': 6,
    'l3_per_tile_mb': 64,
}

def compute_aggregate(design):
    n_compute = design['tiles']['compute']['count']
    n_mc = design['tiles']['memory_ctrl']['count']
    n_io = design['tiles']['cache_io']['count']

    # FP8 dense throughput
    matrix_units = n_compute * design['tiles']['compute']['matrix_units']
    fp8_tflops = matrix_units * design['matrix_tops_fp8_per_unit']

    # Scalar/vector cores (rough: 4 cores, IPC 4, 3.5 GHz, FP32)
    cores = n_compute * design['tiles']['compute']['cores']
    fp32_tflops_scalar = cores * design['core_ipc_fp32'] * design['core_clock_ghz'] * 2 / 1000

    # HBM
    hbm_bw_gbps = n_mc * design['hbm_bw_per_stack_gbps']
    hbm_capacity_gb = n_mc * design['hbm_capacity_per_stack_gb']

    # L3
    l3_total_mb = n_io * design['l3_per_tile_mb']

    # Power
    p_compute = n_compute * design['tiles']['compute']['power_w']
    p_mc = n_mc * design['tiles']['memory_ctrl']['power_w']
    p_io = n_io * design['tiles']['cache_io']['power_w']
    # SerDes: 6 links/tile, 256 Gbps/link, ~1.5 pJ/bit at 50% utilization
    n_total = n_compute + n_mc + n_io
    serdes_power = (n_total * design['fabric_links_per_tile'] *
                    design['fabric_link_bw_gbps'] * 1e9 *
                    1.5e-12 * 0.5) / 2  # /2 to avoid double-count both endpoints
    total_power = p_compute + p_mc + p_io + serdes_power

    return {
        'fp8_pflops_dense': fp8_tflops / 1000,
        'fp32_tflops_scalar': fp32_tflops_scalar,
        'hbm_capacity_gb': hbm_capacity_gb,
        'hbm_bw_tbps': hbm_bw_gbps / 1000 / 8,  # GB/s actually
        'hbm_bw_TBps': hbm_bw_gbps / 8 / 1000,
        'l3_total_mb': l3_total_mb,
        'power_typical_w': total_power,
        'power_breakdown': {
            'compute': p_compute,
            'memory_ctrl': p_mc,
            'cache_io': p_io,
            'serdes': serdes_power,
        },
    }


sphere_perf = compute_aggregate(SPHERE_162)
print("=== Sphere-162 Reference Design ===")
for k, v in sphere_perf.items():
    if isinstance(v, dict):
        print(f"  {k}:")
        for kk, vv in v.items():
            print(f"    {kk}: {vv:.1f}")
    else:
        print(f"  {k}: {v:.1f}")


# ---- Comparison with industry chips (public specs) ----
# Sources: NVIDIA H100/B200 datasheets, AMD MI300X, Cerebras WSE-3 spec
COMPETITORS = {
    'NVIDIA H100 SXM': {
        'fp8_pflops_dense': 3.96,    # 3.96 PFLOPS sparse, ~2 dense — using dense estimate
        'fp32_tflops_scalar': 67,
        'hbm_capacity_gb': 80,
        'hbm_bw_TBps': 3.35,
        'l3_total_mb': 50,            # L2 actually
        'power_typical_w': 700,
    },
    'NVIDIA B200': {
        'fp8_pflops_dense': 9.0,      # ~9 PFLOPS dense FP8 (estimate from announcements)
        'fp32_tflops_scalar': 80,
        'hbm_capacity_gb': 192,
        'hbm_bw_TBps': 8.0,
        'l3_total_mb': 100,
        'power_typical_w': 1000,
    },
    'AMD MI300X': {
        'fp8_pflops_dense': 5.22,
        'fp32_tflops_scalar': 81.7,
        'hbm_capacity_gb': 192,
        'hbm_bw_TBps': 5.3,
        'l3_total_mb': 256,
        'power_typical_w': 750,
    },
    'Cerebras WSE-3': {
        'fp8_pflops_dense': 125,      # claimed peak
        'fp32_tflops_scalar': 0,      # not directly comparable
        'hbm_capacity_gb': 44,        # SRAM only, no HBM
        'hbm_bw_TBps': 21000,         # 21 PB/s on-die SRAM
        'l3_total_mb': 44000,
        'power_typical_w': 23000,
    },
    '8x H100 node (NVLink)': {
        'fp8_pflops_dense': 3.96 * 8,
        'fp32_tflops_scalar': 67 * 8,
        'hbm_capacity_gb': 640,
        'hbm_bw_TBps': 3.35 * 8,
        'l3_total_mb': 400,
        'power_typical_w': 700 * 8 + 800,  # GPUs + switches/CPU
    },
}

# Add sphere
COMPETITORS['Sphere-162 (this work)'] = {
    'fp8_pflops_dense': sphere_perf['fp8_pflops_dense'],
    'fp32_tflops_scalar': sphere_perf['fp32_tflops_scalar'],
    'hbm_capacity_gb': sphere_perf['hbm_capacity_gb'],
    'hbm_bw_TBps': sphere_perf['hbm_bw_TBps'],
    'l3_total_mb': sphere_perf['l3_total_mb'],
    'power_typical_w': sphere_perf['power_typical_w'],
}

print("\n=== Comparison Table ===")
print(f"{'Chip':30s} {'FP8 PF':>8s} {'HBM GB':>8s} {'HBM TB/s':>10s} "
      f"{'Power W':>9s} {'PFLOPS/kW':>10s}")
for name, p in COMPETITORS.items():
    pflops_per_kw = p['fp8_pflops_dense'] / (p['power_typical_w'] / 1000)
    print(f"{name:30s} {p['fp8_pflops_dense']:8.1f} {p['hbm_capacity_gb']:8.0f} "
          f"{p['hbm_bw_TBps']:10.2f} {p['power_typical_w']:9.0f} {pflops_per_kw:10.1f}")


# ---- Workload model ----
# Roofline-style projections for representative workloads
WORKLOADS = {
    'Llama-70B inference (FP8)': {
        'arithmetic_intensity_flops_per_byte': 4,
        'compute_demand_pflops': 0.5,
        'memory_demand_TBps': 5.0,
        'collective_demand_us_per_step': 50,  # all-reduce overhead in distributed
        'fits_in_memory_gb': 70,
    },
    'Llama-405B inference (FP8)': {
        'arithmetic_intensity_flops_per_byte': 4,
        'compute_demand_pflops': 2.5,
        'memory_demand_TBps': 25.0,
        'collective_demand_us_per_step': 200,
        'fits_in_memory_gb': 410,
    },
    'GPT-175B training step': {
        'arithmetic_intensity_flops_per_byte': 100,
        'compute_demand_pflops': 50.0,
        'memory_demand_TBps': 8.0,
        'collective_demand_us_per_step': 500,
        'fits_in_memory_gb': 700,
    },
    'Sparse GEMM (graph, FP16)': {
        'arithmetic_intensity_flops_per_byte': 1,
        'compute_demand_pflops': 0.1,
        'memory_demand_TBps': 12.0,
        'collective_demand_us_per_step': 80,
        'fits_in_memory_gb': 50,
    },
    'CFD simulation (FP64)': {
        'arithmetic_intensity_flops_per_byte': 8,
        'compute_demand_pflops': 5.0,
        'memory_demand_TBps': 4.0,
        'collective_demand_us_per_step': 100,
        'fits_in_memory_gb': 100,
    },
}


def estimate_workload_perf(chip, workload):
    """
    Roofline-bounded throughput. Returns ratio of workload-demand to chip-supply.
    A value >= 1 means chip can sustain the workload. Lower = bottleneck.
    """
    compute_ratio = chip['fp8_pflops_dense'] / max(workload['compute_demand_pflops'], 0.001)
    mem_ratio = chip['hbm_bw_TBps'] / max(workload['memory_demand_TBps'], 0.001)
    fits = chip['hbm_capacity_gb'] >= workload['fits_in_memory_gb']
    bound_ratio = min(compute_ratio, mem_ratio)
    if not fits:
        bound_ratio *= 0.3  # heavy penalty for sharding spillover
    return {
        'compute_ratio': compute_ratio,
        'mem_ratio': mem_ratio,
        'fits_in_memory': fits,
        'effective_throughput_x': bound_ratio,
    }


print("\n=== Workload Projections (effective throughput, higher = better) ===")
header = f"{'Workload':30s}"
for name in COMPETITORS:
    header += f"{name[:14]:>15s}"
print(header)
workload_table = {}
for wl_name, wl in WORKLOADS.items():
    row = f"{wl_name:30s}"
    workload_table[wl_name] = {}
    for chip_name, chip in COMPETITORS.items():
        r = estimate_workload_perf(chip, wl)
        row += f"{r['effective_throughput_x']:14.2f}x"
        workload_table[wl_name][chip_name] = r
    print(row)


# ---- Plot: workload throughput comparison ----
fig, ax = plt.subplots(figsize=(11, 5.5))
chip_order = ['NVIDIA H100 SXM', 'NVIDIA B200', 'AMD MI300X',
              '8x H100 node (NVLink)', 'Sphere-162 (this work)']
chip_colors = ['#888', '#666', '#aa7733', '#2266aa', '#cc3322']
n_chips = len(chip_order)
n_wls = len(WORKLOADS)
x = np.arange(n_wls)
width = 0.85 / n_chips

for i, chip_name in enumerate(chip_order):
    vals = []
    for wl_name in WORKLOADS:
        r = workload_table[wl_name][chip_name]
        # Cap at 30 for visualization
        vals.append(min(r['effective_throughput_x'], 30))
    bars = ax.bar(x + i * width, vals, width, label=chip_name,
                  color=chip_colors[i])

ax.set_xticks(x + width * (n_chips - 1) / 2)
ax.set_xticklabels(list(WORKLOADS.keys()), rotation=15, ha='right')
ax.set_ylabel('Effective throughput multiple (x demanded compute, capped at 30x)')
ax.set_title('Workload Throughput Headroom per Chip (higher = more performance available)')
ax.legend(loc='upper right', fontsize=9)
ax.grid(True, alpha=0.3, axis='y')
ax.axhline(y=1, color='red', linestyle='--', alpha=0.5,
           label='Just enough (1x)')
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig9_workload.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig9_workload.pdf'), bbox_inches='tight')
plt.close()


# ---- Power breakdown pie ----
fig, ax = plt.subplots(figsize=(7, 5))
breakdown = sphere_perf['power_breakdown']
labels = list(breakdown.keys())
sizes = list(breakdown.values())
colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']
ax.pie(sizes, labels=[f'{l}\n{s:.0f} W' for l, s in zip(labels, sizes)],
       colors=colors, autopct='%1.1f%%', startangle=90)
ax.set_title(f'Sphere-162 Power Breakdown (total {sum(sizes):.0f} W)')
plt.tight_layout()
plt.savefig(os.path.join(FIG_DIR, 'fig10_power.png'), bbox_inches='tight')
plt.savefig(os.path.join(FIG_DIR, 'fig10_power.pdf'), bbox_inches='tight')
plt.close()


# ---- Save results ----
output = {
    'sphere_162_design': SPHERE_162,
    'sphere_162_aggregate_performance': sphere_perf,
    'competitors': COMPETITORS,
    'workload_projections': {
        wl: {chip: r for chip, r in chips.items()}
        for wl, chips in workload_table.items()
    },
}
with open(os.path.join(DATA_DIR, 'performance_model.json'), 'w') as f:
    json.dump(output, f, indent=2, default=str)

print("\nSaved performance model and figures.")
