"""UTF-8 JSON Lines protocol shared by WinUI and command-line clients."""
import argparse
import json
import sys
from .pipeline import inspect, convert, Cancelled


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description='ADM / WAV + spatial metadata → Audio Vivid M4A')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'convert'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--input', required=True)
        cmd.add_argument('--metadata', help='ADM XML or Cavern scene.jsonl; omitted for embedded ADM')
        if name == 'convert':
            cmd.add_argument('--output', required=True)
            cmd.add_argument('--tags', help='Copy udta tags verbatim from this M4A/MP4')
            cmd.add_argument('--encoder')
            cmd.add_argument('--start', type=float, default=0)
            cmd.add_argument('--duration', type=float)
            cmd.add_argument('--cancel-file')
            cmd.add_argument('--spatial-reduction', action='store_true',
                             help='JOCForge reduction: keep LFE; merge non-LFE bed and objects into at most 15 objects (lossy positions and gains)')
    args = p.parse_args()
    try:
        values = vars(args)
        if values.pop('command') == 'inspect':
            _, _, info = inspect(values['input'], values['metadata'])
            emit(type='inspection', **info)
        else:
            values['input_path'] = values.pop('input')
            values['output_path'] = values.pop('output')
            convert(**values, emit=emit)
    except Cancelled as ex:
        emit(type='cancelled', message=str(ex))
        return 2
    except Exception as ex:
        emit(type='error', message=str(ex))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
