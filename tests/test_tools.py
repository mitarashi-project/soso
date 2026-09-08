import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load('audit', 'tools/live2d_asset_audit/audit.py')
capacity = load('capacity', 'tools/capacity.py')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / 'assets'
        self.assets.mkdir()

    def image(self, name, size=(32, 32), empty=False):
        im = Image.new('RGBA', size)
        if not empty:
            ImageDraw.Draw(im).ellipse((5, 5, 20, 20), fill=(100, 20, 50, 255))
        im.save(self.assets / name)

    def run_audit(self, config=None):
        return audit.audit(self.assets, self.root / 'report', config)

    def test_normal_and_read_only(self):
        self.image('part.png')
        before = audit.digest(self.assets / 'part.png')
        result = self.run_audit()
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['files'][0]['bbox'], [5, 5, 21, 21])
        self.assertEqual(before, audit.digest(self.assets / 'part.png'))
        for name in ('report.json', 'report.html', 'sha256.json'):
            text = (self.root / 'report' / name).read_text(encoding='utf-8')
            self.assertNotIn(str(self.root), text)

    def test_empty_rectangle_duplicate_pair_and_name(self):
        self.image('empty.png', empty=True)
        self.image('eye_L.png')
        self.image('copy.png')
        Image.new('RGB', (32, 32), 'white').save(self.assets / 'background.png')
        result = self.run_audit({'name_regex': 'eye_[LR]\\.png'})
        codes = {w['code'] for w in result['warnings']}
        self.assertTrue({'empty', 'full_rectangle', 'duplicate_image', 'duplicate_file', 'missing_pair', 'name', 'no_alpha'} <= codes)
        self.assertEqual(result['exit_code'], 1)

    def test_pair_size_tiny_and_configured_pair(self):
        self.image('eye_l.png')
        self.image('eye_r.png', (40, 40))
        im = Image.new('RGBA', (32, 32))
        im.putpixel((2, 2), (0, 0, 0, 255))
        im.save(self.assets / 'dot.png')
        codes = {w['code'] for w in self.run_audit({'pairs': [['a.png', 'b.png']]})['warnings']}
        self.assertTrue({'tiny', 'pair_size', 'missing_pair'} <= codes)

    def test_corrupt_continues(self):
        (self.assets / 'broken.png').write_bytes(b'broken')
        self.image('good.png')
        result = self.run_audit()
        self.assertEqual(result['exit_code'], 2)
        self.assertEqual(len(result['files']), 2)

    def test_output_protection(self):
        self.image('part.png')
        with self.assertRaises(ValueError):
            audit.audit(self.assets, self.assets / 'reports')
        self.run_audit()
        with self.assertRaises(ValueError):
            self.run_audit()

    def test_cli_exit_codes(self):
        self.image('normal.png')
        script = str(ROOT / 'tools/live2d_asset_audit/audit.py')
        for index, expected in enumerate([0, 1, 2]):
            if expected == 1:
                self.image('empty.png', empty=True)
            elif expected == 2:
                (self.assets / 'broken.png').write_bytes(b'broken')
            output = self.root / ('cli-' + str(index))
            run = subprocess.run([sys.executable, script, '--input', str(self.assets),
                                  '--output', str(output)], capture_output=True)
            self.assertEqual(run.returncode, expected, run.stderr)
            self.assertEqual(json.loads((output / 'report.json').read_text(encoding='utf-8'))['exit_code'], expected)

    def test_transparent_rgb_ignored(self):
        for name, color in [('one.png', (255, 0, 0, 0)), ('two.png', (0, 255, 0, 0))]:
            im = Image.new('RGBA', (32, 32), color)
            ImageDraw.Draw(im).ellipse((5, 5, 20, 20), fill=(10, 20, 30, 255))
            im.save(self.assets / name)
        result = self.run_audit()
        self.assertIn('duplicate_image', {w['code'] for w in result['warnings']})

    def test_palette_transparency(self):
        im = Image.new('P', (32, 32), 0)
        im.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        ImageDraw.Draw(im).ellipse((5, 5, 20, 20), fill=1)
        im.save(self.assets / 'palette.png', transparency=0)
        self.assertTrue(self.run_audit()['files'][0]['alpha'])

    def test_html_escape(self):
        self.image('part.png')
        result = self.run_audit({'pairs': [['<script>.png', 'b.png']]})
        page = (self.root / 'report' / 'report.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)

    def test_psd_generated_layers(self):
        try:
            from psd_tools import PSDImage
            from psd_tools.api.layers import PixelLayer
        except ImportError:
            self.skipTest('Optional psd-tools is not installed')
        psd = PSDImage.new('RGBA', (32, 32))
        PixelLayer.frompil(Image.new('RGBA', (32, 32)), psd, 'empty')
        layer = PixelLayer.frompil(Image.new('RGBA', (16, 16), 'red'), psd, 'paint')
        layer.visible = False
        psd.save(self.assets / 'dummy.psd')
        before = audit.digest(self.assets / 'dummy.psd')
        result = self.run_audit()
        self.assertEqual(result['errors'], [])
        layers = {l['name']: l for l in result['files'][0]['layers']}
        self.assertTrue(layers['empty']['empty'])
        self.assertFalse(layers['paint']['empty'])
        self.assertFalse(layers['paint']['visible'])
        self.assertEqual(before, audit.digest(self.assets / 'dummy.psd'))


class CapacityTests(unittest.TestCase):
    def test_status_boundaries_and_stale(self):
        now = datetime.now(timezone.utc)
        for remaining, expected in [(None, 'unknown'), (0, 'paused'), (20, 'paused'), (40, 'light_only'), (41, 'available')]:
            result = capacity.status('peer', remaining, observed_at=now.isoformat(), now=now)
            self.assertEqual(result['accepting_work'], expected)
        old = (now - timedelta(hours=7)).isoformat()
        self.assertEqual(capacity.status('peer', 99, observed_at=old, now=now)['accepting_work'], 'unknown')
        self.assertEqual(capacity.status('peer', 99, reset_at=old, observed_at=now.isoformat(), now=now)['accepting_work'], 'unknown')

    def test_invalid(self):
        for value in [-1, 101, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                capacity.status('peer', value)
        with self.assertRaises(ValueError):
            capacity.status('peer', 50)


if __name__ == '__main__':
    unittest.main()
