"""UTF-8 JSON Lines protocol shared by WinUI and command-line clients."""
import argparse
import json
import sys
from .pipeline import Cancelled
from .inputs import inspect_input, convert_input, export_input


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description='ADM / WAV / Atmos / MPEG-H 360RA → Audio Vivid / AC-4 A-JOC M4A')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'convert', 'export'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--input', required=True)
        cmd.add_argument('--metadata', help='ADM XML or Cavern scene.jsonl; omitted for embedded ADM')
        cmd.add_argument('--cavern', help='CavernExport.exe override')
        cmd.add_argument('--mpegh', help='MpeghExport.exe override')
        cmd.add_argument('--cancel-file')
        if name != 'export':
            cmd.add_argument('--codec', choices=('av3a', 'ac4'), default='av3a')
            cmd.add_argument('--bitrate', type=int, default=768, help='AC-4 total bitrate in kbps, 128..1500')
            cmd.add_argument('--ac4-native', action='store_true', help='Experimental native bed assignments and 64 total inputs; default compatible object mode uses 20')
        if name in ('convert', 'export'):
            cmd.add_argument('--output', required=True)
            cmd.add_argument('--start', type=float, default=0)
            cmd.add_argument('--duration', type=float)
        if name == 'convert':
            cmd.add_argument('--tags', help='Copy udta tags verbatim from this M4A/MP4')
            cmd.add_argument('--encoder')
            cmd.add_argument('--ac4-encoder', help='Ac4Encode.exe override')
            cmd.add_argument('--spatial-reduction', action='store_true',
                             help='JOCForge reduction: preserve LFE; merge bed/objects within the selected codec capacity (lossy positions and gains)')
    args = p.parse_args()
    try:
        values = vars(args)
        command = values.pop('command')
        values['input_path'] = values.pop('input')
        if command == 'inspect':
            info = inspect_input(**values, emit=emit)
            emit(type='inspection', **info)
        else:
            values['output_path'] = values.pop('output')
            (export_input if command == 'export' else convert_input)(**values, emit=emit)
    except Cancelled as ex:
        emit(type='cancelled', message=str(ex))
        return 2
    except Exception as ex:
        emit(type='error', message=str(ex))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
