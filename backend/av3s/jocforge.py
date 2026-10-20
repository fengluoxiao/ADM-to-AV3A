# SPDX-License-Identifier: Apache-2.0
# Adapted from chyinan/JOCForge, reduction.rs, commit 05a4108e0c6288130dec1203b301979a91475fca.
# Modifications: NumPy port; positions are ADM Cartesian XYZ; caller supplies
# streaming features and adds PCM crossfades. See THIRD_PARTY_NOTICES.md.
"""Pure JOCForge seed, assignment and RMS aggregation operations."""
import numpy as np

FLOOR = 1e-12
WEIGHTS = (.55, .35, .10)
CONTINUITY = .15
PEAK_LIMIT = .90


def signature(signal, bins=16):
    """One source per column, matching the upstream integer bin boundaries."""
    length = len(signal)
    values = np.stack([signal[i * length // bins:max(i * length // bins + 1,
                          (i + 1) * length // bins)].mean(axis=0) for i in range(bins)], axis=1)
    norm = np.linalg.norm(values, axis=1)
    return values / np.where(norm > FLOOR, norm, 1)[:, None]


def distances(signatures, energy, positions):
    spatial = ((positions[:, None] - positions[None, :]) ** 2).sum(axis=2)
    spatial /= max(1., float(spatial.max()))
    signal = ((signatures[:, None] - signatures[None, :]) ** 2).mean(axis=2)
    rms = np.sqrt(energy)
    power = ((rms[:, None] - rms[None, :]) / (rms[:, None] + rms[None, :] + FLOOR)) ** 2
    return WEIGHTS[0] * signal + WEIGHTS[1] * spatial + WEIGHTS[2] * power


def choose_seeds(cost, energy, budget, semantic_slots=None):
    """Lowest source ordinal wins near ties; semantic carrier slots come first."""
    def maximum(values, candidates):
        best = candidates[0]
        for i in candidates[1:]:
            if values[i] - values[best] > FLOOR:
                best = i
        return best

    count = min(budget, len(energy))
    seeds = [maximum(energy, list(range(len(energy))))]
    while len(seeds) < count:
        nearest = cost[:, seeds].min(axis=1)
        seeds.append(maximum(nearest, [i for i in range(len(energy)) if i not in seeds]))
    ordered = []
    if semantic_slots is not None:
        for slot in range(7):
            candidates = [i for i, value in enumerate(semantic_slots) if value == slot]
            if candidates:
                ordered.append(maximum(energy, candidates))
    ordered += [i for i in seeds if i not in ordered]
    if not ordered or semantic_slots is None or not any(semantic_slots[i] is not None for i in ordered):
        ordered.sort()
    return np.array(ordered[:count], dtype=np.int64)


def assign(cost, seeds, previous=None, continuity=CONTINUITY):
    values = cost[:, seeds].copy()
    if previous is not None:
        values += continuity * (np.arange(len(seeds))[None, :] != previous[:, None])
    assigned = np.zeros(len(values), dtype=np.int64)
    for group in range(1, len(seeds)):
        best = values[np.arange(len(values)), assigned]
        delta = values[:, group] - best
        better = (delta < -FLOOR) | ((np.abs(delta) <= FLOOR) & (seeds[group] < seeds[assigned]))
        assigned[better] = group
    assigned[seeds] = np.arange(len(seeds))
    return assigned


def aggregate(signal, energy, positions, assigned, count):
    """Upstream RMS/L2 gains, RMS centroid and per-cluster headroom."""
    gains = np.zeros(len(energy))
    centers = np.zeros((count, 3))
    mixed = np.zeros((len(signal), count))
    rms = np.maximum(np.sqrt(energy), FLOOR)
    for group in range(count):
        members = np.flatnonzero(assigned == group)
        weights = rms[members]
        gains[members] = weights / np.linalg.norm(weights)
        centers[group] = (positions[members] * weights[:, None]).sum(axis=0) / weights.sum()
        mixed[:, group] = signal[:, members] @ gains[members]
        peak = float(np.abs(mixed[:, group]).max())
        if peak > PEAK_LIMIT:
            scale = PEAK_LIMIT / peak
            gains[members] *= scale
            mixed[:, group] *= scale
    return gains, centers, mixed
