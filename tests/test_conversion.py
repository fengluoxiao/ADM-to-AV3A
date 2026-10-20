import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import wave
from av3s.waveio import Wave
from av3s.scene import load_scene
from av3s.pipeline import convert, Cancelled
from av3s.mp4 import atoms, Bits, crc16, box, full, copy_tags


def riff_chunk(kind, data):
    return struct.pack('<4sI', kind, len(data)) + data + (b'\0' if len(data) & 1 else b'')


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='av3s-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wav = self.root / '母版.wav'
        with wave.open(str(self.wav), 'wb') as f:
            f.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            f.writeframes(struct.pack('<hh', 200, -100) * 4800)
        self.scene = self.root / '空间.jsonl'
        self.scene.write_text(json.dumps(dict(sample=0, objects=[dict(x=-.5, y=.5, z=0), dict(x=.5, y=.5, z=.5)])), encoding='utf-8')

    def test_wav_and_scene_validation(self):
        w = Wave.open(self.wav)
        self.assertEqual(w.samples, 4800)
        self.assertAlmostEqual(float(w.read(0, 1)[0, 0]), 200 / 32768)
        scene = load_scene(w, self.scene)
        self.assertEqual(scene.objects, 2)
        self.assertEqual(scene.at(4000)[0].x, -.5)
        self.scene.write_text(json.dumps(dict(sample=0, objects=[dict(x=3), {}])), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '范围'):
            load_scene(w, self.scene)

    def test_truncated_chunk_rejected(self):
        self.wav.write_bytes(self.wav.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, '截断'):
            Wave.open(self.wav)

    def test_rf64_trailing_adm_and_chna_track_order(self):
        xml = b'''<audioFormatExtended>
<audioTrackUID UID="ATU_00000001"><audioChannelFormatIDRef>AC_00031001</audioChannelFormatIDRef></audioTrackUID>
<audioTrackUID UID="ATU_00000002"><audioChannelFormatIDRef>AC_00031002</audioChannelFormatIDRef></audioTrackUID>
<audioChannelFormat audioChannelFormatID="AC_00031001" typeLabel="0003"><audioBlockFormat><cartesian>1</cartesian><position coordinate="X">-0.5</position><position coordinate="Y">0.5</position><position coordinate="Z">0</position></audioBlockFormat></audioChannelFormat>
<audioChannelFormat audioChannelFormatID="AC_00031002" typeLabel="0003"><audioBlockFormat><cartesian>1</cartesian><position coordinate="X">0.5</position><position coordinate="Y">0.5</position><position coordinate="Z">0</position></audioBlockFormat></audioChannelFormat>
</audioFormatExtended>'''
        chna = struct.pack('<HH', 2, 2)
        for index, uid, channel in [(2, b'ATU_00000001', b'AC_00031001'), (1, b'ATU_00000002', b'AC_00031002')]:
            chna += struct.pack('<H12s14s11sB', index, uid, channel, b'', 0)
        pcm = b'\0' * (100 * 4)
        body = riff_chunk(b'ds64', struct.pack('<QQQI', 0, len(pcm), 100, 0))
        body += riff_chunk(b'fmt ', struct.pack('<HHIIHH', 1, 2, 48000, 192000, 4, 16))
        body += b'data' + struct.pack('<I', 0xffffffff) + pcm
        body += riff_chunk(b'axml', xml) + riff_chunk(b'chna', chna)
        self.wav.write_bytes(b'RF64' + struct.pack('<I', 0xffffffff) + b'WAVE' + body)
        scene = load_scene(Wave.open(self.wav))
        self.assertEqual([p.x for p in scene.at(0)], [.5, -.5])

    def test_adm_interpolates_block_endpoints(self):
        from av3s.scene import load_adm
        w = Wave.open(self.wav)
        w.chunks[b'chna'] = struct.pack('<HH', 2, 2) + b''.join(
            struct.pack('<H12s14s11sB', i, f'ATU_{i:08d}'.encode(), f'AC_0003100{i}'.encode(), b'', 0) for i in (1, 2))
        blocks = '<audioBlockFormat rtime="00:00:00.000" duration="00:00:00.050"><cartesian>1</cartesian><position coordinate="X">0</position><position coordinate="Y">0</position><position coordinate="Z">0</position></audioBlockFormat><audioBlockFormat rtime="00:00:00.050" duration="00:00:00.050"><cartesian>1</cartesian><position coordinate="X">1</position><position coordinate="Y">0</position><position coordinate="Z">0</position></audioBlockFormat>'
        xml = '<audioFormatExtended>' + ''.join(f'<audioTrackUID UID="ATU_{i:08d}"><audioChannelFormatIDRef>AC_0003100{i}</audioChannelFormatIDRef></audioTrackUID><audioChannelFormat audioChannelFormatID="AC_0003100{i}" typeLabel="0003">{blocks}</audioChannelFormat>' for i in (1, 2)) + '</audioFormatExtended>'
        s = load_adm(xml.encode(), w, 'test')
        self.assertAlmostEqual(s.at(3600)[0].x, .5)
        self.assertEqual(s.at(2400)[0].x, 0)

    def test_cancel_and_existing_output(self):
        output = self.root / 'out.m4a'
        output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, '已存在'):
            convert(self.wav, output, self.scene)
        self.assertEqual(output.read_bytes(), b'keep')
        output.unlink()
        encoder = Path(os.environ.get('AV3S_TEST_ENCODER', '.tools/bin/avs3Encoder.exe')).resolve()
        if not encoder.is_file():
            self.skipTest('Native encoder not built')
        flag = self.root / 'cancel'
        flag.touch()
        with self.assertRaises(Cancelled):
            convert(self.wav, output, self.scene, encoder=encoder, cancel_file=flag)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix('.conversion.json').exists())

    def test_native_encoder_roundtrip_and_tags(self):
        encoder = Path(os.environ.get('AV3S_TEST_ENCODER', '.tools/bin/avs3Encoder.exe')).resolve()
        if not encoder.is_file():
            self.skipTest('Native encoder not built')
        output = self.root / '成品.m4a'
        udta = box(b'udta', full(b'meta', b'\0' * 4))
        tag_source = self.root / 'tags.m4a'
        tag_source.write_bytes(box(b'ftyp', b'isom' + b'\0' * 4) + box(b'moov', udta))
        report = convert(self.wav, output, self.scene, tags=tag_source, encoder=encoder)
        self.assertEqual(report['objects_preserved'], 2)
        self.assertEqual(report['validation']['crc_failures'], 0)
        self.assertEqual(copy_tags(output), udta)
        self.assertEqual(output.with_suffix('.source.scene.jsonl').read_bytes(), self.scene.read_bytes())
        data = output.read_bytes()
        mdat = next(a for a in atoms(data) if a[0] == b'mdat')
        frame = data[mdat[2]:mdat[2] + 512]
        b = Bits(frame)
        b.read(27)
        high = b.read(8)
        b.read(15)
        low = b.read(8)
        self.assertEqual(crc16(frame[8:]), high << 8 | low)
        self.assertEqual(report['output_samples'], 4800)
        self.assertTrue(output.with_suffix('.conversion.json').exists())

    def test_cancel_during_native_encoding_cleans_outputs(self):
        encoder = Path(os.environ.get('AV3S_TEST_ENCODER', '.tools/bin/avs3Encoder.exe')).resolve()
        if not encoder.is_file():
            self.skipTest('Native encoder not built')
        output = self.root / 'cancelled.m4a'
        flag = self.root / 'cancel-running'

        def events(**event):
            if event.get('phase') == 'encode':
                flag.touch()

        with self.assertRaises(Cancelled):
            convert(self.wav, output, self.scene, encoder=encoder, cancel_file=flag, emit=events)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix('.conversion.json').exists())
        self.assertFalse(list(self.root.glob('*.partial')))


if __name__ == '__main__':
    unittest.main()
