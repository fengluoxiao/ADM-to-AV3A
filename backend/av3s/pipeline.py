"""Conversion transaction. Only publish a completed, CRC-checked M4A."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import numpy as np
from .waveio import Wave
from .scene import load_scene
from .metadata import static_metadata, frame_metadata, bed_layout
from .mp4 import copy_tags, validate_stream, mux
from .reduction import cluster


class Cancelled(Exception):
    pass


def inspect(input_path, metadata=None, codec="av3a", bitrate=768, ac4_native=False):
    wave = Wave.open(input_path)
    scene = load_scene(wave, metadata)
    if codec not in ("av3a", "ac4"):
        raise ValueError("不支持的输出编码格式。")
    layout = bed_layout(scene)
    from .ac4 import CAPACITY, NATIVE_CAPACITY
    capacity = (NATIVE_CAPACITY if ac4_native else CAPACITY) if codec == "ac4" else 16
    return wave, scene, dict(channels=wave.channels, objects=scene.objects, lfe=scene.lfe,
                             bed_channels=len(scene.bed), bed_speakers=scene.bed,
                             encoder_capacity=capacity, fits_encoder=wave.channels <= capacity, output_codec=codec,
                             bed_layout=layout[1] if layout else ('LFE' if scene.lfe is not None else None),
                             rate=wave.rate, samples=wave.samples, seconds=wave.samples / wave.rate,
                             metadata_updates=len(scene.times), names=scene.names,
                             mode='AC-4 A-JOC' if codec == 'ac4' else 'Mixed / ObjectsOnly',
                             bitrate=bitrate * 1000 if codec == 'ac4' else wave.channels * 96000)


def convert(input_path, output_path, metadata=None, tags=None, encoder=None, start=0,
            duration=None, cancel_file=None, spatial_reduction=False, source_context=None, emit=lambda **_: None,
            codec="av3a", bitrate=768, ac4_encoder=None, ac4_native=False):
    wave, scene, info = inspect(input_path, metadata, codec, bitrate, ac4_native)
    capacity = info["encoder_capacity"]
    if codec == "ac4" and (type(bitrate) is not int or not 128 <= bitrate <= 1500):
        raise ValueError("AC-4 码率必须为 128–1500 kbps 的整数。")
    if wave.channels > capacity and not spatial_reduction:
        raise ValueError(f'已读取 {wave.channels} 路音频：{len(scene.bed)} 路声床＋{scene.objects} 路对象。'
                         f'当前输出编码器最多支持 {capacity} 路；'
                         '如接受合并对象和位置近似，请明确开启 --spatial-reduction。')
    output = Path(output_path).resolve()
    protected = [Path(p).resolve() for p in (input_path, metadata, tags) if p]
    if source_context:
        protected.append(Path(source_context['input']).resolve())
    if output in protected:
        raise ValueError('输出路径不能覆盖输入文件。')
    if output.suffix.lower() != '.m4a':
        raise ValueError('输出扩展名必须是 .m4a。')
    report_path = output.with_suffix('.conversion.json')
    if output.exists() or report_path.exists():
        raise ValueError('输出或报告文件已存在，请选择新文件名。')
    if not math.isfinite(start) or start < 0:
        raise ValueError('起始时间必须是非负有限数。')
    first = round(start * wave.rate)
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError('时长必须大于 0。')
    samples = wave.samples - first if duration is None else min(wave.samples - first, round(duration * wave.rate))
    if first >= wave.samples or samples <= 0:
        raise ValueError('转换区间超出 WAV。')
    if codec == "ac4":
        from .ac4 import encoder_path
        encoder = encoder_path(ac4_encoder)
    else:
        encoder = Path(encoder or os.environ.get('AV3S_ENCODER', Path(__file__).resolve().parents[2] / '.tools/bin/avs3Encoder.exe')).resolve()
        if not encoder.is_file():
            raise ValueError('找不到参考编码器，请运行 scripts/bootstrap.ps1 或指定编码器路径。')
    udta = copy_tags(tags)
    source_meta = Path(metadata).read_bytes() if metadata else wave.chunks[b'axml']
    meta_suffix = '.source.scene.jsonl' if metadata and Path(metadata).suffix.lower() == '.jsonl' else '.source.adm.xml'
    backups = [(output.with_suffix(meta_suffix), source_meta)]
    if metadata and b'axml' in wave.chunks:
        suffix = '.source.embedded.adm.xml' if meta_suffix == '.source.adm.xml' else '.source.adm.xml'
        backups.append((output.with_suffix(suffix), wave.chunks[b'axml']))
    if source_context:
        source_context = dict(source_context)
        imported_files = []
        for attachment in source_context.get('metadata_files', []):
            attachment = dict(attachment)
            content = Path(attachment['path']).read_bytes()
            if hashlib.sha256(content).hexdigest() != attachment['sha256']:
                raise ValueError('导入元数据校验失败。')
            target = output.with_suffix(attachment['suffix'])
            backups.append((target, content))
            attachment['path'] = str(target)
            imported_files.append(attachment)
        if imported_files:
            source_context['metadata_files'] = imported_files
        importer = source_context.get('importer', 'cavern')
        backups.append((output.with_suffix(f'.source.{importer}.json'),
                        (json.dumps(source_context, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))
    for kind in (b'chna', b'dbmd', b'bext', b'iXML', b'LIST'):
        if kind in wave.chunks:
            backups.append((output.with_suffix('.source.' + kind.decode('ascii') + '.bin'), wave.chunks[kind]))
    if any(path.exists() or path in protected for path, _ in backups):
        raise ValueError('元数据备份路径已存在或与输入冲突。')

    def check():
        if cancel_file and Path(cancel_file).exists():
            raise Cancelled('已取消转换。')

    output.parent.mkdir(parents=True, exist_ok=True)
    # ASCII temporary paths accommodate the reference codec's narrow fopen API.
    with tempfile.TemporaryDirectory(prefix='av3s-') as temp:
        work = Path(temp)
        pcm, meta, stream = [work / name for name in ('input.pcm', 'metadata.bin', 'encoded.av3a')]
        reduction = None
        read_pcm = wave.read
        if wave.channels > capacity:
            emit(type='progress', phase='analyse', progress=0,
                 message=f'JOCForge 聚类：{wave.channels - int(scene.lfe is not None)} 路非 LFE 音频缩减为最多 {capacity - 1} 个对象；声床参与聚类，位置与混合增益会变化')
            scene, read_pcm, reduction = cluster(wave, scene, first, samples, check, emit, capacity=capacity)
        bed_mapping = []
        if codec == 'ac4' and not ac4_native:
            from .ac4 import compatible_scene
            scene, bed_mapping = compatible_scene(scene)
        attachments = []
        if reduction:
            timeline_path = output.with_suffix('.reduction.npz')
            if timeline_path.exists() or timeline_path in protected:
                raise ValueError('聚类时间线文件已存在或与输入冲突。')
            timeline_temp = work / 'reduction.npz'
            np.savez_compressed(timeline_temp, **reduction.pop('_timeline'))
            timeline_bytes = timeline_temp.read_bytes()
            attachments.append((timeline_path, timeline_bytes))
            reduction['timeline'] = dict(path=str(timeline_path), sha256=hashlib.sha256(timeline_bytes).hexdigest(),
                                         assignment_index='zero-based cluster index; add LFE carrier count for physical output index',
                                         gains='target gains after source ADM gain; interpolate prior-to-current over transition_crossfade_samples; then apply uniform_gain')
        channels = scene.channels
        details = {}
        if codec == 'ac4':
            from .ac4 import encode
            staged, validation, details = encode(wave, scene, read_pcm, first, samples, work,
                                                 encoder, bitrate, udta, check, emit)
            details.update(ac4_profile='native' if ac4_native else 'compatible objects',
                           bed_object_mapping=bed_mapping,
                           objects_preserved=info['objects'] if reduction is None else 0)
            if reduction:
                reduction.update(pcm_peak_before_headroom=details['pcm_peak_before_headroom'],
                                 uniform_gain=details['uniform_gain'], uniform_gain_db=details['uniform_gain_db'])
        else:
            frames = math.ceil((samples + 2048) / 1024)
            input_samples = frames * 1024 + 512
            static = static_metadata(scene)
            emit(type='progress', phase='prepare', progress=10 if reduction else 0, message='准备对象 PCM 与空间元数据')
            with meta.open('wb') as f:
                for frame in range(frames):
                    if frame % 128 == 0:
                        check()
                    points = scene.at(min(first + max(0, frame * 1024 - 1024), wave.samples - 1))
                    f.write(frame_metadata(scene, points, static))
            peak = 0
            rng = np.random.default_rng(20261020)
            float_pcm = work / 'input.f32'
            with float_pcm.open('wb') as f:
                for begin in range(0, input_samples, 48000):
                    check()
                    n = min(48000, input_samples - begin)
                    active = max(0, min(n, samples - begin))
                    block = np.zeros((n, channels), dtype=np.float32)
                    if active:
                        block[:active] = read_pcm(first + begin, active)
                        bed_carriers = list(scene.bed) or ([scene.lfe] if scene.lfe is not None else [])
                        if bed_carriers:
                            gains = scene.pcm_gains(first + begin, active, bed_carriers)
                            block[:active, bed_carriers] *= gains
                        if not np.isfinite(block).all():
                            raise ValueError('混合 PCM 含 NaN 或无穷大。')
                        peak = max(peak, float(np.abs(block[:active]).max()))
                        if not reduction and np.any((block[:active] >= 1) | (block[:active] < -1)):
                            raise ValueError('PCM 超出 16-bit 编码范围；请先降低母版增益，避免削波。')
                    f.write(block.astype('<f4').tobytes())
                    emit(type='progress', phase='prepare', progress=round(10 + 8 * (begin + n) / input_samples, 1), message='正在准备音频')
            scale = min(1, (32766 / 32768) / peak) if reduction and peak else 1
            with float_pcm.open('rb') as src, pcm.open('wb') as dst:
                for begin in range(0, input_samples, 48000):
                    check()
                    n = min(48000, input_samples - begin)
                    active = max(0, min(n, samples - begin))
                    block = np.frombuffer(src.read(n * channels * 4), dtype='<f4').copy().reshape(n, channels)
                    block *= scale
                    if active:
                        block[:active] += (rng.random((active, channels), dtype=np.float32) - rng.random((active, channels), dtype=np.float32)) / 32768
                    dst.write(np.clip(np.rint(block * 32768), -32768, 32767).astype('<i2').tobytes())
            float_pcm.unlink()
            if reduction:
                reduction.update(pcm_peak_before_headroom=peak, uniform_gain=scale,
                                 uniform_gain_db=20 * math.log10(scale))
            check()
            args = [str(encoder), '-nn_type', '1', '-bitdepth', '16', '-mix', '0', str(channels),
                    '96000', '-meta_file', meta.name, '0', '48', pcm.name, stream.name]
            emit(type='progress', phase='encode', progress=20, message='编码 Audio Vivid 对象音频')
            with (work / 'encoder.log').open('wb') as log:
                proc = subprocess.Popen(args, cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                try:
                    last = -1
                    while proc.poll() is None:
                        check()
                        done = min(frames, stream.stat().st_size // (channels * 256)) if stream.exists() else 0
                        percent = int(20 + 70 * done / frames)
                        if percent != last:
                            emit(type='progress', phase='encode', progress=percent, message=f'编码 {done} / {frames} 帧')
                            last = percent
                        time.sleep(.2)
                    if proc.returncode:
                        raise ValueError('参考编码器失败：' + (work / 'encoder.log').read_bytes()[-1800:].decode('utf-8', 'replace'))
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
            emit(type='progress', phase='verify', progress=92, message='逐帧校验 CRC 并封装 M4A')
            check()
            validation = validate_stream(stream, channels, frames, check)
            staged = work / 'output.m4a'
            validation.update(mux(stream, staged, channels, frames, samples, udta))
        report = dict(version=1, input=str(wave.path.resolve()), output=str(output), source=info,
                      start_samples=first, output_samples=samples, duration_seconds=samples / wave.rate,
                      objects_preserved=scene.objects if reduction is None else 0,
                      encoded_objects=scene.objects, encoded_channels=channels,
                      spatial_reduction=reduction, bed_channels_preserved=len(scene.bed),
                      bed_layout=info['bed_layout'] if reduction is None and not bed_mapping else ('LFE' if scene.lfe is not None else None), bed_speakers=scene.bed,
                      lfe_carrier=scene.lfe, pcm_bit_depth=16,
                      metadata_delay_samples=1024, metadata_sampling='1024 samples',
                      quantization='XYZ 8/6/4 bits; extent 7/5/4 bits; gain 7 bits, 0..6',
                      encoder_source='https://github.com/Dreamello/av3a',
                      encoder_commit='b44b5321862385a06ad6c427de5791e18f16c16b',
                      validation=validation)
        report.update(output_codec=codec, **details)
        if source_context:
            report.update(input=source_context['input'], start_samples=source_context['presentation_start_samples'])
            report[source_context.get('importer', 'cavern') + '_import'] = source_context
        report['source_metadata_sha256'] = hashlib.sha256(source_meta).hexdigest()
        report['metadata_backups'] = [dict(path=str(path), sha256=hashlib.sha256(content).hexdigest())
                                     for path, content in backups]
        check()
        published = []
        fd, partial_name = tempfile.mkstemp(prefix='.av3s-', suffix='.partial', dir=output.parent)
        partial = Path(partial_name)
        try:
            # Finish the copy on the destination volume before making the M4A visible.
            with os.fdopen(fd, 'wb') as f, staged.open('rb') as src:
                shutil.copyfileobj(src, f, 1024 * 1024)
                f.flush()
                os.fsync(f.fileno())
            check()
            # Exclusive creation prevents overwriting a file created during encoding.
            for target, content in backups + attachments + [(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))]:
                with target.open('xb') as f:
                    published.append(target)
                    f.write(content)
            if os.name == 'nt':
                os.rename(partial, output)  # Windows rename fails if the destination exists.
            else:
                os.link(partial, output)  # POSIX rename would replace an existing file.
            published.append(output)
        except BaseException:
            for p in reversed(published):
                p.unlink(missing_ok=True)
            raise
        finally:
            partial.unlink(missing_ok=True)
        emit(type='complete', progress=100, message='转换完成', output=str(output), report=str(report_path),
             spatial_reduction=bool(reduction), encoded_objects=scene.objects, output_codec=codec,
             experimental=(codec == 'ac4'),
             bed_channels=len(scene.bed), **validation)
        return report
