"""Streaming JOCForge reduction with explicit dynamic-ADM adaptations."""
import math
import numpy as np
from .scene import Scene, Point
from . import jocforge as core

FRAME = 1024
FADE = 240  # 5 ms at 48 kHz; crossfade source contributions between assignments.
UPSTREAM = '05a4108e0c6288130dec1203b301979a91475fca'


def cluster(wave, source, first, samples, check, emit, capacity=16):
    sources = [i for i in range(source.channels) if i != source.lfe]
    lfe_count = int(source.lfe is not None)
    budget = min(capacity - 1, capacity - lfe_count, len(sources))
    if not sources or budget < 1:
        raise ValueError('JOCForge 聚类需要至少一路非 LFE 音频。')
    count = math.ceil(samples / FRAME)
    energy = np.zeros((count, len(sources)))
    positions = np.zeros((count, len(sources), 3))
    signatures = np.zeros((count, len(sources), 16))
    whole_bins = np.zeros((len(sources), 16))
    bin_lengths = np.zeros(16)
    all_carriers = list(range(source.channels))
    for begin in range(0, samples, FRAME * 64):
        check()
        active = min(samples - begin, FRAME * 64)
        block = wave.read(first + begin, active)
        block *= source.pcm_gains(first + begin, active, all_carriers)
        block = block[:, sources].astype(np.float64)
        for offset in range(0, active, FRAME):
            frame = (begin + offset) // FRAME
            section = block[offset:offset + FRAME]
            energy[frame] = (section ** 2).mean(axis=0)
            signatures[frame] = core.signature(section)
            points = source.at(first + begin + offset)
            positions[frame] = [(points[i].x, points[i].y, points[i].z) for i in sources]
        # Initialize representatives from the complete selection, including
        # music after leading silence, rather than locking seeds to a silent frame.
        for b in range(16):
            left, right = max(begin, b * samples // 16), min(begin + active, (b + 1) * samples // 16)
            if right > left:
                whole_bins[:, b] += block[left - begin:right - begin].sum(axis=0)
                bin_lengths[b] += right - left
        emit(type='progress', phase='analyse', progress=round(5 * (begin + active) / samples, 1),
             message='JOCForge：分析音频特征、能量和动态位置')
    means = whole_bins / np.maximum(bin_lengths, 1)[None, :]
    norm = np.linalg.norm(means, axis=1)
    means /= np.where(norm > core.FLOOR, norm, 1)[:, None]
    lengths = np.minimum(FRAME, samples - np.arange(count) * FRAME)
    totals = (energy * lengths[:, None]).sum(axis=0)
    average_energy = totals / samples
    average_positions = (positions * (energy * lengths[:, None])[:, :, None]).sum(axis=0)
    average_positions /= np.maximum(totals[:, None], core.FLOOR)
    silent = totals <= core.FLOOR
    average_positions[silent] = positions[0, silent]
    slots = {'L': 0, 'R': 1, 'C': 2, 'Ls': 3, 'Rs': 4, 'Lss': 3, 'Rss': 4, 'Lrs': 5, 'Rrs': 6}
    semantics = [slots.get(source.bed.get(i)) for i in sources]
    seeds = core.choose_seeds(core.distances(means, average_energy, average_positions),
                             average_energy, budget, semantics)
    assignments = np.zeros((count, len(sources)), dtype=np.uint8)
    gains = np.zeros((count, len(sources)), dtype=np.float64)
    centers = np.zeros((count, budget, 3))
    changes = np.zeros(len(sources), dtype=np.int64)
    weighted_error, total_energy, maximum = 0., 0., 0.
    previous = None
    for begin in range(0, samples, FRAME * 64):
        check()
        active = min(samples - begin, FRAME * 64)
        block = wave.read(first + begin, active)
        block *= source.pcm_gains(first + begin, active, all_carriers)
        block = block[:, sources].astype(np.float64)
        for offset in range(0, active, FRAME):
            frame = (begin + offset) // FRAME
            assigned = core.assign(core.distances(signatures[frame], energy[frame], positions[frame]), seeds, previous)
            target, center, _ = core.aggregate(block[offset:offset + FRAME], energy[frame], positions[frame], assigned, budget)
            assignments[frame], gains[frame], centers[frame] = assigned, target, center
            if previous is not None:
                changes += assigned != previous
            previous = assigned
            error = ((positions[frame] - center[assigned]) ** 2).sum(axis=1)
            weight = energy[frame] * lengths[frame]
            weighted_error += float((weight * error).sum())
            total_energy += float(weight.sum())
            audible = energy[frame] > core.FLOOR
            if audible.any():
                maximum = max(maximum, float(np.sqrt(error[audible].max())))
        emit(type='progress', phase='analyse', progress=round(5 + 5 * (begin + active) / samples, 1),
             message='JOCForge：稳定分组并计算混合增益')

    def evaluate(sample):
        frame = max(0, min(count - 1, (sample - first) // FRAME))
        return ([Point()] if lfe_count else []) + [Point(*p) for p in centers[frame]]

    names = ([source.names[source.lfe]] if lfe_count else []) + [f'JOCForge cluster {j + 1}' for j in range(budget)]
    encoded = Scene([first + i * FRAME for i in range(count)], [], 0 if lfe_count else None,
                    names, source.source, evaluate, {0: 'LFE'} if lfe_count else {},
                    lambda start, count, carriers: np.ones((count, len(carriers)), dtype=np.float32))

    def read(start, length):
        block = wave.read(start, length)
        block *= source.pcm_gains(start, length, all_carriers)
        result = np.zeros((length, budget + lfe_count), dtype=np.float64)
        if lfe_count:
            result[:, 0] = block[:, source.lfe]
        offset = 0
        while offset < length:
            relative = start + offset - first
            frame, within = divmod(relative, FRAME)
            n = min(length - offset, FRAME - within)
            signal = block[offset:offset + n, sources].astype(np.float64)
            alpha = np.clip((np.arange(within, within + n) + 1) / FADE, 0, 1) if frame else np.ones(n)
            for group in range(budget):
                current = gains[frame] * (assignments[frame] == group)
                prior = gains[max(0, frame - 1)] * (assignments[max(0, frame - 1)] == group)
                result[offset:offset + n, group + lfe_count] = (signal @ prior) * (1 - alpha) + (signal @ current) * alpha
            offset += n
        return result.astype(np.float32)

    report = dict(method='JOCForge farthest-point + RMS-weighted aggregate', lossy=True,
                  algorithm_source='https://github.com/chyinan/JOCForge', algorithm_commit=UPSTREAM,
                  source_objects=source.objects, source_non_lfe_channels=len(sources), encoded_objects=budget,
                  bed_channels_preserved=lfe_count, bed_channels_promoted_to_objects=len(source.bed) - lfe_count,
                  analysis_frame_samples=FRAME, transition_crossfade_samples=FADE,
                  objective_weights=dict(signal=.55, spatial=.35, energy=.10, continuity=.15),
                  signature_bins=16, cluster_peak_limit=.9,
                  adaptations=['ADM Cartesian XYZ instead of upstream position components',
                               'whole-selection seed initialization; 1024-sample dynamic windows',
                               '240-sample linear PCM coefficient crossfades'],
                  position_error_units='Euclidean ADM Cartesian distance',
                  position_error_scope='audible source windows, at frame starts, before target codec quantization and crossfades',
                  energy_weighted_position_rms=math.sqrt(weighted_error / max(total_energy, core.FLOOR)),
                  maximum_position_error=maximum, source_audio_discarded=False,
                  source_gains_changed=True, source_assignment_changes=int(changes.sum()),
                  representative_sources=[sources[i] for i in seeds],
                  sources=[dict(index=i, name=source.names[i], role=source.bed.get(i, 'Objects'),
                                assignment_changes=int(changes[j]),
                                minimum_contribution_gain=float(gains[:, j].min()),
                                maximum_contribution_gain=float(gains[:, j].max())) for j, i in enumerate(sources)],
                  _timeline=dict(frame_samples=first + np.arange(count) * FRAME,
                                 source_indices=np.array(sources), assignments=assignments,
                                 contribution_gains=gains, positions=centers))
    return encoded, read, report
