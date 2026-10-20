"""Streaming ICL Forge A-JOC bridge; upstream codec sources are unmodified."""
import hashlib
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess
import time
import numpy as np
from .mp4 import mux

COMMIT = '315899937e5c337ab34e3e27471539b9810e3b5f'
# The published independent decoder supports <=20 fullband upmix signals,
# dynamic object assignments and an LFE. Native mode retains upstream limits.
CAPACITY = 20
NATIVE_CAPACITY = 64
METADATA_STEP = 256
BED = {'L': 0, 'R': 1, 'C': 2, 'Ls': 4, 'Rs': 5, 'Lss': 4, 'Rss': 5,
       'Lrs': 6, 'Rrs': 7, 'Lts': 10, 'Rts': 11}
BED_POSITIONS = {'L': (-1, 1, 0), 'R': (1, 1, 0), 'C': (0, 1, 0),
                 'Ls': (-1, -1, 0), 'Rs': (1, -1, 0),
                 'Lss': (-1, 0, 0), 'Rss': (1, 0, 0),
                 'Lrs': (-1, -1, 0), 'Rrs': (1, -1, 0),
                 'Lts': (-1, 0, 1), 'Rts': (1, 0, 1)}


def compatible_scene(scene):
    """Represent non-LFE bed roles as fixed objects for published decoders."""
    mapped = {i: label for i, label in scene.bed.items() if i != scene.lfe}
    if not mapped:
        return scene, []
    for label in mapped.values():
        if label not in BED_POSITIONS:
            raise ValueError(f'AC-4 兼容模式暂不支持声床扬声器 {label}。')
    def evaluate(sample):
        points = list(scene.at(sample))
        for i, label in mapped.items():
            x, y, z = BED_POSITIONS[label]
            points[i] = replace(points[i], x=x, y=y, z=z, size=0)
        return points
    converted = replace(scene, evaluator=evaluate,
                        bed={scene.lfe: 'LFE'} if scene.lfe is not None else {})
    return converted, [dict(carrier=i, source_speaker=label, target='fixed positional object',
                            adm_position=list(BED_POSITIONS[label]), approximate=True)
                       for i, label in mapped.items()]


def encoder_path(override=None):
    root = Path(__file__).resolve().parents[2]
    candidates = [Path(override)] if override else [Path(os.environ['AV3S_AC4_ENCODER'])] if os.environ.get('AV3S_AC4_ENCODER') else [
        root / 'tools/iclforge/Ac4Encode.exe', root / '.tools/iclforge-bin/Ac4Encode.exe']
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError('找不到 AC-4 编码组件，请运行 scripts/build-iclforge.ps1 或使用完整便携包。')


def properties(point):
    # ADM X=right/Y=front. AC-4 X=right from left wall, Y=back from front wall.
    return dict(position=[(point.x + 1) / 2, (1 - point.y) / 2, point.z],
                width=[point.size] * 3)


def prepare_scene(scene, first, samples, config, timeline, bitrate, check, compatible=True):
    objects = []
    for i, point in enumerate(scene.at(first)):
        item = properties(point)
        if i == scene.lfe:
            item['lfe'] = True
        elif i in scene.bed:
            if scene.bed[i] not in BED:
                raise ValueError(f'AC-4 暂不支持声床扬声器 {scene.bed[i]}。')
            item['bed'] = BED[scene.bed[i]]
        objects.append(item)
    config.write_text(json.dumps(dict(channels=scene.channels, samples=samples,
        bitrate_kbps=bitrate, compatible=compatible, objects=objects)), encoding='ascii')
    previous = [properties(p) for p in scene.at(first)]
    updates, error_squared, maximum = 0, 0., 0.
    with timeline.open('w', encoding='ascii') as dst:
        for sample in range(0, samples, METADATA_STEP):
            check()
            target = min(sample + METADATA_STEP, samples - 1)
            points = scene.at(first + target)
            for i, point in enumerate(points):
                if i in scene.bed or i == scene.lfe:
                    continue
                value = properties(point)
                x, y, z = value['position']
                error = (2 * (round(x * 62) / 62 - x)) ** 2 + (2 * (round(y * 62) / 62 - y)) ** 2 + (round(z * 15) / 15 - z) ** 2
                error_squared += error
                maximum = max(maximum, math.sqrt(error))
                if value != previous[i]:
                    dst.write(json.dumps(dict(sample=sample, object=i, ramp=target - sample, **value)) + '\n')
                    updates += 1
                    previous[i] = value
    return dict(updates=updates, sampling_samples=METADATA_STEP,
                position_quantization_rms=math.sqrt(error_squared / max(1, math.ceil(samples / METADATA_STEP) * scene.objects)),
                position_quantization_maximum=maximum,
                position_error_scope='sampled dynamic objects; metadata quantization only, excluding temporal interpolation and A-JOC audio reconstruction')


