"""Synthetic Logic-style ADM exports; no user audio or metadata in fixtures."""
from pathlib import Path
import struct
import tempfile
import unittest
import numpy as np
from av3s.pipeline import inspect, convert
from av3s.metadata import bed_layout
from av3s.reduction import cluster
from test_conversion import riff_chunk


SPEAKERS = ('L', 'R', 'C', 'LFE', 'Lss', 'Rss', 'Lrs', 'Rrs', 'Lts', 'Rts')


def logic_fixture(path, objects=6, permute=False):
    channels = 10 + objects
    parts = ['<audioFormatExtended><audioProgramme audioProgrammeID="APR_1001"/>']
    chna = struct.pack('<HH', channels, channels)
    for i in range(channels):
        bed = i < 10
        channel = f'AC_{1 if bed else 3:04x}{i+0x1001:04x}'
        uid = f'ATU_{i+1:08x}'
        track = f'AT_{1 if bed else 3:04x}{i+0x1001:04x}_01'
        stream = f'AS_{1 if bed else 3:04x}{i+0x1001:04x}'
        physical = channels - i if permute else i + 1
        chna += struct.pack('<H12s14s11sB', physical, uid.encode(), track.encode(), b'', 0)
        parts.append(f'<audioObject audioObjectID="AO_{i+0x1001:04x}" audioObjectName="Track {i}" '
                     f'start="00:00:00.00000" duration="00:00:00.10000"><audioTrackUIDRef>{uid}</audioTrackUIDRef></audioObject>')
        parts.append(f'<audioTrackUID UID="{uid}"><audioTrackFormatIDRef>{track}</audioTrackFormatIDRef></audioTrackUID>'
                     f'<audioTrackFormat audioTrackFormatID="{track}"><audioStreamFormatIDRef>{stream}</audioStreamFormatIDRef></audioTrackFormat>'
                     f'<audioStreamFormat audioStreamFormatID="{stream}"><audioChannelFormatIDRef>{channel}</audioChannelFormatIDRef></audioStreamFormat>')
        parts.append(f'<audioChannelFormat audioChannelFormatID="{channel}" typeLabel="{1 if bed else 3:04x}">')
        if bed:
            parts.append(f'<audioBlockFormat><speakerLabel>RC_{SPEAKERS[i]}</speakerLabel>'
                         '<cartesian>1</cartesian><position coordinate="X">0</position>'
                         '<position coordinate="Y">1</position><gain>0.5</gain></audioBlockFormat>')
        else:
            # Two coordinates, 10 ms jump interpolation, and independently
            # rounded five-decimal durations (one sample of rounding error).
            for start, duration, x in [('0.00000', '0.03333', 0), ('0.03333', '0.03333', 1), ('0.06667', '0.03333', 1)]:
                parts.append(f'<audioBlockFormat rtime="00:00:{start}" duration="00:00:{duration}">'
                             f'<cartesian>1</cartesian><position coordinate="X">{x}</position>'
                             '<position coordinate="Y">0</position><jumpPosition interpolationLength="0.01000">1</jumpPosition></audioBlockFormat>')
        parts.append('</audioChannelFormat>')
    parts.append('</audioFormatExtended>')
    t = np.arange(4800) / 48000
    pcm = np.stack([np.sin(2 * np.pi * (200 + i * 73) * t) * 1000 for i in range(channels)], axis=1).astype('<i2').tobytes()
    body = b'WAVE' + riff_chunk(b'fmt ', struct.pack('<HHIIHH', 1, channels, 48000, channels * 96000, channels * 2, 16))
    body += riff_chunk(b'data', pcm) + riff_chunk(b'axml', ''.join(parts).encode()) + riff_chunk(b'chna', chna)
    body += riff_chunk(b'dbmd', b'Synthetic Dolby metadata fixture')
    path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)


class LogicAdmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='logic-adm-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wav = self.root / 'logic.wav'

    def test_66_track_inspection_separates_bed_objects_and_capacity(self):
        logic_fixture(self.wav, objects=56)
        wave, scene, info = inspect(self.wav)
        self.assertEqual((wave.channels, scene.objects, info['bed_channels']), (66, 56, 10))
        self.assertFalse(info['fits_encoder'])
        self.assertEqual(info['bed_layout'], '7.1.2')
        self.assertEqual(scene.lfe, 3)
        self.assertEqual(wave.chunks[b'dbmd'], b'Synthetic Dolby metadata fixture')
        self.assertEqual(scene.frames, [])
        with self.assertRaisesRegex(ValueError, '66 路 ADM'):
            convert(self.wav, self.root / 'result.m4a')
        self.assertFalse((self.root / 'result.m4a').exists())

    def test_logic_jump_interpolation_and_omitted_z(self):
        logic_fixture(self.wav)
        _, scene, _ = inspect(self.wav)
        self.assertEqual(scene.at(1599)[10].x, 0)
        self.assertAlmostEqual(scene.at(1840)[10].x, .5)
        self.assertEqual(scene.at(2080)[10].x, 1)
        self.assertEqual(scene.at(1840)[10].z, 0)
        np.testing.assert_array_equal(scene.pcm_gains(0, 20, [0, 3, 9]), .5)

    def test_chna_order_controls_bed_routing(self):
        logic_fixture(self.wav, permute=True)
        _, scene, _ = inspect(self.wav)
        self.assertEqual(bed_layout(scene), (22, '7.1.2', list(range(15, 5, -1))))
        self.assertEqual(scene.lfe, 12)
        self.assertEqual(scene.bed[15], 'L')
        self.assertEqual(scene.bed[6], 'Rts')

    def test_native_712_bed_plus_six_objects(self):
        encoder = Path('.tools/bin/avs3Encoder.exe').resolve()
        if not encoder.is_file():
            self.skipTest('Native encoder not built')
        logic_fixture(self.wav)
        output = self.root / 'result.m4a'
        report = convert(self.wav, output, encoder=encoder)
        self.assertEqual(report['objects_preserved'], 6)
        self.assertEqual(report['bed_channels_preserved'], 10)
        self.assertEqual(report['bed_layout'], '7.1.2')
        self.assertEqual(report['validation']['crc_failures'], 0)

    def test_clustering_keeps_every_source_and_bakes_gains_once(self):
        logic_fixture(self.wav, objects=56)
        wave, scene, _ = inspect(self.wav)
        encoded, read, report = cluster(wave, scene, 0, wave.samples, lambda: None, lambda **_: None)
        self.assertEqual((encoded.channels, encoded.objects, len(encoded.bed)), (16, 15, 1))
        members = [s['index'] for s in report['sources']]
        self.assertEqual(sorted(members), [i for i in range(66) if i != 3])
        self.assertEqual(len(set(members)), 65)
        self.assertTrue(report['lossy'])
        mixed = read(1500, 500)
        original = wave.read(1500, 500)
        original *= scene.pcm_gains(1500, 500, list(range(66)))
        np.testing.assert_allclose(mixed[:, 0], original[:, 3], atol=1e-7)
        # Arbitrary reads, including a split inside a crossfade, must produce
        # exactly the same PCM as a contiguous read.
        np.testing.assert_array_equal(read(800, 1600), np.concatenate([read(800, 230), read(1030, 1370)]))
        timeline = report['_timeline']
        self.assertTrue(np.all(timeline['contribution_gains'] > 0))
        expected = np.zeros(16)
        expected[0] = original[0, 3]
        for j, physical in enumerate(timeline['source_indices']):
            expected[1 + timeline['assignments'][1, j]] += original[0, physical] * timeline['contribution_gains'][1, j]
        np.testing.assert_allclose(mixed[0], expected, atol=1e-7)
        self.assertTrue(all(p.gain == 1 for p in encoded.at(2000)))

    def test_native_reduction_report_and_dolby_metadata_backups(self):
        encoder = Path('.tools/bin/avs3Encoder.exe').resolve()
        if not encoder.is_file():
            self.skipTest('Native encoder not built')
        logic_fixture(self.wav, objects=56)
        output = self.root / 'clustered.m4a'
        report = convert(self.wav, output, encoder=encoder, spatial_reduction=True)
        self.assertEqual(report['source']['objects'], 56)
        self.assertEqual((report['encoded_channels'], report['encoded_objects']), (16, 15))
        self.assertEqual(report['bed_channels_preserved'], 1)
        self.assertLess(report['objects_preserved'], 56)
        self.assertTrue(report['spatial_reduction']['lossy'])
        self.assertEqual(output.with_suffix('.source.dbmd.bin').read_bytes(), b'Synthetic Dolby metadata fixture')
        self.assertEqual(len(report['metadata_backups']), 3)
        self.assertTrue(output.with_suffix('.reduction.npz').is_file())
        self.assertEqual(report['bed_layout'], 'LFE')


if __name__ == '__main__':
    unittest.main()
