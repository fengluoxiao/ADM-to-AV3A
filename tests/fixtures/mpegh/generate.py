"""Reproduce original test tones/OAM, then encode with a supplied libmpeghe CLI.

See README.md for the required encoder pin and test-only LC4 configuration patch.
Only writes into an explicitly chosen empty output directory.
"""
import argparse
import math
from pathlib import Path
import struct
import subprocess
import wave

from av3s.mp4 import atoms


def generate(encoder, output, channels, formats, below=False, subframes=False):
    samples = 16384
    name = f'subframe{channels}' if subframes else f'below{channels}' if below else f'moving{channels}'
    for i in range(channels):
        pcm = b''.join(struct.pack('<h', round(.03 * 32767 * math.sin(2 * math.pi * (300 + i * 73) * s / 48000)))
                       for s in range(samples))
        with wave.open(str(output / f'{name}_{i:03d}.wav'), 'wb') as f:
            f.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            f.writeframes(pcm)
    oam = output / f'{name}.oam'
    with oam.open('wb') as f:
        f.write(b'OAM\0' + struct.pack('<HHH', 2, 0, channels) + bytes(32) + bytes(channels * 64))
        step = 256 if subframes else 1024
        for sample in range(step, samples + 8193, step):
            for i in range(channels):
                az = (-150 + i * 300 / (channels - 1) + 60 * sample / samples + 180) % 360 - 180
                elevation = -30 + i * 15 if below else (-30 + i * 15) % 90
                f.write(struct.pack('<QH5f', sample, i, az, elevation,
                                    1., .6 + .1 * math.sin(sample / 4096 + i), i % 3 * 12))
    for fmt, suffix in formats:
        target = output / (name + suffix)
        subprocess.run([str(encoder), '-oam_file:' + str(oam), '-ofile:' + str(target),
                        '-op_fmt:' + str(fmt), '-br:' + str(384000 if channels == 4 else 1280000),
                        '-cicp:19'], check=True, stdout=subprocess.DEVNULL)
        if fmt != 1:
            data = bytearray(target.read_bytes())
            def fix_edit(start, end):
                for kind, _, body, finish in atoms(data, start, end):
                    if kind == b'elst':
                        assert data[body] == 0 and struct.unpack_from('>I', data, body + 4)[0] == 1
                        assert data[finish - 4:finish] == bytes(4)
                        # Upstream writer incorrectly leaves edit rate at zero.
                        data[finish - 4:finish] = b'\0\1\0\0'
                    elif kind in (b'moov', b'trak', b'edts'):
                        fix_edit(body, finish)
            fix_edit(0, len(data))
            target.write_bytes(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--encoder', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--unpatched', action='store_true', help='Generate only the default CPE/IGF four-object fixture')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    if args.unpatched:
        generate(args.encoder.resolve(), out, 4, [(1, '.mhas')])
    else:
        generate(args.encoder.resolve(), out, 4, [(1, '-sce.mhas'), (2, '-mha1.mp4'), (3, '-mhm1.mp4')])
        generate(args.encoder.resolve(), out, 20, [(1, '-sce.mhas')])
        generate(args.encoder.resolve(), out, 4, [(1, '-sce.mhas')], below=True)
        generate(args.encoder.resolve(), out, 4, [(1, '-sce.mhas')], subframes=True)