def crc_table():
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x8005 if crc & 0x8000 else 0)) & 0xffff
        table.append(crc)
    return table


CRC_TABLE = crc_table()


def crc16(data):
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ CRC_TABLE[(crc >> 8) ^ byte]) & 0xffff
    return crc


def unpack_stream(stream, packets, check):
    """Check every Annex G sync-frame CRC before stripping framing for BMFF."""
    sizes, digest = [], hashlib.sha256()
    with stream.open('rb') as src, packets.open('wb') as dst:
        while head := src.read(4):
            check()
            if len(head) != 4 or head[:2] != b'\xac\x41':
                raise ValueError('AC-4 帧同步或 CRC 标志错误。')
            size = int.from_bytes(head[2:], 'big')
            length = head[2:]
            if size == 0xffff:
                extra = src.read(3)
                if len(extra) != 3:
                    raise ValueError('AC-4 扩展长度截断。')
                length += extra
                size = int.from_bytes(extra, 'big')
            if not 0 < size <= 1024 * 1024:
                raise ValueError('AC-4 帧长度无效。')
            raw, checksum = src.read(size), src.read(2)
            if len(raw) != size or len(checksum) != 2:
                raise ValueError('AC-4 帧截断。')
            if crc16(length + raw) != int.from_bytes(checksum, 'big'):
                raise ValueError(f'AC-4 第 {len(sizes)} 帧 CRC 错误。')
            dst.write(raw)
            digest.update(raw)
            sizes.append(size)
    if not sizes:
        raise ValueError('AC-4 编码器未输出音频。')
    return sizes, dict(frames=len(sizes), crc_failures=0, elementary_sha256=digest.hexdigest())


