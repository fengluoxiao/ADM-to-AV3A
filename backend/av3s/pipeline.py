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
from .metadata import static_metadata, frame_metadata
from .mp4 import copy_tags, validate_stream, mux


class Cancelled(Exception):
    pass


def inspect(input_path, metadata=None):
    wave = Wave.open(input_path)
    scene = load_scene(wave, metadata)
    return wave, scene, dict(channels=wave.channels, objects=scene.objects, lfe=scene.lfe,
                             rate=wave.rate, samples=wave.samples, seconds=wave.samples / wave.rate,
                             metadata_updates=len(scene.times), names=scene.names,
                             mode='Mixed / ObjectsOnly', bitrate=wave.channels * 96000)


def convert(input_path, output_path, metadata=None, tags=None, encoder=None, start=0,
            duration=None, cancel_file=None, emit=lambda **_: None):
    wave, scene, info = inspect(input_path, metadata)
    output = Path(output_path).resolve()
    protected = [Path(p).resolve() for p in (input_path, metadata, tags) if p]
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
    encoder = Path(encoder or os.environ.get('AV3S_ENCODER', Path(__file__).resolve().parents[2] / '.tools/bin/avs3Encoder.exe')).resolve()
    if not encoder.is_file():
        raise ValueError('找不到参考编码器，请运行 scripts/bootstrap.ps1 或指定编码器路径。')
    udta = copy_tags(tags)

    def check():
        if cancel_file and Path(cancel_file).exists():
            raise Cancelled('已取消转换。')

    output.parent.mkdir(parents=True, exist_ok=True)
    # ASCII temporary paths accommodate the reference codec's narrow fopen API.
    with tempfile.TemporaryDirectory(prefix='av3s-') as temp:
        work = Path(temp)
        pcm, meta, stream = [work / name for name in ('input.pcm', 'metadata.bin', 'encoded.av3a')]
        frames = math.ceil((samples + 2048) / 1024)
        input_samples = frames * 1024 + 512
        static = static_metadata(scene)
        emit(type='progress', phase='prepare', progress=0, message='准备对象 PCM 与空间元数据')
        with meta.open('wb') as f:
            for frame in range(frames):
                if frame % 128 == 0:
                    check()
                points = scene.at(min(first + max(0, frame * 1024 - 1024), wave.samples - 1))
                f.write(frame_metadata(scene, points, static))
        clipped = 0
        rng = np.random.default_rng(20261020)
        with pcm.open('wb') as f:
            for begin in range(0, input_samples, 48000):
                check()
                n = min(48000, input_samples - begin)
                active = max(0, min(n, samples - begin))
                block = np.zeros((n, wave.channels), dtype=np.float32)
                if active:
                    block[:active] = wave.read(first + begin, active)
                    if scene.lfe is not None:
                        gains = np.array([scene.at(first + begin + j)[scene.lfe].gain for j in range(active)], dtype=np.float32)
                        block[:active, scene.lfe] *= gains
                    clipped += int(np.count_nonzero((block[:active] >= 1) | (block[:active] < -1)))
                    if clipped:
                        raise ValueError('PCM 超出 16-bit 编码范围；请先降低母版增益，避免削波。')
                    block[:active] += (rng.random((active, wave.channels), dtype=np.float32) - rng.random((active, wave.channels), dtype=np.float32)) / 32768
                f.write(np.clip(np.rint(block * 32768), -32768, 32767).astype('<i2').tobytes())
                emit(type='progress', phase='prepare', progress=round(20 * (begin + n) / input_samples, 1), message='正在准备音频')
        check()
        args = [str(encoder), '-nn_type', '1', '-bitdepth', '16', '-mix', '0', str(wave.channels),
                '96000', '-meta_file', meta.name, '0', '48', pcm.name, stream.name]
        emit(type='progress', phase='encode', progress=20, message='编码 Audio Vivid 对象音频')
        with (work / 'encoder.log').open('wb') as log:
            proc = subprocess.Popen(args, cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                last = -1
                while proc.poll() is None:
                    check()
                    done = min(frames, stream.stat().st_size // (wave.channels * 256)) if stream.exists() else 0
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
        validation = validate_stream(stream, wave.channels, frames, check)
        staged = work / 'output.m4a'
        validation.update(mux(stream, staged, wave.channels, frames, samples, udta))
        report = dict(version=1, input=str(wave.path.resolve()), output=str(output), source=info,
                      start_samples=first, output_samples=samples, duration_seconds=samples / wave.rate,
                      objects_preserved=scene.objects, lfe_carrier=scene.lfe, pcm_bit_depth=16,
                      metadata_delay_samples=1024, metadata_sampling='1024 samples',
                      quantization='XYZ 8/6/4 bits; extent 7/5/4 bits; gain 7 bits, 0..6',
                      encoder_source='https://github.com/Dreamello/av3a',
                      encoder_commit='b44b5321862385a06ad6c427de5791e18f16c16b',
                      validation=validation)
        source_meta = Path(metadata).read_bytes() if metadata else wave.chunks[b'axml']
        report['source_metadata_sha256'] = hashlib.sha256(source_meta).hexdigest()
        # Preserve original metadata verbatim alongside the converted representation.
        meta_suffix = '.source.scene.jsonl' if metadata and Path(metadata).suffix.lower() == '.jsonl' else '.source.adm.xml'
        meta_path = output.with_suffix(meta_suffix)
        if meta_path.exists() or meta_path in protected:
            raise ValueError('元数据备份路径已存在或与输入冲突。')
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
            for target, content in ((meta_path, source_meta),
                                    (report_path, (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))):
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
        emit(type='complete', progress=100, message='转换完成', output=str(output), report=str(report_path), **validation)
        return report
