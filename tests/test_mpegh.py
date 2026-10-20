"""Synthetic MPEG-H integration tests; no music or private decoder modifications."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from av3s import cavern, inputs, mpegh
from av3s.mp4 import box, full, copy_tags
from av3s.pipeline import Cancelled
from av3s.scene import load_scene
from av3s.waveio import Wave

FIXTURES = Path(__file__).with_name('fixtures') / 'mpegh'
ROOT = Path(__file__).resolve().parents[1]


class MpeghTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mpegh-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def native(self):
        try:
            mpegh.decoder_path()
        except ValueError:
            self.skipTest('MPEG-H component not built')

    def test_dispatch_uses_sample_entry_not_song_name(self):
        for name in ('moving4-mha1.mp4', 'moving4-mhm1.mp4', 'moving4-sce.mhas'):
            self.assertIs(inputs.backend(FIXTURES / name), mpegh)
        fake = self.root / '360RA.m4a'
        fake.write_bytes(box(b'ftyp', b'isom' + bytes(4)) + box(b'moov',
            box(b'trak', box(b'mdia', box(b'minf', box(b'stbl',
                full(b'stsd', b'\0\0\0\1' + box(b'ec-3', bytes(28)))))))))
        self.assertIs(inputs.backend(fake), cavern)

    def test_bad_snapshot_never_silently_moves_objects(self):
        path = self.root / 'oam.jsonl'
        base = [0, -120, 0, 3, 32, 0, 0, 0, 0, 0, 0, 0]
        for values, sample in ((base, 64), (base[:-1], 0),
                               ([1] + base[1:], 0), ([0, 1e300] + base[2:], 0)):
            path.write_text(json.dumps(dict(sample=sample, period=1024, objects=[values])), encoding='utf-8')
            with self.assertRaises(ValueError):
                list(mpegh.oam_frames(path))

    def test_complete_intra_state_and_coordinate_handedness(self):
        self.native()
        source = ROOT / '.tools/libmpegh/smoke_test_suite/inp/sine_1khz_cicp16.mhas'
        if not source.is_file():
            self.skipTest('Upstream smoke vector not available')
        info = mpegh.run(source, self.root)
        frames = list(mpegh.oam_frames(self.root / 'oam.jsonl'))
        # Stock external .bs omits the initial azimuth on this intra-only stream.
        self.assertEqual(frames[0][3][0]['azimuth'], -180)
        self.assertEqual(info['raw_samples'], 480256)
        p = dict(frames[0][3][0], azimuth=90, elevation=0)
        mapped = mpegh.mapped([p], [0], 1)[0]
        self.assertAlmostEqual(mapped['x'], -1)
        self.assertAlmostEqual(mapped['y'], 0)
        self.assertEqual(mapped['gain'], 1)

    def test_dynamic_pcm_carriers_and_non_aligned_cut(self):
        self.native()
        source = FIXTURES / 'moving4-sce.mhas'
        a, b = self.root / 'all', self.root / 'cut'
        a.mkdir(); b.mkdir()
        full_wav, full_meta, _ = mpegh.decode(source, a)
        cut_wav, cut_meta, report = mpegh.decode(source, b, start=3069 / 48000, duration=4801 / 48000)
        with wave.open(str(full_wav)) as f:
            f.setpos(3069)
            expected = f.readframes(4801)
        with wave.open(str(cut_wav)) as f:
            self.assertEqual(f.getnchannels(), 4)
            self.assertEqual(f.getsampwidth(), 3)
            self.assertEqual(f.getnframes(), 4801)
            self.assertEqual(f.readframes(4801), expected)
        self.assertFalse(report['pcm_gain_baked'])
        w = Wave.open(full_wav)
        pcm = w.read(2048, 12288)
        peaks = np.fft.rfftfreq(len(pcm), 1 / 48000)[np.abs(np.fft.rfft(pcm, axis=0)).argmax(axis=0)]
        np.testing.assert_allclose(peaks, [300, 373, 446, 519], atol=4)
        scene = load_scene(w, full_meta)
        cut_scene = load_scene(Wave.open(cut_wav), cut_meta)
        self.assertEqual(scene.at(3069), cut_scene.at(0))
        self.assertNotEqual(scene.at(0)[0].x, scene.at(15360)[0].x)
        self.assertAlmostEqual(scene.at(0)[0].gain, .63095734448)
        self.assertNotEqual(scene.at(0)[0].gain, scene.at(15360)[0].gain)

    def test_mha1_mhm1_and_cpe_match_unwrapped_pcm(self):
        self.native()
        raw = self.root / 'raw'; raw.mkdir()
        mpegh.run(FIXTURES / 'moving4-sce.mhas', raw)
        expected = (raw / 'objects.pcm').read_bytes()
        for name in ('moving4-mha1.mp4', 'moving4-mhm1.mp4'):
            work = self.root / name; work.mkdir()
            info = mpegh.run(FIXTURES / name, work)
            self.assertEqual(info['presentation']['media_start_samples'], 1600)
            self.assertEqual(info['samples'], 14784)
            self.assertEqual((work / 'objects.pcm').read_bytes(), expected)
        work = self.root / 'cpe'; work.mkdir()
        self.assertEqual(mpegh.run(FIXTURES / 'moving4.mhas', work)['objects'], 4)

    def test_lower_hemisphere_is_not_flattened(self):
        self.native()
        wav, meta, _ = mpegh.decode(FIXTURES / 'below4-sce.mhas', self.root)
        scene = load_scene(Wave.open(wav), meta)
        self.assertAlmostEqual(scene.at(0)[0].z, -.5)
        self.assertLess(scene.at(0)[1].z, 0)
        self.assertEqual(scene.at(0)[2].z, 0)
        self.assertGreater(scene.at(0)[3].z, 0)
        if (ROOT / '.tools/bin/avs3Encoder.exe').is_file():
            report = inputs.convert_input(FIXTURES / 'below4-sce.mhas', self.root / 'below.m4a', duration=.05)
            self.assertEqual(report['validation']['crc_failures'], 0)
            mapped = json.loads((self.root / 'below.source.scene.jsonl').read_text(encoding='utf-8').splitlines()[0])
            self.assertAlmostEqual(mapped['objects'][0]['z'], -.5)

    def test_subframe_updates_and_cut_timeline_remain_contiguous(self):
        self.native()
        source = FIXTURES / 'subframe4-sce.mhas'
        raw = self.root / 'raw'; raw.mkdir()
        info = mpegh.run(source, raw)
        frames = list(mpegh.oam_frames(raw / 'oam.jsonl'))
        self.assertEqual(info['metadata_updates'], 64)
        self.assertEqual([r[0] for r in frames], list(range(0, 16384, 256)))
        self.assertTrue(all(r[1] == 256 for r in frames))
        self.assertGreater(len({r[3][0]['azimuth'] for r in frames}), 8)
        cut = self.root / 'cut'; cut.mkdir()
        wav, meta, _ = mpegh.decode(source, cut, start=317 / 48000, duration=1901 / 48000)
        scene = load_scene(Wave.open(wav), meta)
        expected = mpegh.mapped(frames[1][3], frames[1][2], info['radius_normalization'])[0]
        self.assertEqual(scene.at(0)[0].x, expected['x'])
        self.assertEqual(scene.at(0)[0].gain, expected['gain'])

    def test_export_keeps_originals_hashes_and_rolls_back_failure(self):
        self.native()
        output = self.root / 'objects.wav'
        report = inputs.export_input(FIXTURES / 'moving4-sce.mhas', output, duration=.05)
        for attachment in report['metadata_files']:
            path = Path(attachment['path'])
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), attachment['sha256'])
        with self.assertRaisesRegex(ValueError, '已存在'):
            mpegh.export_input(FIXTURES / 'moving4-sce.mhas', output)
        with patch('av3s.mpegh.shutil.copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                mpegh.export_input(FIXTURES / 'moving4-sce.mhas', self.root / 'failed.wav')
        self.assertFalse(list(self.root.glob('failed.*')))

    def test_cancellation_publishes_nothing(self):
        self.native()
        flag = self.root / 'cancel'
        def events(**event):
            if event.get('phase') == 'decode':
                flag.touch()
        with self.assertRaises(Cancelled):
            mpegh.export_input(FIXTURES / 'moving20-sce.mhas', self.root / 'cancelled.wav',
                               cancel_file=flag, emit=events)
        self.assertFalse(list(self.root.glob('cancelled.*')))

    def test_vivid_tags_metadata_and_explicit_twenty_object_reduction(self):
        self.native()
        if not (ROOT / '.tools/bin/avs3Encoder.exe').is_file():
            self.skipTest('Native Vivid encoder not built')
        # Relocate moov to the end and leave equal-size padding at its old
        # location, so opaque tags can be added without relocating audio.
        original = (FIXTURES / 'moving4-mhm1.mp4').read_bytes()
        from av3s.mp4 import atoms
        movie = next(a for a in atoms(original) if a[0] == b'moov')
        udta = box(b'udta', full(b'meta', bytes(4)))
        source = self.root / '输入.m4a'
        source.write_bytes(original[:movie[1]] + box(b'free', bytes(movie[3] - movie[2]))
                           + original[movie[3]:] + box(b'moov', original[movie[2]:movie[3]] + udta))
        output = self.root / 'vivid.m4a'
        report = inputs.convert_input(source, output, duration=.05)
        self.assertEqual(report['validation']['crc_failures'], 0)
        self.assertEqual(report['objects_preserved'], 4)
        self.assertEqual(copy_tags(output), udta)
        for attachment in report['mpegh_import']['metadata_files']:
            path = Path(attachment['path'])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), attachment['sha256'])
        twenty = FIXTURES / 'moving20-sce.mhas'
        with self.assertRaisesRegex(ValueError, '--spatial-reduction'):
            inputs.convert_input(twenty, self.root / 'refused.m4a')
        self.assertFalse(list(self.root.glob('refused.*')))
        report = inputs.convert_input(twenty, self.root / 'reduced.m4a', spatial_reduction=True)
        self.assertEqual(report['validation']['crc_failures'], 0)
        self.assertEqual(report['encoded_objects'], 15)
        self.assertTrue(report['spatial_reduction']['lossy'])
        self.assertEqual(len(report['spatial_reduction']['sources']), 20)
        self.assertTrue(Path(report['spatial_reduction']['timeline']['path']).is_file())


if __name__ == '__main__':
    unittest.main()