def encode(wave, scene, read_pcm, first, samples, work, encoder, bitrate, udta, check, emit, compatible=True):
    config, timeline, pcm, stream, receipt = [work / p for p in
        ('ac4-config.json', 'ac4-scene.jsonl', 'ac4-input.f32', 'output.ac4', 'ac4-result.json')]
    emit(type='progress', phase='prepare', progress=10, message='AC-4：准备对象音频与空间元数据')
    metadata = prepare_scene(scene, first, samples, config, timeline, bitrate, check, compatible)
    peak = 0.
    carriers = list(range(scene.channels))
    with pcm.open('wb') as dst:
        for begin in range(0, samples, 48000):
            check()
            count = min(48000, samples - begin)
            block = read_pcm(first + begin, count)
            block *= scene.pcm_gains(first + begin, count, carriers)
            if not np.isfinite(block).all():
                raise ValueError('AC-4 输入 PCM 含非有限数值。')
            peak = max(peak, float(np.abs(block).max()))
            dst.write(block.astype('<f4').tobytes())
            emit(type='progress', phase='prepare', progress=round(10 + 10 * (begin + count) / samples, 1), message='AC-4：写入 Float32 对象音频与源增益')
    scale = min(1., .999 / peak) if peak else 1.
    if scale < 1:
        with pcm.open('r+b') as f:
            while raw := f.read(1024 * 1024):
                check()
                block = np.frombuffer(raw, dtype='<f4') * scale
                f.seek(-len(raw), 1)
                f.write(block.astype('<f4').tobytes())
    args = [str(encoder), config.name, pcm.name, timeline.name, stream.name, receipt.name]
    emit(type='progress', phase='encode', progress=20, message='ICL Forge：编码 AC-4 A-JOC 空间对象')
    log_path = work / 'ac4-encoder.log'
    with log_path.open('w+b') as log:
        proc = subprocess.Popen(args, cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            last_emit = 0
            while proc.poll() is None:
                check()
                now = time.monotonic()
                if now - last_emit >= 2:
                    # Adapter reports samples consumed, with bounded memory regardless of duration.
                    with log_path.open('rb') as reader:
                        reader.seek(max(0, log_path.stat().st_size - 4096))
                        lines = reader.read().decode('utf-8', 'replace').splitlines()
                    for line in reversed(lines):
                        try:
                            done = json.loads(line)['samples']
                        except (ValueError, KeyError, TypeError):
                            continue
                        emit(type='progress', phase='encode', progress=round(20 + 65 * done / samples, 1), message=f'AC-4：编码 {done} / {samples} 个采样')
                        break
                    last_emit = now
                time.sleep(.1)
            check()
            if proc.returncode:
                raise ValueError('AC-4 编码失败：' + log_path.read_bytes()[-2000:].decode('utf-8', 'replace'))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
    check()
    result = json.loads(receipt.read_text(encoding='utf-8'))
    emit(type='progress', phase='verify', progress=90, message='AC-4：逐帧检查对象数量、CRC，写入 dac4 与 MP4 时间线')
    packets = work / 'ac4-packets.bin'
    sizes, validation = unpack_stream(stream, packets, check)
    if len(sizes) != result['frames'] or result['b_static_dmx'] is not False:
        raise ValueError('AC-4 输出未满足 A-JOC 对象配置。')
    topology = result.get('topology')
    if not topology:
        raise ValueError('AC-4 编码组件过旧，未返回实际码流对象数量；请更新完整便携包或重新构建组件。')
    if (topology['fullband_upmix_signals'] != scene.channels - int(scene.lfe is not None)
            or topology['lfe'] != (scene.lfe is not None)):
        raise ValueError('AC-4 实际码流对象数量或 LFE 标记与输入不符。')
    sda_structure = 1 <= topology['fullband_upmix_signals'] <= 20 and not topology['bed_signals'] and not topology['isf_signals']
    if compatible and not sda_structure:
        raise ValueError('AC-4 码流超出 SDA 的结构限制；全频重建信号须不超过 20 路，且不能使用原生声床。')
    delay = result['encoder_delay_samples'] + result['decoder_delay_samples']
    staged = work / 'output.m4a'
    validation.update(mux(packets, staged, scene.channels, len(sizes), samples, udta,
        ac4=dict(delay=delay, bitrate=bitrate * 1000, dac4=bytes(result['dac4']),
                 sizes=sizes, sync_samples=result['sync_samples'])))
    validation.update(codec_string=result['codec_string'], b_static_dmx=False,
                      ac4_topology=topology, topology_checked_frames=len(sizes),
                      sda_structure_compatible=sda_structure)
    binary_hash = hashlib.sha256(encoder.read_bytes()).hexdigest()
    manifest_path = encoder.with_name('build.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig')) if manifest_path.is_file() else {}
    identified = manifest.get('adapter_sha256') == binary_hash
    details = dict(encoder_source='https://github.com/iainchesworthlabs/iclforge',
        encoder_commit=manifest.get('commit') if identified else None, encoder_sha256=binary_hash,
        encoder_build_identified=identified, codec_mode='ASPX',
        experimental=True, audio_reconstruction_lossy=True, independent_decoder_verified=False,
        mode='A-JOC computed object downmix', pcm_bit_depth=32, pcm_format='IEEE Float32',
        source_gain_baked=True, pcm_peak_before_headroom=peak, uniform_gain=scale,
        uniform_gain_db=20 * math.log10(scale), metadata_delay_samples=result['encoder_delay_samples'],
        decoder_delay_samples=result['decoder_delay_samples'], metadata_sampling=f'{METADATA_STEP} samples with linear ramps',
        quantization='AC-4 Annex F: X/Y 1/62, signed Z 1/15, size 1/31; source gain baked into PCM',
        metadata_mapping=metadata)
    return staged, validation, details
