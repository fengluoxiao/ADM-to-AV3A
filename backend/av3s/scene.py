"""ADM input scenes, with independent object and DirectSpeakers tracks."""
from bisect import bisect_right
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Callable
import numpy as np
from defusedxml import ElementTree as ET


def finite(v, low, high, name):
    v = float(v)
    if not math.isfinite(v) or not low <= v <= high:
        raise ValueError(f'{name} 超出支持范围 [{low}, {high}]：{v}')
    return v


@dataclass(frozen=True)
class Point:
    x: float = 0
    y: float = 0
    z: float = 0
    gain: float = 1
    size: float = 0

    @classmethod
    def parse(cls, d):
        return cls(*(finite(d.get(k, 1 if k == 'gain' else 0), lo, hi, k)
                     for k, lo, hi in [('x', -1, 1), ('y', -1, 1), ('z', -1, 1),
                                       ('gain', 0, 6), ('size', 0, 1)]))


@dataclass
class Scene:
    times: list
    frames: list
    lfe: int | None
    names: list
    source: str
    evaluator: Callable | None = None
    bed: dict = field(default_factory=dict)
    gain_evaluator: Callable | None = None

    def at(self, sample):
        if self.evaluator:
            return self.evaluator(sample)
        return self.frames[max(0, bisect_right(self.times, sample) - 1)]

    def pcm_gains(self, start, count, carriers):
        if self.gain_evaluator:
            return self.gain_evaluator(start, count, carriers)
        return np.array([[self.at(start + j)[i].gain for i in carriers]
                         for j in range(count)], dtype=np.float32)

    @property
    def channels(self):
        return len(self.names)

    @property
    def objects(self):
        return self.channels - len(self.bed or ({self.lfe: 'LFE'} if self.lfe is not None else {}))


def load_jsonl(path, wave):
    times, frames, lfe, names = [], [], None, []
    with Path(path).open(encoding='utf-8-sig') as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            d = json.loads(line)
            sample = d['sample']
            if type(sample) is not int or sample < 0 or (times and sample <= times[-1]):
                raise ValueError(f'第 {line_no} 行 sample 必须是递增的非负整数。')
            objects = d['objects']
            if len(objects) != wave.channels:
                raise ValueError(f'第 {line_no} 行对象数与 WAV 声道数不同。')
            if any(o.get('index', i) != i for i, o in enumerate(objects)):
                raise ValueError('JSONL index 必须与 WAV 声道顺序一致（从 0 开始）。')
            lfes = [i for i, o in enumerate(objects) if o.get('lfe', False)]
            if len(lfes) > 1:
                raise ValueError('当前仅支持一个独立 LFE。')
            this_lfe = lfes[0] if lfes else None
            if frames and this_lfe != lfe:
                raise ValueError('LFE 映射不能随时间变化。')
            lfe = this_lfe
            if not names:
                names = [o.get('name', f'Object {i + 1}') for i, o in enumerate(objects)]
            times.append(sample)
            frames.append([Point.parse(o) for o in objects])
    if not times or times[0] != 0:
        raise ValueError('空间元数据必须从 sample=0 开始。')
    return Scene(times, frames, lfe, names, str(path), bed={lfe: 'LFE'} if lfe is not None else {})


def text(e, name, default=''):
    n = e.find(name)
    return n.text.strip() if n is not None and n.text else default


def time_samples(t, rate):
    # ADM HH:MM:SS.fraction; fractional-sample time syntax is intentionally rejected.
    if 'S' in t:
        raise ValueError('暂不支持 ADM fractional-sample 时间语法。')
    parts = t.split(':')
    if len(parts) != 3:
        raise ValueError(f'无效 ADM 时间：{t}')
    return round((int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])) * rate)


