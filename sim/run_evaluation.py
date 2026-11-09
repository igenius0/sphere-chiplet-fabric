"""Reproducible, equal-node topology experiments; historical figures stay intact."""
import argparse
import json
from pathlib import Path
import random

import numpy as np

try:
    from .fabric import (LatencyModel, hotspot_traffic, ring_all_reduce_time,
                         run_traffic, tree_all_reduce_time, uniform_random_traffic)
    from .topology import make_icosphere, make_torus, topology_stats
    from .validation import finite, integer
    from .workloads import resource_budget, undirected_edges
except ImportError:
    from fabric import (LatencyModel, hotspot_traffic, ring_all_reduce_time,
                        run_traffic, tree_all_reduce_time, uniform_random_traffic)
    from topology import make_icosphere, make_torus, topology_stats
    from validation import finite, integer
    from workloads import resource_budget, undirected_edges


def balanced_coordinate_cut(topo, link_bw_gbps=256.0):
    """Bandwidth of one balanced coordinate-median cut, not minimum bisection."""
    finite(link_bw_gbps, 'link_bw_gbps', strict=True)
    axis = 2 if topo.get('topology_type') == 'icosphere' else 0
    ordered = sorted(topo['nodes'], key=lambda n: (topo['coords'][n][axis], n))
    left = set(ordered[:len(ordered) // 2])
    crossing = sum((u in left) != (v in left) for u, v in undirected_edges(topo))
    return {'partition_sizes': [len(left), len(ordered) - len(left)],
            'crossing_links': crossing,
            'cut_bandwidth_gbps': crossing * link_bw_gbps,
            'method': 'one balanced coordinate-median cut; not a minimum-cut search'}


def _compact(metrics):
    return {key: value for key, value in metrics.items()
            if key not in ('latencies_ns', 'hops')}


def evaluate(subdivisions=2, n_packets=2000, seed=42, bandwidth_policy='equal_total'):
    integer(subdivisions, 'subdivisions', 1)
    if subdivisions not in (1, 2):
        raise ValueError('subdivisions must be 1 or 2')
    integer(n_packets, 'n_packets', 1)
    if n_packets > 100000:
        raise ValueError('n_packets must be <= 100000')
    integer(seed, 'seed')
    if bandwidth_policy not in ('equal_total', 'per_link'):
        raise ValueError('bandwidth_policy must be equal_total or per_link')
    rows, cols = (6, 7) if subdivisions == 1 else (9, 18)
    topologies = [('sphere', make_icosphere(subdivisions)),
                  ('sphere_chords', make_icosphere(subdivisions, add_chords=True,
                                                   n_chords=4 if subdivisions == 1 else 10)),
                  ('torus', make_torus(rows, cols))]
    nodes = topologies[0][1]['nodes']
    pairs = uniform_random_traffic(nodes, n_packets, random.Random(seed))
    hot = hotspot_traffic(nodes, n_packets, rng=random.Random(seed))
    reference_links = len(undirected_edges(topologies[-1][1]))
    rates = [0.001, 0.01, 0.1, 0.5, 1.0, 2.0]
    candidates, plot_data = [], []
    for name, topo in topologies:
        scale = reference_links / len(undirected_edges(topo)) if bandwidth_policy == 'equal_total' else 1
        model = LatencyModel(link_rate_gbps=32.0 * scale)
        stats = topology_stats(topo)
        unloaded = run_traffic(topo, pairs, model)
        hotspot = run_traffic(topo, hot, model, injection_rate_pkts_per_ns=0.5)
        load = [run_traffic(topo, pairs, model, injection_rate_pkts_per_ns=rate) for rate in rates]
        payload_flits = len(nodes) * 8
        collective = {
            'per_rank_payload_flits': payload_flits,
            'ring_chunk_flits': 8,
            'tree_message_flits': payload_flits,
            'ring_time_ns': ring_all_reduce_time(topo, 8, model),
            'tree_time_ns': tree_all_reduce_time(topo, payload_flits, model),
            'semantics': 'same full payload per rank; ideal reduce/broadcast schedules; no reduction compute',
        }
        candidates.append({
            'name': name,
            'resource_budget': resource_budget(topo, model),
            'topology_stats': {k: v for k, v in stats.items() if k != 'hop_dist'},
            'uncontended_latency': _compact(unloaded),
            'load_sweep': {'aggregate_rates_pkts_per_ns': rates,
                           'mean_latency_ns': [r['avg_latency_ns'] for r in load],
                           'p99_latency_ns': [r['p99_latency_ns'] for r in load]},
            'all_reduce': collective,
            'balanced_coordinate_cut': balanced_coordinate_cut(topo, model.link_bw_gbps),
            'hotspot': _compact(hotspot),
            'assumed_path_energy_pj_per_bit': stats['avg_hops'] * (1.5 + 0.5) + 0.5,
        })
        plot_data.append((stats['hop_dist'], unloaded['latencies_ns'], hotspot['latencies_ns']))
    report = {
        'schema_version': 2,
        'config': {'subdivisions': subdivisions, 'n_packets': n_packets,
                   'seed': seed, 'bandwidth_policy': bandwidth_policy},
        'assumptions': {
            'evidence': 'exploratory store-and-forward simulation, not cycle-accurate hardware validation',
            'fairness': 'equal nodes and shared rank traces; optional equal baseline total directed bandwidth',
            'uncontended': 'every packet is isolated with link state reset',
            'contention': 'chronological hop events; infinite output queues and full-duplex directed links',
            'physical': 'surface arcs or straight chords on sphere; ideal fixed 4mm torus links; wiring and power differ',
            'cut': 'one balanced coordinate partition, not minimum bisection',
            'collectives': 'matched full payload; ideal schedules omit compute, launch, credits and protocol overhead',
            'energy': 'unvalidated constant 1.5pJ/link-bit and 0.5pJ/router-bit at every rate; path-count proxy only',
            'omitted': 'finite buffers, VC arbitration, endpoint shared injection, memory, faults, thermal and SI/PI',
        },
        'candidates': candidates,
    }
    return report, plot_data


def write_figures(report, plot_data, output_dir):
    # Keep importing this module free of plotting/file-system side effects.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    directory = Path(output_dir) / 'figures'
    directory.mkdir(parents=True, exist_ok=True)
    candidates = report['candidates']
    names = [c['name'] for c in candidates]
    colors = ['#246a9c', '#d47a22', '#368b57']
    def save(fig, name):
        fig.tight_layout()
        for extension in ('png', 'pdf'):
            fig.savefig(directory / f'{name}.{extension}', bbox_inches='tight', dpi=150)
        plt.close(fig)
    for index, name, title, xlabel in [
            (0, 'fig1_hop_cdf', 'Shortest-hop distribution', 'Hops'),
            (1, 'fig2_latency_cdf', 'Isolated packet latency', 'Latency (ns)'),
            (2, 'fig7_hotspot', 'Hotspot traffic at 0.5 aggregate packets/ns', 'Latency (ns)')]:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        for label, color, values in zip(names, colors, plot_data):
            data = sorted(values[index])
            ax.plot(data, np.arange(1, len(data) + 1) / len(data), label=label, color=color)
        ax.set(xlabel=xlabel, ylabel='CDF', title=title)
        ax.legend(); ax.grid(alpha=0.2)
        save(fig, name)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for candidate, color in zip(candidates, colors):
        load = candidate['load_sweep']
        ax.plot(load['aggregate_rates_pkts_per_ns'], load['mean_latency_ns'],
                label=candidate['name'], color=color, marker='o')
    ax.set(xlabel='Aggregate packets/ns', ylabel='Mean latency (ns)',
           title='Offered load (infinite-queue model)', xscale='log', yscale='log')
    ax.legend(); ax.grid(alpha=0.2)
    save(fig, 'fig3_latency_load')
    for filename, title, ylabel, values in [
            ('fig4_tail_ratio', 'Isolated latency tail ratio', 'p99 / p50',
             [c['uncontended_latency']['p99_latency_ns'] / c['uncontended_latency']['p50_latency_ns'] for c in candidates]),
            ('fig6_balanced_cut', 'Chosen balanced coordinate-cut bandwidth', 'Gb/s per direction',
             [c['balanced_coordinate_cut']['cut_bandwidth_gbps'] for c in candidates]),
            ('fig8_energy_proxy', 'Assumed path energy (unvalidated constant per hop)', 'pJ/bit proxy',
             [c['assumed_path_energy_pj_per_bit'] for c in candidates])]:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.bar(names, values, color=colors)
        ax.set(title=title, ylabel=ylabel)
        ax.grid(alpha=0.2, axis='y')
        save(fig, filename)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    x = np.arange(len(names))
    ax.bar(x - 0.2, [c['all_reduce']['ring_time_ns'] / 1000 for c in candidates], 0.4, label='Ring')
    ax.bar(x + 0.2, [c['all_reduce']['tree_time_ns'] / 1000 for c in candidates], 0.4, label='Tree')
    ax.set_xticks(x, names)
    ax.set(ylabel='Time (us)', title=f'Ideal collective schedules, {candidates[0]["all_reduce"]["per_rank_payload_flits"] * 64} bytes/rank')
    ax.legend(); ax.grid(alpha=0.2, axis='y')
    save(fig, 'fig5_allreduce')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'generated' / 'evaluation')
    parser.add_argument('--subdivisions', type=int, choices=(1, 2), default=2)
    parser.add_argument('--n-packets', type=int, default=2000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--bandwidth-policy', choices=('equal_total', 'per_link'), default='equal_total')
    parser.add_argument('--no-figures', action='store_true')
    args = parser.parse_args(argv)
    try:
        report, plots = evaluate(args.subdivisions, args.n_packets, args.seed, args.bandwidth_policy)
        serialized = json.dumps(report, indent=2, allow_nan=False) + '\n'
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / 'results.json').write_text(serialized, encoding='utf-8')
        if not args.no_figures:
            write_figures(report, plots, args.output_dir)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    print(f'Wrote exploratory evaluation to {args.output_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
