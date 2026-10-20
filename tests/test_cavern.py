"""Presentation parsing, failure boundaries, and optional local JOC receipts."""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import wave
import numpy as np
from av3s.cavern import presentation, run, export_input, convert_input, exporter_path
from av3s.pipeline import Cancelled
from av3s.mp4 import box, full, copy_tags
from av3s.scene import load_scene
from av3s.waveio import Wave


class CavernTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cavern-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def container(self, edits=1, rate=1, version=0):
        head = b'\0' * (16 if version else 8)
        movie = full(b'mvhd', head + struct.pack('>I', 1000) + b'\0' * 16, version=version)
        media = full(b'mdhd', head + struct.pack('>I', 48000) + b'\0' * 16, version=version)
        stsd = full(b'stsd', struct.pack('>I', 1) + box(b'ec-3', b'\0' * 28))
        mdia = box(b'mdia', media + box(b'minf', box(b'stbl', stsd)))
        entry = struct.pack('>Qqhh' if version else '>Iihh', 100, 3069, rate, 0)
        elst = full(b'elst', struct.pack('>I', edits) + entry * edits, version=version)
        p = self.root / 'timing.m4a'
        p.write_bytes(box(b'ftyp', b'isom' + b'\0' * 4) + box(b'moov', movie + box(b'trak', box(b'edts', elst) + mdia)))
        return p

    def test_mp4_edit_versions_and_media_offset(self):
        for version in (0, 1):
            info = presentation(self.container(version=version), 10000)
            self.assertEqual(info, dict(media_start_samples=3069, samples=4800, edit_list_applied=True))

    def test_complex_edits_and_out_of_range_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '多段'):
            presentation(self.container(edits=2), 10000)
        with self.assertRaisesRegex(ValueError, '变速'):
            presentation(self.container(rate=2), 10000)
        with self.assertRaisesRegex(ValueError, '有效区间'):
            presentation(self.container(), 3069)

    def test_movie_tick_rounding_clamps_only_unavailable_tail(self):
        for version in (0, 1):
            # 100 movie ticks request 4800 samples. The track's real end is
            # 16 samples earlier, within one tick at the 1000 Hz movie scale.
            info = presentation(self.container(version=version), 3069 + 4784)
            self.assertEqual(info['media_start_samples'], 3069)
            self.assertEqual(info['samples'], 4784)
            self.assertEqual(info['requested_edit_samples'], 4800)
            self.assertEqual(info['unavailable_tail_samples'], 16)

    def test_full_duration_edit_after_priming_preserves_start(self):
        # Muxers sometimes retain the full media duration as edit duration,
        # despite a nonzero media start. Only the overlapping media is exported.
        info = presentation(self.container(), 4800)
        self.assertEqual(info['media_start_samples'], 3069)
        self.assertEqual(info['samples'], 1731)
        self.assertEqual(info['unavailable_tail_samples'], 3069)

    def test_cancel_before_subprocess_and_no_overwrite(self):
        flag = self.root / 'cancel'
        flag.touch()
        with self.assertRaises(Cancelled):
            run('inspect', self.root / 'missing.ec3', cancel_file=flag)
        output = self.root / 'exists.wav'
        output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, '已存在'):
            export_input(self.root / 'missing.ec3', output)
        self.assertEqual(output.read_bytes(), b'keep')
        source = self.root / 'source.m4a'
        source.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, '覆盖'):
            convert_input(source, source)
        self.assertEqual(source.read_bytes(), b'keep')

    def test_non_joc_does_not_silently_fall_back_to_channels(self):
        try:
            exporter_path()
        except ValueError:
            self.skipTest('Cavern component not built')
        p = self.root / 'stereo.wav'
        with wave.open(str(p), 'wb') as f:
            f.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            f.writeframes(b'\0' * 4096)
        with self.assertRaisesRegex(ValueError, '不回退'):
            run('inspect', p)

    @unittest.skipUnless(os.environ.get('AV3S_TEST_JOC'), 'Optional private JOC input supplied only locally')
    def test_real_joc_cut_pcm_metadata_tags_and_cancellation(self):
        source = Path(os.environ['AV3S_TEST_JOC'])
        raw, cut = self.root / 'raw.wav', self.root / 'cut.wav'
        run('export', source, raw, 0, 8192)
        run('export', source, cut, 3069, 4801)
        a, b = Wave.open(raw), Wave.open(cut)
        np.testing.assert_array_equal(a.read(3069, 4801), b.read(0, 4801))
        scene = load_scene(b, cut.with_suffix('.scene.jsonl'))
        self.assertEqual(scene.channels, a.channels)
        self.assertTrue(any(p.gain != 1 for p in scene.at(0)))
        out = self.root / 'vivid.m4a'
        report = convert_input(source, out, duration=.025)
        self.assertEqual(report['input'], str(source.resolve()))
        self.assertEqual(report['output_samples'], 1200)
        self.assertEqual(report['validation']['crc_failures'], 0)
        self.assertEqual(copy_tags(out), copy_tags(source))
        self.assertTrue(out.with_suffix('.source.adm.xml').is_file())
        self.assertTrue(out.with_suffix('.source.cavern.json').is_file())
        self.assertIn('sparse_workaround', report['cavern_import']['source'])
        flag = self.root / 'cancel-running'
        def events(**event):
            if event.get('phase') == 'decode':
                flag.touch()
        cancelled = self.root / 'cancelled.wav'
        with self.assertRaises(Cancelled):
            export_input(source, cancelled, start=30, duration=1, cancel_file=flag, emit=events)
        self.assertFalse(list(self.root.glob('cancelled.*')))


if __name__ == '__main__':
    unittest.main()
