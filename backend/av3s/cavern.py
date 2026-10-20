"""Cavern subprocess bridge and presentation-aware JOC import/export."""
import hashlib
import json
import math
import mmap
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
from .mp4 import atoms
from .pipeline import Cancelled, convert, inspect

COMPRESSED = {'.m4a', '.mp4', '.eac3', '.ec3'}
MP4 = {'.m4a', '.mp4'}


def is_compressed(path):
    return Path(path).suffix.lower() in COMPRESSED


def exporter_path(override=None):
    root = Path(__file__).resolve().parents[2]
    paths = [Path(override)] if override else [
        Path(os.environ['AV3S_CAVERN'])] if os.environ.get('AV3S_CAVERN') else [
        root / 'tools/cavern/CavernExport.exe', root / '.tools/cavern-export/CavernExport.exe']
    for path in paths:
        if path.is_file():
            return path.resolve()
    raise ValueError('找不到 Cavern 导出组件，请运行 scripts/build-cavern.ps1 或使用完整便携包。')


def run(command, input_path, output=None, skip=0, samples=None, cavern=None,
        cancel_file=None, emit=lambda **_: None):
    def check():
        if cancel_file and Path(cancel_file).exists():
            raise Cancelled('已取消 Cavern 解码。')
    check()
    args = [str(exporter_path(cavern)), command, str(Path(input_path).resolve())]
    if output:
        args += [str(output), str(skip), str(samples)]
    messages = queue.Queue()
    result, error = None, None
    with tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=stderr, text=True,
                                encoding='utf-8-sig', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def read():
            try:
                for line in proc.stdout:
                    messages.put(line)
            finally:
                messages.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            while True:
                check()
                try:
                    line = messages.get(timeout=.1)
                except queue.Empty:
                    continue
                if line is None:
                    break
                event = json.loads(line)
                kind = event.get('type')
                if kind == 'error':
                    error = event['message']
                elif kind == 'progress':
                    emit(**event)
                elif kind in ('cavern_inspection', 'exported'):
                    result = event
            proc.wait()
            check()
            if proc.returncode or error or result is None:
                stderr.seek(0)
                detail = stderr.read().decode('utf-8', errors='replace')[-2000:]
                raise ValueError(error or detail or f'Cavern 导出失败（{proc.returncode}）。')
            return result
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            reader.join()
            proc.stdout.close()


