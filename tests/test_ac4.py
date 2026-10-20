"""Actual encoder/unchanged decoder integration, using synthetic audio only."""
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import numpy as np
from av3s import ac4
from av3s.mp4 import atoms, box, full, copy_tags
from av3s.pipeline import Cancelled, convert, inspect
from av3s.scene import Point
from av3s.waveio import Wave
from test_logic_adm import logic_fixture

ROOT = Path(__file__).resolve().parents[1]


class Ac4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ac4-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wav = self.root / 'Logic 母版.wav'
        logic_fixture(self.wav, objects=2)

    def native(self):
        try:
            return ac4.encoder_path()
        except ValueError:
            self.skipTest('AC-4 component not built')

    def test_crc_and_corruption_detection(self):
        self.assertEqual(ac4.crc16(b'123456789'), 0xfee8)
        length, payload = b'\x00\x03', b'abc'
        stream, packets = self.root / 'raw.ac4', self.root / 'packets'
        valid = b'\xac\x41' + length + payload + struct.pack('>H', ac4.crc16(length + payload))
        stream.write_bytes(valid)
        self.assertEqual(ac4.unpack_stream(stream, packets, lambda: None)[0], [3])
        self.assertEqual(packets.read_bytes(), payload)
        stream.write_bytes(valid[:5] + b'x' + valid[6:])
        with self.assertRaisesRegex(ValueError, 'CRC'):
            ac4.unpack_stream(stream, packets, lambda: None)
        stream.write_bytes(valid[:-1])
        with self.assertRaisesRegex(ValueError, '截断'):
            ac4.unpack_stream(stream, packets, lambda: None)

    def test_signed_height_and_front_back_coordinates(self):
        self.assertEqual(ac4.properties(Point(-1, 1, -.5, size=.25)),
                         dict(position=[0, 0, -.5], width=[.25] * 3))
        self.assertEqual(ac4.properties(Point(1, -1, 1))['position'], [1, 1, 1])

    def test_capacity_requires_explicit_reduction(self):
        logic_fixture(self.wav, objects=23)
        self.assertEqual(inspect(self.wav, codec='ac4')[2]['encoder_capacity'], 20)
        self.assertTrue(inspect(self.wav, codec='ac4', ac4_native=True)[2]['fits_encoder'])
        self.assertFalse(inspect(self.wav, codec='ac4')[2]['fits_encoder'])
        with self.assertRaisesRegex(ValueError, '20 路'):
            convert(self.wav, self.root / 'out.m4a', codec='ac4')
        for bitrate in (127, 1501, 768.5):
            with self.assertRaisesRegex(ValueError, '码率'):
                convert(self.wav, self.root / 'out.m4a', codec='ac4', bitrate=bitrate)

    def test_native_m4a_bed_objects_tags_and_timeline(self):
        self.native()
        output, tags = self.root / '成品.mp4', self.root / 'tags.m4a'
        udta = box(b'udta', full(b'meta', bytes(4)))
        tags.write_bytes(box(b'ftyp', b'isom' + bytes(4)) + box(b'moov', udta))
        report = convert(self.wav, output, codec='ac4', tags=tags, start=.01, duration=.073)
        self.assertEqual((report['encoded_channels'], report['bed_channels_preserved'], report['objects_preserved']), (12, 1, 2))
        self.assertEqual(report['encoded_objects'], 11)
        self.assertEqual(len(report['bed_object_mapping']), 9)
        self.assertEqual(report['validation']['crc_failures'], 0)
        self.assertEqual(report['validation']['ac4_topology']['fullband_upmix_signals'], 11)
        self.assertTrue(report['validation']['sda_structure_compatible'])
        self.assertEqual(report['output_samples'], 3504)
        self.assertTrue(report['source_gain_baked'])
        self.assertEqual(copy_tags(output), udta)
        self.assertEqual(output.with_suffix('.source.adm.xml').read_bytes(), Wave.open(self.wav).chunks[b'axml'])
        data = output.read_bytes()
        def child(parent, kind):
            return next(a for a in atoms(data, parent[2], parent[3]) if a[0] == kind)
        movie = next(a for a in atoms(data) if a[0] == b'moov')
        track = child(movie, b'trak')
        edit = child(child(track, b'edts'), b'elst')
        self.assertEqual(struct.unpack_from('>IIi', data, edit[2] + 4), (1, 3504, 4385))
        stbl = child(child(child(track, b'mdia'), b'minf'), b'stbl')
        stsd = child(stbl, b'stsd')
        entry = next(atoms(data, stsd[2] + 8, stsd[3]))
        self.assertEqual(entry[0], b'ac-4')
        self.assertEqual(next(atoms(data, entry[2] + 28, entry[3]))[0], b'dac4')
        sizes = child(stbl, b'stsz')
        size, count = struct.unpack_from('>II', data, sizes[2] + 4)
        self.assertEqual(size, 0)
        self.assertEqual(count, report['validation']['frames'])
        mdat = next(a for a in atoms(data) if a[0] == b'mdat')
        self.assertEqual(sum(struct.unpack_from('>' + 'I' * count, data, sizes[2] + 12)), mdat[3] - mdat[2])
        decoder = ROOT / '.tools/macinac4/macinac4-v0.1.0-x86_64-pc-windows-msvc/macinac4.exe'
        if decoder.is_file():
            decoded = self.root / 'decoded.wav'
            result = subprocess.run([str(decoder), 'export-full-adm-bwf', str(output), '--output', str(decoded)],
                                    capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)['result']
            self.assertEqual(receipt['audio']['frames'], 3504)
            # The independent exporter adds a 10-track bed to its ADM output;
            # only its LFE has audio, followed by our 11 positional objects.
            self.assertEqual(receipt['audio']['channels'], 21)
            self.assertEqual(len(receipt['objects']), 11)
            wave = Wave.open(decoded)
            pcm = wave.read(0, wave.samples)
            self.assertGreater(float(np.max(np.abs(pcm))), .001)
            self.assertTrue(np.isfinite(pcm).all())

    def test_native_bed_assignments_preserved(self):
        self.native()
        report = convert(self.wav, self.root / 'native.m4a', codec='ac4', ac4_native=True)
        self.assertEqual(report['ac4_profile'], 'native')
        self.assertEqual(report['bed_channels_preserved'], 10)
        self.assertEqual(report['encoded_objects'], 2)
        self.assertEqual(report['bed_object_mapping'], [])
        self.assertEqual(report['validation']['ac4_topology']['bed_signals'], 9)
        self.assertFalse(report['validation']['sda_structure_compatible'])

    def test_19_objects_returning_after_silence_decode_without_history(self):
        self.native()
        decoder = ROOT / '.tools/macinac4/macinac4-v0.1.0-x86_64-pc-windows-msvc/macinac4.exe'
        if not decoder.is_file():
            self.skipTest('Published independent AC-4 decoder unavailable')
        # Temporal prediction in the previous adapter failed on this scene:
        # zero matrix rows disappear and later return without decoder history.
        import wave
        channels, samples = 19, 48000
        t = np.arange(samples) / 48000
        pcm = np.stack([.025 * np.sin(2 * np.pi * (173 + 83 * i) * t)
                        for i in range(channels)], axis=1)
        for i in range(channels):
            pcm[:int((.12 + i % 5 * .031) * 48000), i] = 0
        pcm[24000:30000] = 0
        with wave.open(str(self.wav), 'wb') as dst:
            dst.setparams((channels, 2, 48000, 0, 'NONE', 'not compressed'))
            dst.writeframes((pcm * 32768).astype('<i2').tobytes())
        sidecar = self.root / 'onsets.jsonl'
        sidecar.write_text(json.dumps(dict(sample=0, objects=[
            dict(x=i / 18 * 2 - 1, y=0, z=0) for i in range(channels)])), encoding='utf-8')
        output = self.root / 'onsets.mp4'
        report = convert(self.wav, output, sidecar, codec='ac4', bitrate=768)
        self.assertEqual(report['validation']['iframe_interval'], 1)
        decoded = self.root / 'onsets-decoded.wav'
        result = subprocess.run([str(decoder), 'export-objects-pcm', str(output), '--output', str(decoded)],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        audio = Wave.open(decoded)
        self.assertEqual((audio.samples, audio.channels), (samples, channels))
        decoded_pcm = audio.read(0, samples)
        self.assertTrue(np.isfinite(decoded_pcm).all())
        self.assertGreater(float(np.max(np.abs(decoded_pcm))), .001)

    def test_24_object_native_stream_is_marked_incompatible(self):
        self.native()
        logic_fixture(self.wav, objects=14)
        sidecar = self.root / 'objects.jsonl'
        sidecar.write_text(json.dumps(dict(sample=0, objects=[dict(x=i / 23 * 2 - 1) for i in range(24)])), encoding='utf-8')
        output = self.root / 'native-24.mp4'
        report = convert(self.wav, output, sidecar, codec='ac4', ac4_native=True)
        self.assertEqual(report['validation']['ac4_topology']['fullband_upmix_signals'], 24)
        self.assertFalse(report['validation']['sda_structure_compatible'])
        self.assertEqual(report['validation']['topology_checked_frames'], report['validation']['frames'])
        with self.assertRaisesRegex(ValueError, '20 路'):
            convert(self.wav, self.root / 'compatible.mp4', sidecar, codec='ac4')

    def test_jsonl_gain_holds_across_updates_and_cut(self):
        from av3s.scene import load_jsonl
        logic_fixture(self.wav, objects=0)
        sidecar = self.root / 'gain.jsonl'
        sidecar.write_text('\n'.join(json.dumps(dict(sample=t, objects=[dict(gain=g + i / 10) for i in range(10)]))
                                      for t, g in [(0, .25), (5, .5), (8, 1)]), encoding='utf-8')
        scene = load_jsonl(sidecar, Wave.open(self.wav))
        np.testing.assert_allclose(scene.pcm_gains(3, 7, [2, 0]),
            [[.45, .25]] * 2 + [[.7, .5]] * 3 + [[1.2, 1]] * 2)

    def test_reduction_to_19_objects_and_lfe(self):
        self.native()
        logic_fixture(self.wav, objects=56)
        output = self.root / 'reduced.m4a'
        report = convert(self.wav, output, codec='ac4', spatial_reduction=True)
        self.assertEqual((report['encoded_channels'], report['encoded_objects']), (20, 19))
        self.assertEqual(report['bed_channels_preserved'], 1)
        self.assertTrue(report['spatial_reduction']['lossy'])
        self.assertEqual(len(report['spatial_reduction']['sources']), 65)
        self.assertEqual(report['validation']['crc_failures'], 0)
        self.assertEqual(report['validation']['ac4_topology']['fullband_upmix_signals'], 19)
        self.assertTrue(report['validation']['sda_structure_compatible'])

    def test_cancelled_encoding_publishes_nothing_and_existing_is_preserved(self):
        self.native()
        output, flag = self.root / 'out.m4a', self.root / 'cancel'
        def events(**event):
            if event.get('phase') == 'encode':
                flag.touch()
        with self.assertRaises(Cancelled):
            convert(self.wav, output, codec='ac4', cancel_file=flag, emit=events)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix('.conversion.json').exists())
        self.assertFalse(list(self.root.glob('*.partial')))
        output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, '已存在'):
            convert(self.wav, output, codec='ac4')
        self.assertEqual(output.read_bytes(), b'keep')


if __name__ == '__main__':
    unittest.main()
