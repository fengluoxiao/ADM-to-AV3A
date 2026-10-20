"""Bounded, streaming RIFF/RF64/BW64 reader, including trailing ADM chunks."""
from dataclasses import dataclass, field
from pathlib import Path
import struct
import numpy as np


@dataclass
class Wave:
    path: Path
    channels: int = 0
    rate: int = 0
    bits: int = 0
    format: int = 0
    offset: int = 0
    size: int = 0
    chunks: dict = field(default_factory=dict)

    @classmethod
    def open(cls, path):
        w = cls(Path(path))
        length = w.path.stat().st_size
        with w.path.open('rb') as f:
            head = f.read(12)
            if len(head) != 12 or head[:4] not in (b'RIFF', b'RF64', b'BW64') or head[8:] != b'WAVE':
                raise ValueError('输入必须是 RIFF / RF64 / BW64 WAV 文件。')
            ds_data = None
            ds_sizes = {}
            while f.tell() + 8 <= length:
                kind, size = struct.unpack('<4sI', f.read(8))
                pos = f.tell()
                if size == 0xffffffff:
                    size = ds_data if kind == b'data' else ds_sizes.get(kind)
                    if size is None:
                        raise ValueError('RF64/BW64 缺少有效 ds64 长度。')
                if pos + size > length:
                    raise ValueError(f'WAV 数据块截断：{kind!r}')
                if kind == b'ds64':
                    if size < 28 or size > 1024 * 1024:
                        raise ValueError('无效 ds64。')
                    raw = f.read(size)
                    _, ds_data, _, count = struct.unpack_from('<QQQI', raw)
                    if 28 + count * 12 > size:
                        raise ValueError('无效 ds64 表。')
                    for i in range(count):
                        k, n = struct.unpack_from('<4sQ', raw, 28 + i * 12)
                        ds_sizes[k] = n
                elif kind == b'fmt ':
                    if size < 16 or size > 4096:
                        raise ValueError('无效 WAV fmt。')
                    raw = f.read(size)
                    w.format, w.channels, w.rate, _, align, w.bits = struct.unpack_from('<HHIIHH', raw)
                    if w.format == 0xfffe:
                        if len(raw) < 40 or raw[26:40] != bytes.fromhex('000000001000800000aa00389b71'):
                            raise ValueError('不支持的 WAVEFORMATEXTENSIBLE 子格式。')
                        valid = struct.unpack_from('<H', raw, 18)[0]
                        if valid not in (0, w.bits):
                            raise ValueError('暂不支持容器位深与有效位深不同的 WAV。')
                        w.format = struct.unpack_from('<H', raw, 24)[0]
                    if not w.channels or align != w.channels * (w.bits // 8):
                        raise ValueError('无效 WAV 声道/采样对齐。')
                elif kind == b'data':
                    if w.size:
                        raise ValueError('暂不支持多个 WAV data 块。')
                    w.offset, w.size = pos, size
                elif kind in (b'axml', b'chna', b'bext', b'LIST', b'iXML', b'dbmd'):
                    if size > 128 * 1024 * 1024:
                        raise ValueError('WAV 元数据块过大。')
                    w.chunks[kind] = f.read(size)
                f.seek(pos + size + (size & 1))
        if not w.offset or not w.size or not w.channels:
            raise ValueError('WAV 缺少 fmt 或音频数据。')
        if (w.format, w.bits) not in ((1, 16), (1, 24), (1, 32), (3, 32)):
            raise ValueError('支持 PCM 16/24/32-bit 或 Float32 WAV。')
        if w.rate != 48000:
            raise ValueError('当前对象编码路径要求 48 kHz；请先重采样母版。')
        if not 1 <= w.channels <= 128:
            raise ValueError('ADM 输入支持 1–128 路音频轨道。')
        if w.size % (w.channels * w.bits // 8):
            raise ValueError('WAV 数据不是完整采样帧。')
        return w

    @property
    def samples(self):
        return self.size // (self.channels * self.bits // 8)

    def read(self, start, count):
        if not 0 <= start <= self.samples or count < 0 or start + count > self.samples:
            raise ValueError('读取范围超出 WAV。')
        with self.path.open('rb') as f:
            f.seek(self.offset + start * self.channels * (self.bits // 8))
            raw = f.read(count * self.channels * (self.bits // 8))
        if self.bits == 24:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            a = b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16
            a = ((a ^ 0x800000) - 0x800000).astype(np.float32) / 8388608
        elif self.format == 3:
            a = np.frombuffer(raw, dtype='<f4').copy()
        else:
            a = np.frombuffer(raw, dtype=f'<i{self.bits // 8}').astype(np.float32) / (2 ** (self.bits - 1))
        a = a.reshape(count, self.channels)
        if not np.isfinite(a).all():
            raise ValueError('WAV 含 NaN 或无穷大。')
        return a

    def channel_assignments(self):
        raw = self.chunks.get(b'chna')
        if not raw:
            return {}
        if len(raw) < 4:
            raise ValueError('chna 截断。')
        tracks, count = struct.unpack_from('<HH', raw)
        if tracks != self.channels or len(raw) < 4 + count * 40:
            raise ValueError('chna 与 WAV 声道数不一致。')
        result = {}
        for i in range(count):
            index, uid, ref, pack, _ = struct.unpack_from('<H12s14s11sB', raw, 4 + i * 40)
            uid = uid.rstrip(b'\0').decode('ascii')
            if uid in result or not 1 <= index <= self.channels:
                raise ValueError('chna 声道映射重复或越界。')
            result[uid] = (index - 1, ref.rstrip(b'\0').decode('ascii'))
        return result
