"""
Topology generators for evaluation.
"""
import math
from collections import defaultdict, deque
import numpy as np


def _normalize(v):
    n = math.sqrt(sum(x*x for x in v))
    return tuple(x/n for x in v)


def _midpoint_unit(a, b):
    return _normalize(((a[0]+b[0])/2, (a[1]+b[1])/2, (a[2]+b[2])/2))


def icosahedron_base():
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
        (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
        (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]
    verts = [_normalize(v) for v in verts]
    faces = [
        (0,11,5),(0,5,1),(0,1,7),(0,7,10),(0,10,11),
        (1,5,9),(5,11,4),(11,10,2),(10,7,6),(7,1,8),
        (3,9,4),(3,4,2),(3,2,6),(3,6,8),(3,8,9),
        (4,9,5),(2,4,11),(6,2,10),(8,6,7),(9,8,1),
    ]
    return verts, faces


def make_icosphere(subdivisions=2, radius_mm=40.0, add_chords=False, n_chords=0):
    verts, faces = icosahedron_base()
    for _ in range(subdivisions):
        cache = {}
        def midpoint(i, j):
            key = tuple(sorted((i, j)))
            if key in cache:
                return cache[key]
            m = _midpoint_unit(verts[i], verts[j])
            verts.append(m)
            cache[key] = len(verts) - 1
            return cache[key]
        new_faces = []
        for (a, b, c) in faces:
            ab = midpoint(a, b)
            bc = midpoint(b, c)
            ca = midpoint(c, a)
            new_faces.extend([(a, ab, ca), (b, bc, ab),
                              (c, ca, bc), (ab, bc, ca)])
        faces = new_faces

    adj = defaultdict(set)
    for (a, b, c) in faces:
        adj[a].add(b); adj[b].add(a)
        adj[b].add(c); adj[c].add(b)
        adj[c].add(a); adj[a].add(c)

    coords = {i: tuple(radius_mm * x for x in v) for i, v in enumerate(verts)}
    adj_out = {i: sorted(list(s)) for i, s in adj.items()}

    if add_chords and n_chords > 0:
        verts_arr = np.array(verts)
        used = set()
        chords_added = 0
        for i in range(len(verts)):
            if chords_added >= n_chords:
                break
            if i in used:
                continue
            dots = verts_arr @ verts_arr[i]
            j = int(np.argmin(dots))
            if j == i or j in used or j in adj_out[i]:
                continue
            adj_out[i].append(j)
            adj_out[j].append(i)
            used.add(i); used.add(j)
            chords_added += 1

    link_length = {}
    for u, neighbors in adj_out.items():
        for v in neighbors:
            if (v, u) in link_length:
                link_length[(u, v)] = link_length[(v, u)]
                continue
            uv = np.array(coords[u])
            vv = np.array(coords[v])
            cos_angle = np.dot(uv, vv) / (np.linalg.norm(uv) * np.linalg.norm(vv))
            cos_angle = np.clip(cos_angle, -1, 1)
            arc = radius_mm * math.acos(cos_angle)
            link_length[(u, v)] = arc

    return {
        'name': f'icosphere_s{subdivisions}{"_chords"+str(n_chords) if add_chords else ""}',
        'nodes': list(range(len(verts))),
        'coords': coords,
        'adj': adj_out,
        'link_length': link_length,
        'topology_type': 'icosphere',
    }


def make_torus(rows, cols, link_length_mm=4.0):
    N = rows * cols
    adj = {i: [] for i in range(N)}
    coords = {}
    for r in range(rows):
        for c in range(cols):
            i = r * cols + c
            coords[i] = (c * link_length_mm, r * link_length_mm, 0)
            adj[i].append(r * cols + ((c+1) % cols))
            adj[i].append(r * cols + ((c-1) % cols))
            adj[i].append(((r+1) % rows) * cols + c)
            adj[i].append(((r-1) % rows) * cols + c)
    link_length = {}
    for u, neighbors in adj.items():
        for v in neighbors:
            link_length[(u, v)] = link_length_mm
    return {
        'name': f'torus_{rows}x{cols}',
        'nodes': list(range(N)),
        'coords': coords,
        'adj': {k: sorted(list(set(v))) for k, v in adj.items()},
        'link_length': link_length,
        'topology_type': 'torus',
    }


def make_dragonfly(n_groups=9, routers_per_group=6, link_length_mm=8.0):
    N = n_groups * routers_per_group
    adj = {i: [] for i in range(N)}
    coords = {}
    for g in range(n_groups):
        gx = 100 * math.cos(2*math.pi*g/n_groups)
        gy = 100 * math.sin(2*math.pi*g/n_groups)
        for r in range(routers_per_group):
            i = g * routers_per_group + r
            rx = gx + 10 * math.cos(2*math.pi*r/routers_per_group)
            ry = gy + 10 * math.sin(2*math.pi*r/routers_per_group)
            coords[i] = (rx, ry, 0)
    for g in range(n_groups):
        base = g * routers_per_group
        for r1 in range(routers_per_group):
            for r2 in range(r1+1, routers_per_group):
                adj[base + r1].append(base + r2)
                adj[base + r2].append(base + r1)
    for g in range(n_groups):
        for r in range(routers_per_group):
            i = g * routers_per_group + r
            target_g = (g + 1 + r) % n_groups
            if target_g == g:
                target_g = (target_g + 1) % n_groups
            j = target_g * routers_per_group + r
            if j not in adj[i]:
                adj[i].append(j)
                adj[j].append(i)
    link_length = {}
    for u, neighbors in adj.items():
        for v in neighbors:
            cu, cv = coords[u], coords[v]
            dist = math.sqrt(sum((cu[k]-cv[k])**2 for k in range(3)))
            link_length[(u, v)] = max(dist, 1.0)
    return {
        'name': f'dragonfly_{n_groups}g_{routers_per_group}r',
        'nodes': list(range(N)),
        'coords': coords,
        'adj': {k: sorted(list(set(v))) for k, v in adj.items()},
        'link_length': link_length,
        'topology_type': 'dragonfly',
    }


def topology_stats(topo):
    adj = topo['adj']
    N = len(adj)
    all_dists = []
    diameters = []
    for src in adj:
        dist = {src: 0}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in dist:
                    dist[v] = dist[u] + 1
                    q.append(v)
        for d in dist.values():
            if d > 0:
                all_dists.append(d)
        diameters.append(max(dist.values()))
    return {
        'nodes': N,
        'avg_degree': sum(len(v) for v in adj.values()) / N,
        'avg_hops': sum(all_dists) / len(all_dists),
        'max_hops': max(diameters),
        'p99_hops': sorted(all_dists)[int(0.99 * len(all_dists))],
        'p95_hops': sorted(all_dists)[int(0.95 * len(all_dists))],
        'hop_dist': all_dists,
    }
