"""Generate an original two-object moving-tone demo (no music assets)."""
import argparse
import json
import math
from pathlib import Path
import struct
import wave


def make_demo(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rate, seconds = 48000, 2
    with wave.open(str(folder / 'moving.wav'), 'wb') as f:
        f.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        audio = bytearray()
        for i in range(rate * seconds):
            fade = min(1, i / 2400, (rate * seconds - 1 - i) / 2400)
            audio.extend(struct.pack('<hh', *(round(2500 * fade * math.sin(2 * math.pi * hz * i / rate)) for hz in (440, 660))))
        f.writeframes(audio)
    with (folder / 'moving.scene.jsonl').open('w', encoding='utf-8') as f:
        for sample in range(0, rate * seconds, 256):
            x = -.8 + 1.6 * sample / (rate * seconds)
            objects = [dict(index=0, name='Moving tone', x=x, y=.8, z=.2, gain=1, size=0),
                       dict(index=1, name='Reply', x=-x, y=-.6, z=.5, gain=.8, size=.1)]
            f.write(json.dumps(dict(sample=sample, objects=objects)) + '\n')
    return folder / 'moving.wav', folder / 'moving.scene.jsonl'


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('folder', nargs='?', default='artifacts/demo')
    for path in make_demo(p.parse_args().folder):
        print(path)
