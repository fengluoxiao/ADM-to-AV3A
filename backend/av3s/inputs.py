"""Route compressed inputs by their actual MP4 sample entry."""
import mmap
from pathlib import Path
from . import cavern, mpegh
from .mp4 import atoms


def backend(path):
    suffix = Path(path).suffix.lower()
    if suffix in {'.mhas', '.mha', '.mhm'}:
        return mpegh
    if suffix in {'.m4a', '.mp4'}:
        with open(path, 'rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            def find(start, end, kind):
                return next((a for a in atoms(data, start, end) if a[0] == kind), None)
            moov = find(0, len(data), b'moov')
            if moov:
                for track in atoms(data, moov[2], moov[3]):
                    if track[0] != b'trak':
                        continue
                    parent = track
                    for kind in (b'mdia', b'minf', b'stbl', b'stsd'):
                        parent = find(parent[2], parent[3], kind)
                        if parent is None:
                            break
                    if parent and any(a[0] in (b'mha1', b'mhm1')
                                      for a in atoms(data, parent[2] + 8, parent[3])):
                        return mpegh
    return cavern


def inspect_input(input_path, metadata=None, cavern=None, mpegh=None, **kwargs):
    target = backend(input_path)
    return target.inspect_input(input_path, metadata, **{target.OPTION: mpegh if target.OPTION == 'mpegh' else cavern}, **kwargs)


def convert_input(input_path, output_path, metadata=None, cavern=None, mpegh=None, **kwargs):
    target = backend(input_path)
    return target.convert_input(input_path, output_path, metadata, **{target.OPTION: mpegh if target.OPTION == 'mpegh' else cavern}, **kwargs)


def export_input(input_path, output_path, metadata=None, cavern=None, mpegh=None, **kwargs):
    target = backend(input_path)
    return target.export_input(input_path, output_path, metadata, **{target.OPTION: mpegh if target.OPTION == 'mpegh' else cavern}, **kwargs)
