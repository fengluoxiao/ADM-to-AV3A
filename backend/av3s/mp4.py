"""Minimal fast-start audio BMFF writer and strict fixed-profile frame checks."""
import hashlib
import mmap
import shutil
import struct


def box(kind, payload=b''):
    if len(payload) + 8 >= 2 ** 32:
        raise ValueError('元数据盒超出 32 位大小。')
    return struct.pack('>I4s', len(payload) + 8, kind) + payload


def full(kind, payload=b'', version=0, flags=0):
    return box(kind, bytes([version]) + flags.to_bytes(3, 'big') + payload)


def u32(*values):
    return struct.pack('>' + 'I' * len(values), *values)


def atoms(data, start=0, end=None):
    end = len(data) if end is None else end
    while start < end:
        if start + 8 > end:
            raise ValueError('MP4 盒头截断。')
        size, kind = struct.unpack_from('>I4s', data, start)
        header = 8
        if size == 1:
            if start + 16 > end:
                raise ValueError('MP4 扩展盒头截断。')
            size = struct.unpack_from('>Q', data, start + 8)[0]
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            raise ValueError(f'无效 MP4 盒 {kind!r}。')
        yield kind, start, start + header, start + size
        start += size


def copy_tags(path):
    if not path:
        return b''
    with open(path, 'rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        moov = next((a for a in atoms(data) if a[0] == b'moov'), None)
        if moov is None:
            raise ValueError('标签来源不是有效 M4A/MP4。')
        udta = next((a for a in atoms(data, moov[2], moov[3]) if a[0] == b'udta'), None)
        if udta is None:
            raise ValueError('标签来源没有 udta。')
        if udta[3] - udta[1] > 64 * 1024 * 1024:
            raise ValueError('标签块大于 64 MiB。')
        return bytes(data[udta[1]:udta[3]])


class Bits:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n):
        value = 0
        for _ in range(n):
            value = value << 1 | ((self.data[self.pos // 8] >> (7 - self.pos % 8)) & 1)
            self.pos += 1
        return value


def crc_table():
    table = []
    for i in range(256):
        v = i << 8
        for _ in range(8):
            v = ((v << 1) ^ (0x1021 if v & 0x8000 else 0)) & 0xffff
        table.append(v)
    return table


CRC_TABLE = crc_table()


def crc16(payload):
    # AVS3 recurrence differs from the usual CRC-16/CCITT byte recurrence.
    crc = 0xffff
    for byte in payload:
        crc = (((crc << 8) | byte) ^ CRC_TABLE[crc >> 8]) & 0xffff
    return crc


def validate_stream(path, channels, count, check_cancel=lambda: None):
    frame_size = channels * 256  # 96 kbps per carrier, 48 kHz, 1024 samples
    if path.stat().st_size != frame_size * count:
        raise ValueError('编码器输出帧数或帧长不符合预期。')
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for i in range(count):
            if i % 64 == 0:
                check_cancel()
            frame = f.read(frame_size)
            b = Bits(frame[:8])
            sync, codec, anc, nn, profile, fs = [b.read(n) for n in (12, 4, 1, 3, 3, 4)]
            high = b.read(8)
            bed, objects, bitrate, resolution = b.read(2), b.read(7) + 1, b.read(4), b.read(2)
            low = b.read(8)
            if (sync, codec, anc, nn, profile, fs, bed, objects, bitrate, resolution) != (
                    0xfff, 2, 0, 1, 1, 2, 0, channels, 7, 1):
                raise ValueError(f'第 {i} 帧编码配置不符合预期。')
            if crc16(frame[8:]) != high << 8 | low:
                raise ValueError(f'第 {i} 帧 CRC 错误。')
            if frame[8] & 0xc0 != 0x80:  # static flag=1, hasVrExt=0
                raise ValueError(f'第 {i} 帧缺少预期静态元数据。')
            digest.update(frame)
    return dict(frames=count, frame_size=frame_size, crc_failures=0, elementary_sha256=digest.hexdigest())


def mux(stream, output, channels, count, samples, udta=b'', *, ac4=None):
    frame_size, rate, delay = channels * 256, 48000, 1024
    bitrate, frame_samples = channels * 96000, 1024
    if ac4:
        delay, bitrate, frame_samples = ac4['delay'], ac4['bitrate'], 2048
        frame_size = 0
    if samples + delay > count * frame_samples or count * frame_samples >= 2 ** 32:
        raise ValueError('输出时长超过当前封装器范围。')
    identity = struct.pack('>9I', 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    mvhd = full(b'mvhd', u32(0, 0, rate, samples, 0x10000) + struct.pack('>H', 0x100) + b'\0' * 10 + identity + b'\0' * 24 + u32(2))
    tkhd = full(b'tkhd', u32(0, 0, 1, 0, samples) + b'\0' * 8 + struct.pack('>hhhh', 0, 0, 0x100, 0) + identity + u32(0, 0), flags=3)
    mdhd = full(b'mdhd', u32(0, 0, rate, count * frame_samples) + struct.pack('>HH', 0x55c4, 0))
    hdlr = full(b'hdlr', u32(0) + b'soun' + b'\0' * 12 + (b'AC-4 A-JOC\0' if ac4 else b'Audio Vivid\0'))
    dinf = box(b'dinf', full(b'dref', u32(1) + full(b'url ', flags=1)))
    dca3 = bytes([0x22, 0x21, channels << 1]) + struct.pack('>H', bitrate // 1000) + b'\x40'
    entry = b'\0' * 6 + struct.pack('>H', 1) + b'\0' * 8 + struct.pack('>HHHHI', 2 if ac4 else channels, 16, 0, 0, rate << 16)
    stsd = full(b'stsd', u32(1) + box(b'ac-4' if ac4 else b'av3a', entry +
                box(b'dac4' if ac4 else b'dca3', ac4['dac4'] if ac4 else dca3) +
                box(b'btrt', u32(max(ac4['sizes']) if ac4 else frame_size, bitrate, bitrate))))
    edts = box(b'edts', full(b'elst', u32(1, samples, delay) + struct.pack('>hh', 1, 0)))
    ftyp = box(b'ftyp', b'isom' + u32(512) + b'isomiso2mp41')

    def moov(offset):
        stbl = box(b'stbl', stsd + full(b'stts', u32(1, count, frame_samples)) + full(b'stsc', u32(1, 1, count, 1)) +
                   full(b'stsz', u32(frame_size, count) + (u32(*ac4['sizes']) if ac4 else b'')) +
                   (full(b'stss', u32(len(ac4['sync_samples']), *ac4['sync_samples'])) if ac4 else b'') +
                   full(b'co64', u32(1) + struct.pack('>Q', offset)))
        minf = box(b'minf', full(b'smhd', b'\0' * 4) + dinf + stbl)
        return box(b'moov', mvhd + box(b'trak', tkhd + edts + box(b'mdia', mdhd + hdlr + minf)) + udta)

    movie = moov(0)
    movie = moov(len(ftyp) + len(movie) + 16)
    with output.open('wb') as f, stream.open('rb') as src:
        f.write(ftyp + movie + struct.pack('>I4sQ', 1, b'mdat', stream.stat().st_size + 16))
        shutil.copyfileobj(src, f, 1024 * 1024)
    # Verify sample table relocation and exact tag copy independently of writer offsets.
    with output.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        top = list(atoms(data))
        mdat = next(a for a in top if a[0] == b'mdat')
        if mdat[2] != len(ftyp) + len(movie) + 16 or mdat[3] != len(data):
            raise ValueError('M4A 数据偏移验证失败。')
        digest = hashlib.sha256(data).hexdigest()
    return dict(m4a_sha256=digest, edit_start_samples=delay, udta_copied=bool(udta),
                **({'dac4': ac4['dac4'].hex(), 'sample_entry': 'ac-4'} if ac4 else {'dca3': dca3.hex()}))
