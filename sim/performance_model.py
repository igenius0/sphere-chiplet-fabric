"""Assumption-driven capacity bounds, not competitor or silicon benchmarks.

The publication's historical model mixed precision and memory technologies.
This replacement reports explicit input assumptions and endpoint bottlenecks.
The archived paper/figures are unchanged; see docs/RESEARCH_REVIEW.md.
"""
import argparse
import json
from pathlib import Path

try:
    from .validation import finite, integer
except ImportError:
    from validation import finite, integer


SPHERE_162 = {
    'name': 'Sphere-162 hypothetical reference',
    'tiles': {
        'compute': {'count': 113, 'cores': 4, 'matrix_units': 1, 'power_w': 15.0},
        'memory_ctrl': {'count': 25, 'power_w': 12.0},
        'cache_io': {'count': 24, 'power_w': 8.0},
    },
    'matrix_tops_fp8_per_unit': 500.0,  # Assumed dense TFLOPS, not measured.
    'hbm_bw_per_stack_gbps': 9600.0,  # 1.2 TB/s local stack assumption.
    'hbm_capacity_per_stack_gb': 36.0,
    'fabric_link_bw_gbps': 256.0,  # Per direction.
    'fabric_links_per_tile': 6,  # Optimistic uniform port budget; 12 sphere nodes have degree 5.
    'link_energy_pj_per_bit': 1.5,  # Both endpoints per transmitted bit, assumed.
    'fabric_utilization': 0.5,
}


def compute_aggregate(design):
    """Return decimal-unit capacity ceilings for one stack per memory tile.

    Remote HBM accesses must cross memory-controller egress and compute ingress.
    This endpoint ceiling excludes cuts, queueing, protocol and controller loss.
    Component power excludes HBM stack and system power, so it cannot establish
    full-system energy efficiency. Link energy is charged to each directed bit.
    """
    for kind in ('compute', 'memory_ctrl', 'cache_io'):
        integer(design['tiles'][kind]['count'], kind + ' count')
        finite(design['tiles'][kind]['power_w'], kind + ' power_w')
    for key in ('matrix_tops_fp8_per_unit', 'hbm_bw_per_stack_gbps',
                'hbm_capacity_per_stack_gb', 'fabric_link_bw_gbps'):
        finite(design[key], key, strict=True)
    integer(design['tiles']['compute']['matrix_units'], 'matrix_units', 1)
    integer(design['fabric_links_per_tile'], 'fabric_links_per_tile', 1)
    finite(design['link_energy_pj_per_bit'], 'link_energy_pj_per_bit')
    finite(design['fabric_utilization'], 'fabric_utilization')
    if design['fabric_utilization'] > 1:
        raise ValueError('fabric_utilization must be <= 1')
    counts = {kind: tile['count'] for kind, tile in design['tiles'].items()}
    n_compute, n_memory = counts['compute'], counts['memory_ctrl']
    port_TBps = design['fabric_link_bw_gbps'] / 8 / 1000
    memory_egress = n_memory * design['fabric_links_per_tile'] * port_TBps
    compute_ingress = n_compute * design['fabric_links_per_tile'] * port_TBps
    local_hbm = n_memory * design['hbm_bw_per_stack_gbps'] / 8 / 1000
    breakdown = {kind: tile['count'] * tile['power_w']
                 for kind, tile in design['tiles'].items()}
    breakdown['fabric_dynamic'] = (
        sum(counts.values()) * design['fabric_links_per_tile']
        * design['fabric_link_bw_gbps'] * 1e9
        * design['link_energy_pj_per_bit'] * 1e-12
        * design['fabric_utilization'])
    result = {
        'fp8_pflops_dense': n_compute * design['tiles']['compute']['matrix_units']
                            * design['matrix_tops_fp8_per_unit'] / 1000,
        'hbm_capacity_gb': n_memory * design['hbm_capacity_per_stack_gb'],
        'hbm_bw_TBps': local_hbm,
        'remote_hbm_fabric_bound_TBps': memory_egress,
        'compute_fabric_ingress_bound_TBps': compute_ingress,
        'effective_remote_hbm_bw_TBps': min(local_hbm, memory_egress, compute_ingress),
        'power_component_subtotal_w': sum(breakdown.values()),
        'power_breakdown': breakdown,
        'full_system_power_w': None,
        'missing_power_terms': ['HBM stacks', 'host and external IO',
                                'power conversion', 'cooling', 'static fabric power'],
        'evidence_level': 'unvalidated analytical assumptions; capacity upper bounds',
    }
    for key, value in result.items():
        if isinstance(value, (float, int)):
            finite(value, key)
    return result


def estimate_roofline(chip, workload):
    """Precision-matched Roofline ceiling in TFLOPS using decimal TB/s.

    TB/s * FLOP/byte = TFLOPS. This does not predict runtime, tokens/s, memory
    fit, utilization, collectives or overlap. Unsupported precision is an error.
    """
    precision = workload.get('precision')
    peaks = chip.get('compute_tflops_by_precision', {})
    if not isinstance(precision, str) or precision not in peaks:
        raise ValueError('workload precision requires a matching compute peak')
    peak = finite(peaks[precision], 'compute peak TFLOPS', strict=True)
    bandwidth = finite(chip['memory_bandwidth_TBps'], 'memory bandwidth TB/s', strict=True)
    intensity = finite(workload['arithmetic_intensity_flops_per_byte'],
                       'arithmetic intensity FLOP/byte', strict=True)
    memory_bound = bandwidth * intensity
    finite(memory_bound, 'memory-bound TFLOPS')
    return {
        'precision': precision,
        'compute_bound_tflops': peak,
        'memory_bound_tflops': memory_bound,
        'attainable_tflops_upper_bound': min(peak, memory_bound),
        'bottleneck': 'memory' if memory_bound < peak else 'compute',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional JSON report path')
    parser.add_argument('--arithmetic-intensity', type=float, default=4.0,
                        help='Illustrative FP8 FLOP/byte, not a measured workload')
    args = parser.parse_args(argv)
    try:
        aggregate = compute_aggregate(SPHERE_162)
        report = {
            'schema_version': 2,
            'design_assumptions': SPHERE_162,
            'aggregate_bounds': aggregate,
            'illustrative_remote_memory_roofline': estimate_roofline(
                {'compute_tflops_by_precision': {'fp8': aggregate['fp8_pflops_dense'] * 1000},
                 'memory_bandwidth_TBps': aggregate['effective_remote_hbm_bw_TBps']},
                {'precision': 'fp8',
                 'arithmetic_intensity_flops_per_byte': args.arithmetic_intensity}),
            'limitations': [
                'No measured performance, thermal or packaging validation.',
                'Local stack bandwidth is not uniform remotely accessible bandwidth.',
                'Six ports per tile is optimistic before memory-tile placement.',
                'Remote bound can fall further with cuts, contention and failures.',
                'No full-system power, efficiency ranking or competitor comparison.',
            ],
        }
        serialized = json.dumps(report, indent=2, allow_nan=False) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(serialized, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