def presentation(path, raw_samples):
    """Apply a single unit-rate MP4 edit; reject unsupported timeline edits."""
    result = dict(media_start_samples=0, samples=raw_samples, edit_list_applied=False)
    if Path(path).suffix.lower() not in MP4:
        return result
    with open(path, 'rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        def children(atom):
            return list(atoms(data, atom[2], atom[3]))
        def one(parent, kind):
            return next((a for a in children(parent) if a[0] == kind), None)
        moov = next(a for a in atoms(data) if a[0] == b'moov')
        mvhd = one(moov, b'mvhd')
        p = mvhd[2]
        movie_scale = struct.unpack_from('>I', data, p + (20 if data[p] == 1 else 12))[0]
        tracks = []
        for track in children(moov):
            if track[0] != b'trak':
                continue
            mdia = one(track, b'mdia')
            if not mdia:
                continue
            minf = one(mdia, b'minf')
            stbl = one(minf, b'stbl') if minf else None
            stsd = one(stbl, b'stsd') if stbl else None
            if not stsd:
                continue
            entries = list(atoms(data, stsd[2] + 8, stsd[3]))
            if any(a[0] == b'ec-3' for a in entries):
                tracks.append((track, mdia))
        if len(tracks) != 1:
            raise ValueError('M4A/MP4 必须包含恰好一条未加密 ec-3 音轨。')
        track, mdia = tracks[0]
        mdhd = one(mdia, b'mdhd')
        p = mdhd[2]
        media_scale = struct.unpack_from('>I', data, p + (20 if data[p] == 1 else 12))[0]
        if media_scale != 48000:
            raise ValueError('当前 M4A 对象导入要求媒体时间基为 48000。')
        edts = one(track, b'edts')
        elst = one(edts, b'elst') if edts else None
        if elst:
            p = elst[2]
            version = data[p]
            count = struct.unpack_from('>I', data, p + 4)[0]
            if version not in (0, 1) or count != 1:
                raise ValueError('暂不支持多段或空段 MP4 edit list。')
            fmt = '>Qqhh' if version else '>Iihh'
            if p + 8 + struct.calcsize(fmt) > elst[3]:
                raise ValueError('MP4 edit list 截断。')
            duration, start, rate, fraction = struct.unpack_from(fmt, data, p + 8)
            if start < 0 or (rate, fraction) != (1, 0) or not movie_scale:
                raise ValueError('暂不支持 MP4 空白或变速 edit list。')
            length = round(duration * 48000 / movie_scale)
            if length <= 0 or start >= raw_samples:
                raise ValueError('MP4 edit list 没有与实际音轨重叠的有效区间。')
            # Movie ticks can round the duration beyond the last media sample.
            # Some muxers also leave the full track duration after adding a
            # priming edit. Preserve the start and intersect with real media;
            # never synthesize silence or read past the track to fill that tail.
            result.update(media_start_samples=start, samples=min(length, raw_samples - start), edit_list_applied=True)
            if length > raw_samples - start:
                result.update(requested_edit_samples=length,
                              unavailable_tail_samples=length - (raw_samples - start),
                              tail_policy='intersection with available media; no added samples')
    return result


def inspect_input(input_path, metadata=None, cavern=None, cancel_file=None, emit=lambda **_: None):
    if not is_compressed(input_path):
        return inspect(input_path, metadata)[2]
    if metadata:
        raise ValueError('Atmos 输入使用 Cavern 提取的空间元数据，请清空外置元数据字段。')
    info = run('inspect', input_path, cavern=cavern, cancel_file=cancel_file, emit=emit)
    info.pop('type', None)
    timing = presentation(input_path, info['samples'])
    if timing.get('unavailable_tail_samples'):
        emit(type='progress', phase='inspect', progress=0,
             message=f"MP4 时长已对齐实际音轨末尾（未读取超出的 {timing['unavailable_tail_samples']} 个采样）。")
    info.update(samples=timing['samples'], seconds=timing['samples'] / 48000, presentation=timing)
    return info


def selection(info, start, duration):
    if not math.isfinite(start) or start < 0:
        raise ValueError('起始时间必须是非负有限数。')
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError('时长必须大于 0。')
    first = round(start * 48000)
    length = info['samples'] - first
    if duration is not None:
        length = min(length, round(duration * 48000))
    if length <= 0:
        raise ValueError('转换区间超出输入。')
    return first, length


def decode(input_path, work, metadata=None, start=0, duration=None, cavern=None,
           cancel_file=None, emit=lambda **_: None):
    info = inspect_input(input_path, metadata, cavern, cancel_file, emit)
    first, length = selection(info, start, duration)
    wav = Path(work) / 'objects.wav'
    report = run('export', input_path, wav, info['presentation']['media_start_samples'] + first,
                 length, cavern, cancel_file, emit)
    report.update(input=str(Path(input_path).resolve()), presentation=info['presentation'],
                  presentation_start_samples=first, original_samples=info['samples'])
    if report['source']['sparse_workaround']:
        emit(type='progress', phase='decode', progress=30,
             message='Cavern 检测到稀疏 JOC，已使用上游兼容处理；对象还原可能存在近似，详情已记入报告。')
    return wav, wav.with_suffix('.scene.jsonl'), report


def convert_input(input_path, output_path, metadata=None, cavern=None, emit=lambda **_: None, **kwargs):
    if not is_compressed(input_path):
        return convert(input_path, output_path, metadata, emit=emit, **kwargs)
    output = Path(output_path).resolve()
    if output == Path(input_path).resolve() or output.suffix.lower() != '.m4a':
        raise ValueError('输出必须是新的 .m4a 路径，不能覆盖输入。')
    if output.exists() or output.with_suffix('.conversion.json').exists():
        raise ValueError('输出或报告文件已存在，请选择新文件名。')
    with tempfile.TemporaryDirectory(prefix='av3s-cavern-') as temp:
        start, duration = kwargs.pop('start', 0), kwargs.pop('duration', None)
        wav, scene, report = decode(input_path, temp, metadata, start, duration, cavern,
                                    kwargs.get('cancel_file'), emit)
        # Copy the source container's tags automatically when available.
        if not kwargs.get('tags') and Path(input_path).suffix.lower() in MP4:
            from .mp4 import copy_tags
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


def export_input(input_path, output_path, metadata=None, start=0, duration=None, cavern=None,
                 cancel_file=None, emit=lambda **_: None):
    if not is_compressed(input_path):
        raise ValueError('Cavern 对象导出接受 Atmos M4A/MP4 或 E-AC-3 JOC 裸流。')
    output = Path(output_path).resolve()
    if output.suffix.lower() != '.wav':
        raise ValueError('Cavern 导出扩展名必须是 .wav。')
    targets = [output, output.with_suffix('.scene.jsonl'), output.with_suffix('.export.json')]
    if any(p.exists() or p == Path(input_path).resolve() for p in targets):
        raise ValueError('导出文件或附件已存在，请选择新的文件名。')
    with tempfile.TemporaryDirectory(prefix='av3s-export-') as temp:
        wav, scene, report = decode(input_path, temp, metadata, start, duration, cavern, cancel_file, emit)
        output.parent.mkdir(parents=True, exist_ok=True)
        published = []
        try:
            report['files'] = []
            for target, source in zip(targets, (wav, scene)):
                with source.open('rb') as f:
                    report['files'].append(dict(path=str(target), sha256=hashlib.file_digest(f, 'sha256').hexdigest()))
            for target, source in zip(targets, (wav, scene)):
                if cancel_file and Path(cancel_file).exists():
                    raise Cancelled('已取消 Cavern 导出。')
                with target.open('xb') as dst, source.open('rb') as src:
                    published.append(target)
                    shutil.copyfileobj(src, dst)
            with targets[2].open('x', encoding='utf-8') as f:
                published.append(targets[2])
                json.dump(report, f, ensure_ascii=False, indent=2)
            if cancel_file and Path(cancel_file).exists():
                raise Cancelled('已取消 Cavern 导出。')
        except BaseException:
            for path in published:
                path.unlink(missing_ok=True)
            raise
    emit(type='export_complete', progress=100, message='Cavern 对象 WAV、空间元数据与导出报告已保存。', output=str(output))
    return report
