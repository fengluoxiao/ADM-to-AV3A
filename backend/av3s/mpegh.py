"""Unmodified Ittiam external-renderer PCM/OAM bridge (pinned LC Level 4)."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import wave

from .cavern import presentation, selection
from .pipeline import Cancelled, convert
from .mp4 import copy_tags

OPTION = 'mpegh'
COMMIT = 'f7ff0ac78d4d83f0b853bf2dff2ef075c92724f8'
ENTRIES = (b'mha1', b'mhm1')


def decoder_path(override=None):
    root = Path(__file__).resolve().parents[2]
    candidates = [Path(override)] if override else [Path(os.environ['AV3S_MPEGH'])] if os.environ.get('AV3S_MPEGH') else [
        root / 'tools/mpegh/MpeghExport.exe', root / '.tools/mpegh/MpeghExport.exe']
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError('找不到 MPEG-H 导出组件，请运行 scripts/build-mpegh.ps1 或使用完整便携包。')


def check_cancel(flag):
    if flag and Path(flag).exists():
        raise Cancelled('已取消 MPEG-H 解码。')


def oam_frames(path, cancel_file=None):
    """Complete quantized OAM snapshots from our read-only native adapter."""
    expected, ids = 0, None
    with Path(path).open(encoding='utf-8') as source:
        for line in source:
            check_cancel(cancel_file)
            record = json.loads(line)
            sample, period, objects = record['sample'], record['period'], record['objects']
            if (type(sample) is not int or type(period) is not int
                    or sample != expected or period not in (64, 128, 256, 512, 1024)
                    or not 1 <= len(objects) <= 32):
                raise ValueError('MPEG-H 对象时间线不连续或帧长度无效。')
            this_ids, points, extras = [], [], []
            for values in objects:
                if (len(values) != 12 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
                        or any(type(v) is not int or abs(v) > 2147483647 for v in values[:9])):
                    raise ValueError('MPEG-H 对象状态无效。')
                obj, azi, ele, radius, gain, width, height, depth, priority, diffuse, divergence, sectors = values
                this_ids.append(obj)
                points.append(dict(azimuth=max(-180, min(180, azi * 1.5)),
                    elevation=max(-90, min(90, ele * 3)), radius=max(.5, 2 ** (min(15, radius) / 3) / 2),
                    gain=max(.004, min(5.957, 10 ** ((min(63, max(-64, gain)) - 32) / 40))),
                    spread_width=max(0, min(180, width * 1.5)), spread_height=max(0, min(90, height * 3)),
                    spread_depth=max(0, 2 ** (min(15, depth) / 3) / 2 - .5), priority=priority))
                extras.append(dict(diffuseness=diffuse, divergence=divergence, exclusion_sectors=sectors))
            if this_ids != list(range(len(objects))) or (ids is not None and this_ids != ids):
                raise ValueError('MPEG-H 对象载体编号无效或中途变化。')
            ids = this_ids
            yield sample, period, ids, points, extras
            expected += period


def run(input_path, work, mpegh=None, cancel_file=None, emit=lambda **_: None):
    check_cancel(cancel_file)
    work = Path(work)
    # The upstream C CLI uses narrow fopen and has short fixed filename buffers.
    # Stage the input under a short ASCII name; never pass the user's Unicode path.
    staged = work / ('input.mp4' if is_container(input_path) else 'input.mhas')
    with open(input_path, 'rb') as src, staged.open('xb') as dst:
        while block := src.read(1024 * 1024):
            check_cancel(cancel_file)
            dst.write(block)
    with (work / 'decoder.log').open('w+b') as log:
        proc = subprocess.Popen([str(decoder_path(mpegh)), staged.name], cwd=work,
            stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            emit(type='progress', phase='decode', progress=0, message='MPEG-H：解码独立对象与空间元数据')
            last_size, stalled_at, last_emit = -1, time.monotonic(), 0
            while proc.poll() is None:
                check_cancel(cancel_file)
                size = (work / 'decoder.log').stat().st_size
                now = time.monotonic()
                if size != last_size:
                    last_size, stalled_at = size, now
                if now - stalled_at > 120:
                    raise ValueError('MPEG-H 解码器长时间没有进展，文件可能不受支持。')
                if now - last_emit >= 2:
                    emit(type='progress', phase='decode', progress=5, message='MPEG-H：正在导出渲染前 PCM 与 OAM')
                    last_emit = now
                time.sleep(.1)
            check_cancel(cancel_file)
            log.seek(0)
            text = log.read().decode('utf-8', 'replace')
            if proc.returncode or 'fatal error:' in text.lower():
                raise ValueError(f'MPEG-H 解码失败（退出码 {proc.returncode}）：' + text[-1800:])
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
    return analyse(input_path, work, cancel_file)


def is_container(path):
    with open(path, 'rb') as f:
        head = f.read(12)
    return len(head) == 12 and head[4:8] == b'ftyp'


def analyse(input_path, work, cancel_file=None):
    work = Path(work)
    oam, pcm = work / 'oam.jsonl', work / 'objects.pcm'
    if not oam.is_file() or not oam.stat().st_size or not pcm.is_file():
        raise ValueError('MPEG-H 文件没有可导出的对象 PCM／OAM。')
    frames, count, radius, enhanced, last, period = 0, 0, 1., False, 0, 0
    ids = []
    for last, period, ids, points, extras in oam_frames(oam, cancel_file):
        frames += 1
        count = len(ids)
        radius = max(radius, *(p['radius'] for p in points))
        enhanced |= any(e[k] for e in extras for k in ('diffuseness', 'divergence', 'exclusion_sectors'))
    samples = last + period
    if not frames or pcm.stat().st_size != samples * count * 3:
        raise ValueError('MPEG-H 对象 PCM 长度与 OAM 不一致，拒绝错位导入。')
    timing = presentation(input_path, samples, ENTRIES) if is_container(input_path) else dict(
        media_start_samples=0, samples=samples, edit_list_applied=False)
    return dict(decoder='Ittiam/libmpegh', decoder_commit=COMMIT, input_codec='MPEG-H 3D Audio / 360RA',
        channels=count, objects=count, bed_channels=0, bed_layout=None, lfe=None, bed_speakers={},
        rate=48000, samples=timing['samples'], seconds=timing['samples'] / 48000,
        metadata_updates=frames, encoder_capacity=16, fits_encoder=count <= 16, bitrate=count * 96000,
        names=[f'MPEG-H object {i}' for i in ids], presentation=timing, raw_samples=samples,
        radius_normalization=radius, enhanced_metadata_present=enhanced,
        spatial_mapping=dict(coordinates='x right, y front, z up; MPEG-H positive azimuth is left',
            radius=f'whole scene divided by {radius:g} to fit the Vivid unit space',
            extent='isotropic size = max(width/180, height/90, depth/15.5); approximation',
            enhanced='original OAM snapshots and external binary retained; diffuseness/divergence/exclusion/priority are not mapped',
            timeline='OAM subframe positions held; Vivid samples every 1024 samples',
            pcm='unmodified upstream pre-render signed 24-bit; decoder saturation may already be present'))


def inspect_input(input_path, metadata=None, mpegh=None, cancel_file=None, emit=lambda **_: None,
                  codec='av3a', bitrate=768, ac4_native=False):
    if metadata:
        raise ValueError('360RA 使用 MPEG-H 解码得到的空间元数据，请清空外置元数据字段。')
    with tempfile.TemporaryDirectory(prefix='av3s-mpegh-') as temp:
        info = run(input_path, temp, mpegh, cancel_file, emit)
        if codec == 'ac4':
            from .ac4 import CAPACITY, NATIVE_CAPACITY
            capacity = NATIVE_CAPACITY if ac4_native else CAPACITY
            info.update(encoder_capacity=capacity, fits_encoder=info['channels'] <= capacity, bitrate=bitrate * 1000, output_codec=codec)
        return info


def mapped(points, ids, radius):
    result = []
    for i, (p, object_id) in enumerate(zip(points, ids)):
        az, el, r = math.radians(p['azimuth']), math.radians(p['elevation']), p['radius'] / radius
        result.append(dict(index=i, name=f'MPEG-H object {object_id}', source_id=object_id,
            x=-r * math.cos(el) * math.sin(az), y=r * math.cos(el) * math.cos(az), z=r * math.sin(el),
            gain=p['gain'], size=max(p['spread_width'] / 180, p['spread_height'] / 90, p['spread_depth'] / 15.5)))
    return result


def decode(input_path, work, metadata=None, start=0, duration=None, mpegh=None,
           cancel_file=None, emit=lambda **_: None):
    if metadata:
        raise ValueError('360RA 使用 MPEG-H 解码得到的空间元数据，请清空外置元数据字段。')
    work = Path(work)
    info = run(input_path, work, mpegh, cancel_file, emit)
    first, length = selection(info, start, duration)
    raw_start = first + info['presentation']['media_start_samples']
    wav, scene = work / 'objects.wav', work / 'objects.scene.jsonl'
    with wave.open(str(wav), 'wb') as dst, (work / 'objects.pcm').open('rb') as src:
        dst.setparams((info['channels'], 3, 48000, 0, 'NONE', 'not compressed'))
        if length * info['channels'] * 3 > 0xffffffff - 36:
            raise ValueError('对象 WAV 超过 RIFF 4 GiB 容量，请分段导出。')
        src.seek(raw_start * info['channels'] * 3)
        remaining = length
        while remaining:
            check_cancel(cancel_file)
            count = min(48000, remaining)
            block = src.read(count * info['channels'] * 3)
            if len(block) != count * info['channels'] * 3:
                raise ValueError('MPEG-H 对象 PCM 截断。')
            dst.writeframesraw(block)
            remaining -= count
    with scene.open('w', encoding='utf-8') as dst:
        previous = None
        for sample, period, ids, points, _ in oam_frames(work / 'oam.jsonl', cancel_file):
            if sample >= raw_start + length:
                break
            if sample + period <= raw_start:
                continue
            objects = mapped(points, ids, info['radius_normalization'])
            if objects != previous:
                dst.write(json.dumps(dict(sample=max(0, sample - raw_start), objects=objects), ensure_ascii=False) + '\n')
                previous = objects
    emit(type='progress', phase='decode', progress=30,
         message='360RA 对象已导出；球坐标／扩散范围映射存在近似，原始 OAM 将另外保存。')
    originals = [(work / 'oam.jsonl', '.source.mpegh.oam.jsonl'), (work / 'oam.bs', '.source.mpegh.oam.bs')]
    attachments = []
    for original, suffix in originals:
        with original.open('rb') as f:
            attachments.append(dict(path=str(original), suffix=suffix, sha256=hashlib.file_digest(f, 'sha256').hexdigest()))
    report = dict(importer='mpegh', input=str(Path(input_path).resolve()), source=info,
        presentation=info['presentation'], presentation_start_samples=first, samples=length,
        pcm_gain_baked=False, metadata_files=attachments)
    return wav, scene, report


def convert_input(input_path, output_path, metadata=None, mpegh=None, emit=lambda **_: None, **kwargs):
    output = Path(output_path).resolve()
    allowed = {'.m4a', '.mp4'} if kwargs.get('codec') == 'ac4' else {'.m4a'}
    if output == Path(input_path).resolve() or output.suffix.lower() not in allowed:
        raise ValueError('输出必须使用受支持的新路径（AC-4：.mp4 / .m4a；Vivid：.m4a），不能覆盖输入。')
    if output.exists() or output.with_suffix('.conversion.json').exists():
        raise ValueError('输出或报告文件已存在，请选择新文件名。')
    with tempfile.TemporaryDirectory(prefix='av3s-mpegh-') as temp:
        wav, scene, report = decode(input_path, temp, metadata, kwargs.pop('start', 0),
            kwargs.pop('duration', None), mpegh, kwargs.get('cancel_file'), emit)
        if not kwargs.get('tags') and is_container(input_path):
            try:
                copy_tags(input_path)
            except ValueError as ex:
                if '没有 udta' not in str(ex):
                    raise
            else:
                kwargs['tags'] = input_path
        def forward(**event):
            if event.get('type') == 'progress':
                event['progress'] = 30 + .7 * event.get('progress', 0)
            emit(**event)
        return convert(wav, output, scene, source_context=report, emit=forward, **kwargs)


def export_input(input_path, output_path, metadata=None, mpegh=None, start=0, duration=None,
                 cancel_file=None, emit=lambda **_: None):
    output = Path(output_path).resolve()
    targets = [output, output.with_suffix('.scene.jsonl'), output.with_suffix('.export.json'),
               output.with_suffix('.source.mpegh.oam.jsonl'), output.with_suffix('.source.mpegh.oam.bs')]
    if output.suffix.lower() != '.wav':
        raise ValueError('对象导出扩展名必须是 .wav。')
    if any(p.exists() or p == Path(input_path).resolve() for p in targets):
        raise ValueError('导出文件或附件已存在，请选择新的文件名。')
    with tempfile.TemporaryDirectory(prefix='av3s-mpegh-') as temp:
        wav, scene, report = decode(input_path, temp, metadata, start, duration, mpegh, cancel_file, emit)
        output.parent.mkdir(parents=True, exist_ok=True)
        published = []
        try:
            report['metadata_files'][0]['path'] = str(targets[3])
            report['metadata_files'][1]['path'] = str(targets[4])
            for src, dst in [(wav, targets[0]), (scene, targets[1]),
                             (Path(temp) / 'oam.jsonl', targets[3]), (Path(temp) / 'oam.bs', targets[4])]:
                check_cancel(cancel_file)
                with src.open('rb') as f, dst.open('xb') as out:
                    published.append(dst)
                    shutil.copyfileobj(f, out)
            with targets[2].open('x', encoding='utf-8') as f:
                published.append(targets[2])
                json.dump(report, f, ensure_ascii=False, indent=2)
            check_cancel(cancel_file)
        except BaseException:
            for p in published:
                p.unlink(missing_ok=True)
            raise
    emit(type='export_complete', progress=100, message='360RA 对象 WAV、空间元数据、原始 OAM 和报告已保存。', output=str(output))
    return report
