"""AVS3 RM typed binary metadata; 16 physical channels, no capacity patches."""
import math
import struct


# Common-definition pack IDs and order from the upstream AVS3 renderer's
# speaker_definition.hpp. Dolby room-centric speaker roles map to these slots.
BED_LAYOUTS = {
    ('L', 'R'): (2, '2.0'),
    ('L', 'R', 'C', 'LFE', 'Ls', 'Rs'): (3, '5.1'),
    ('L', 'R', 'C', 'LFE', 'Lss', 'Rss', 'Lrs', 'Rrs', 'Lts', 'Rts'): (22, '7.1.2'),
}


def bed_layout(scene):
    if not scene.bed or set(scene.bed.values()) == {'LFE'}:
        return None
    for labels, (pack_id, name) in BED_LAYOUTS.items():
        if len(labels) == len(scene.bed) and set(labels) == set(scene.bed.values()):
            by_label = {label: carrier for carrier, label in scene.bed.items()}
            return pack_id, name, [by_label[label] for label in labels]
    raise ValueError('暂不支持该 DirectSpeakers 布局；当前支持 2.0、5.1、7.1.2 和单独 LFE。')


class Binary:
    def __init__(self):
        self.data = bytearray()

    def i(self, *values):
        self.data.extend(struct.pack('<' + 'h' * len(values), *values))

    def f(self, *values):
        self.data.extend(struct.pack('<' + 'f' * len(values), *values))


def quantize(value, bits):
    resolution = 2 / ((1 << bits) - 1)
    offset = 1 << (bits - 1)
    q = max(0, min((1 << bits) - 1, math.floor(value / resolution + .5) + offset))
    return (q - offset) * resolution


def static_metadata(scene):
    if not 1 <= scene.channels <= 16:
        raise ValueError('参考编码器空间元数据最多支持 16 路载体。')
    layout = bed_layout(scene)
    if layout:
        return bed_metadata(scene, layout)
    b = Binary()
    groups = int(scene.lfe is not None) + int(scene.objects > 0)
    b.i(1, 0, 0)  # static, no VR, basic level 1
    b.i(0, 0, 0, 0, 1, 0)  # programme -> content 0
    b.i(1, 0, 0, 0, 0, 0, groups, *range(groups))
    b.i(groups)
    for j in range(groups):
        b.i(j, *([0] * 8), 1, j)
    b.i(groups)
    offset = 0
    if scene.lfe is not None:
        # Common-definition 5.1 pack. Only LFE is active; the other five
        # logical channels reuse its carrier with explicit zero gain.
        b.i(0, 0, 1, 1)
        b.f(1)
        b.i(3, 6)
        for j in range(6):
            b.i(j, scene.lfe)
        offset = 6
    if scene.objects:
        b.i(groups - 1, 0, 1, 3)
        b.f(1)
        b.i(scene.objects)
        for logical, carrier in enumerate(i for i in range(scene.channels) if i != scene.lfe):
            b.i(offset + logical, carrier)
    b.i(offset + scene.objects)
    for j in range(offset + scene.objects):
        if j < offset and j != 3:
            b.i(j, 1, 0)
            b.f(0)
        else:
            b.i(j, 0)
    return b.data


def bed_metadata(scene, layout):
    pack_id, _, carriers = layout
    objects = [i for i in range(scene.channels) if i not in scene.bed]
    groups = 1 + bool(objects)
    b = Binary()
    b.i(1, 0, 0)
    b.i(0, 0, 0, 0, 1, 0)
    b.i(1, 0, 0, 0, 0, 0, groups, *range(groups))
    b.i(groups)
    for group in range(groups):
        b.i(group, *([0] * 8), 1, group)
    b.i(groups)
    b.i(0, 0, 1, 1)
    b.f(1)
    b.i(pack_id, len(carriers))
    for logical, carrier in enumerate(carriers):
        b.i(logical, carrier)
    if objects:
        b.i(1, 0, 1, 3)
        b.f(1)
        b.i(len(objects))
        for logical, carrier in enumerate(objects, len(carriers)):
            b.i(logical, carrier)
    b.i(scene.channels)
    for logical in range(scene.channels):
        b.i(logical, 0)
    return b.data


def frame_metadata(scene, points, static):
    b = Binary()
    b.data.extend(static)
    b.i(1, 0)  # dynamic, level 1; one record for every physical carrier
    for i, point in enumerate(points):
        b.i(0, i, 1, 1, 1, 0, 0)
        xyz = [quantize(v, bits) for v, bits in zip((point.x, point.y, point.z), (8, 6, 4))]
        size = [round(point.size * ((1 << n) - 1)) / ((1 << n) - 1) for n in (7, 5, 4)]
        # The LFE gain is baked into its PCM, not applied a second time.
        gain = 1 if i in scene.bed or i == scene.lfe else round(point.gain * 127 / 6) * 6 / 127
        b.f(*xyz, *size, gain)
        b.i(0)
    return b.data