def load_adm(raw, wave, source):
    root = ET.fromstring(raw)
    for e in root.iter():
        e.tag = e.tag.rsplit('}', 1)[-1]
    by_id = {}
    for e in root.iter():
        for key, value in e.attrib.items():
            if key.endswith('ID') or key == 'UID':
                by_id[value] = e
    # Fail closed on ADM features that this converter cannot preserve.
    for tag in ('alternativeValueSet', 'audioComplementaryObjectIDRef', 'audioObjectInteraction'):
        if root.find('.//' + tag) is not None:
            raise ValueError(f'暂不支持 ADM {tag}。')
    programmes = root.findall('.//audioProgramme')
    if len(programmes) > 1:
        raise ValueError('暂不支持多节目 ADM；请导出单节目母版。')
    assignments = wave.channel_assignments()
    if not assignments:
        raise ValueError('ADM 需要 WAV chna 块映射声道；无 chna 请使用 scene.jsonl。')
    tracks = {}
    timing = {}
    names = [''] * wave.channels
    lfe = None
    bed = {}
    owners = {}
    for obj in root.findall('.//audioObject'):
        if text(obj, 'gain', '1') != '1' or obj.get('start', '00:00:00.00000') not in ('00:00:00.00000', '00:00:00.000', '00:00:00'):
            raise ValueError('暂不支持 audioObject 层增益或非零起点。')
        if 'duration' in obj.attrib and time_samples(obj.attrib['duration'], wave.rate) < wave.samples:
            raise ValueError('暂不支持 audioObject 层裁剪时长。')
        if obj.findall('audioObjectIDRef'):
            raise ValueError('暂不支持嵌套 audioObject。')
        for uid in obj.findall('audioTrackUIDRef'):
            if uid.text in owners:
                raise ValueError('不支持同一轨道被多个对象引用。')
            owners[uid.text] = obj.get('audioObjectName', uid.text)
    for uid, (index, chna_ref) in assignments.items():
        if index in tracks:
            raise ValueError('不支持多个 UID 复用同一 WAV 声道。')
        track = by_id.get(uid)
        if track is None:
            raise ValueError(f'ADM 缺少 {uid}。')
        channel_id = text(track, 'audioChannelFormatIDRef')
        if not channel_id:
            track_ref = text(track, 'audioTrackFormatIDRef', chna_ref)
            tf = by_id.get(track_ref)
            stream = by_id.get(text(tf, 'audioStreamFormatIDRef')) if tf is not None else None
            if stream is not None:
                channel_id = text(stream, 'audioChannelFormatIDRef')
        if not channel_id and chna_ref.startswith('AC_'):
            channel_id = chna_ref
        channel = by_id.get(channel_id)
        is_lfe = channel_id.upper() == 'AC_00010004'
        if channel is None and not is_lfe:
            raise ValueError(f'无法解析声道 {uid} → {channel_id}。')
        if channel is not None:
            labels = ' '.join((n.text or '') for n in channel.findall('.//speakerLabel'))
            type_label = channel.get('typeLabel', channel_id[3:7])
            is_lfe = is_lfe or (type_label == '0001' and ('LFE' in labels.upper() or 'LFE' in channel.get('audioChannelFormatName', '').upper()))
            if type_label not in ('0001', '0003'):
                raise ValueError('支持 Objects 和 DirectSpeakers；HOA/Matrix 暂不支持。')
            if type_label == '0001':
                # Dolby room-centric labels carry the speaker role. The LFE's
                # placeholder XYZ must never make it a positional object.
                label = labels.strip().removeprefix('RC_')
                if not label:
                    raise ValueError(f'DirectSpeakers 缺少明确 speakerLabel：{channel_id}')
                bed[index] = label
        if is_lfe:
            if lfe is not None:
                raise ValueError('当前仅支持一个独立 LFE。')
            lfe = index
            bed[index] = 'LFE'
        blocks = channel.findall('audioBlockFormat') if channel is not None else []
        if not blocks and not is_lfe:
            raise ValueError('对象没有 audioBlockFormat。')
        points = []
        timing[index] = []
        for block in blocks:
            allowed = {'cartesian', 'position', 'gain', 'width', 'height', 'depth', 'jumpPosition', 'speakerLabel'}
            for child in block:
                if child.tag not in allowed:
                    raise ValueError(f'不支持的 ADM 块属性：{child.tag}')
            if any(p.get('bound') or p.get('screenEdgeLock') for p in block.findall('position')):
                raise ValueError('暂不支持 ADM position bound / screenEdgeLock。')
            jump = block.find('jumpPosition')
            jump_length = finite(jump.get('interpolationLength', '0'), 0, 86400,
                                 'interpolationLength') if jump is not None else 0
            pos = {p.get('coordinate', '').lower(): float(p.text) for p in block.findall('position')}
            if is_lfe:
                xyz = (0, 0, 0)
            elif text(block, 'cartesian', '0') == '1':
                if not {'x', 'y'} <= pos.keys():
                    raise ValueError('Cartesian 对象需要 X、Y；省略 Z 时按 ADM 默认值 0。')
                # ADM: X right, Y front, Z up. AVS3 cartesian uses the same axes.
                xyz = pos['x'], pos['y'], pos.get('z', 0)
            else:
                if not {'azimuth', 'elevation'} <= pos.keys():
                    raise ValueError('球坐标对象需要 azimuth、elevation。')
                az, el = math.radians(pos['azimuth']), math.radians(pos['elevation'])
                d = pos.get('distance', 1)
                xyz = (-math.sin(az) * math.cos(el) * d, math.cos(az) * math.cos(el) * d, math.sin(el) * d)
            gain_node = block.find('gain')
            gain = float(text(block, 'gain', '1'))
            if gain_node is not None and gain_node.get('gainUnit', 'linear') == 'dB':
                gain = 10 ** (gain / 20)
            ext = [float(text(block, k, '0')) for k in ('width', 'height', 'depth')]
            if any(ext):
                raise ValueError('暂只接受 ADM 点对象（width/height/depth=0）；JSONL 可使用归一化 size。')
            start = time_samples(block.get('rtime', '00:00:00.00000'), wave.rate)
            if start < 0 or (points and start <= points[-1][0]):
                raise ValueError('ADM 块起点必须严格递增。')
            duration = time_samples(block.attrib['duration'], wave.rate) if 'duration' in block.attrib else None
            if duration is not None and duration <= 0:
                raise ValueError('ADM 块 duration 必须大于 0。')
            timing[index].append((duration, jump is not None and (jump.text or '').strip() == '1',
                                  round(jump_length * wave.rate)))
            points.append((start, Point.parse(dict(zip(('x', 'y', 'z'), xyz)) | {'gain': gain})))
        if not points:
            points = [(0, Point())]
            timing[index] = [(wave.samples, True, 0)]
        if points[0][0] != 0:
            raise ValueError('对象块必须从 0 开始。')
        for j, (begin, _) in enumerate(points):
            duration, jump, interpolation = timing[index][j]
            if len(points) > 1 and duration is None:
                raise ValueError('动态 ADM 块需要明确 duration。')
            # Decimal ADM times can independently round to adjacent samples.
            if j + 1 < len(points) and abs(begin + duration - points[j + 1][0]) > 1:
                raise ValueError('暂不支持 ADM 块时间间隙或重叠。')
        if timing[index][-1][0] is not None and points[-1][0] + timing[index][-1][0] < wave.samples - 1:
            raise ValueError('ADM 块未覆盖全部 WAV；请导出与音频等长的元数据。')
        tracks[index] = points
        names[index] = f'{owners.get(uid, uid)} / {bed[index]}' if index in bed else owners.get(uid, uid)
    if len(tracks) != wave.channels:
        raise ValueError('ADM/chna 必须明确映射全部 WAV 声道。')
    times = sorted({t for points in tracks.values() for t, _ in points})
    track_times = {i: [t for t, _ in p] for i, p in tracks.items()}
    origins = {}
    for i, points in tracks.items():
        origins[i] = [points[0][1]]
        for j in range(1, len(points)):
            previous = points[j - 1][1]
            duration, jump, interpolation = timing[i][j - 1]
            length = interpolation if jump else duration
            if j > 1 and length:
                fraction = min(1, (points[j][0] - points[j - 1][0]) / length)
                origin = origins[i][j - 1]
                previous = Point(*(getattr(origin, k) + (getattr(previous, k) - getattr(origin, k)) * fraction
                                   for k in ('x', 'y', 'z', 'gain', 'size')))
            origins[i].append(previous)

    def evaluate(sample):
        result = []
        for i in range(wave.channels):
            j = max(0, bisect_right(track_times[i], sample) - 1)
            begin, point = tracks[i][j]
            duration, jump, interpolation = timing[i][j]
            length = interpolation if jump else duration
            if j and length:
                previous = origins[i][j]
                fraction = min(1, max(0, (sample - begin) / length))
                point = Point(*(getattr(previous, k) + (getattr(point, k) - getattr(previous, k)) * fraction
                                for k in ('x', 'y', 'z', 'gain', 'size')))
            result.append(point)
        return result

    # Evaluate only requested instants, rather than materializing every track
    # at every automation timestamp in large DAW exports.
    gain_arrays = {i: np.array([p.gain for _, p in points]) for i, points in tracks.items()}
    gain_origins = {i: np.array([p.gain for p in points]) for i, points in origins.items()}
    gain_unity = {i: np.all(values == 1) for i, values in gain_arrays.items()}
    interpolation_arrays = {i: np.array([length if jump else duration or 0 for duration, jump, length in entries])
                            for i, entries in timing.items()}
    time_arrays = {i: np.array(values) for i, values in track_times.items()}

    def gains(start, count, carriers):
        samples = np.arange(start, start + count)
        result = np.ones((count, len(carriers)), dtype=np.float32)
        for column, i in enumerate(carriers):
            values = gain_arrays[i]
            if gain_unity[i]:
                continue
            indices = np.maximum(0, np.searchsorted(time_arrays[i], samples, side='right') - 1)
            result[:, column] = values[indices]
            lengths = interpolation_arrays[i][indices]
            interpolate = (indices > 0) & (lengths > 0)
            selected = indices[interpolate]
            fraction = np.clip((samples[interpolate] - time_arrays[i][selected]) /
                               lengths[interpolate], 0, 1)
            previous = gain_origins[i][selected]
            result[interpolate, column] = previous + (values[selected] - previous) * fraction
        return result

    return Scene(times, [], lfe, names, source, evaluate, bed, gains)


def load_scene(wave, path=None):
    if path and Path(path).suffix.lower() == '.jsonl':
        scene = load_jsonl(path, wave)
    else:
        raw = Path(path).read_bytes() if path else wave.chunks.get(b'axml')
        if not raw:
            raise ValueError('WAV 没有 axml；请选择单独的 ADM XML 或 scene.jsonl。')
        scene = load_adm(raw.rstrip(b'\0'), wave, str(path or 'embedded axml'))
    return scene
