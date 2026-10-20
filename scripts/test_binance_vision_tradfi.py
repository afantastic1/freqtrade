"""Offline tests for binance_vision_tradfi.py (no network / Binance credentials)."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).with_name('binance_vision_tradfi.py')
spec = importlib.util.spec_from_file_location('vision_v2', SCRIPT)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def make_zip(month='2026-09', duplicate=False):
    ts1, ts2 = 1788220800000, 1788224400000  # 2026-09-01T00/01 UTC
    if duplicate:
        ts2 = ts1
    data = '\n'.join(
        f'{stamp},100,103,99,102,12,{stamp+3599999},1224,8,4,408,0'
        for stamp in (ts1, ts2)
    ) + '\n'
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('CLUSDT-1h-2026-09.csv', data)
    return b.getvalue()


class ImporterTest(unittest.TestCase):
    def test_curated_scope_and_explicit_symbols(self):
        class Obj:
            symbols = None
            category = 'semiconductor'
            all = False
        selected = v.selected_symbols(Obj(), v.load_catalog(None))
        self.assertIn('NVDAUSDT', selected)
        self.assertNotIn('BTCUSDT', selected)
        self.assertNotIn('CLUSDT', selected)
        Obj.symbols = ['BTCUSDT', 'BTCUSDT']
        self.assertEqual(v.selected_symbols(Obj(), v.load_catalog(None)), ['BTCUSDT'])

    def test_catalog_rejects_invalid_types(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / 'catalog.json'
            p.write_text('{"oil": ["CLUSDT", "NEWOILUSDT"], "crypto": ["BTCUSDT"]}')
            with self.assertRaises(ValueError):
                v.load_catalog(p)
            p.write_text('{"oil": ["CLUSDT", "NEWOILUSDT"]}')
            self.assertIn('NEWOILUSDT', v.load_catalog(p)['oil'])
            self.assertIn('BZUSDT', v.load_catalog(p)['oil'])

    def test_s3_listing_parses_and_paginates(self):
        prefix = v.S3_ROOT_PREFIX
        def xml(keys, truncated, marker=''):
            parts = [f'<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>' for p in keys]
            return ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    + '<IsTruncated>' + str(truncated).lower() + '</IsTruncated>'
                    + f'<NextMarker>{marker}</NextMarker>'
                    + ''.join(parts) + '</ListBucketResult>').encode()
        payloads = [
            xml([prefix+'CLUSDT/'], True, prefix+'CLUSDT/'),
            xml([prefix+'NVDAUSDT/'], False)
        ]
        def fake_fetch(url, **kw):
            self.assertEqual(kw['method'], 'GET')
            return 200, payloads.pop(0)
        with patch.object(v, 'fetch', side_effect=fake_fetch), patch.object(v.time, 'sleep'):
            self.assertEqual(v.discover_archive_symbols(), ['CLUSDT', 'NVDAUSDT'])
        self.assertFalse(payloads)

    def test_s3_listing_does_not_accept_html(self):
        with patch.object(v, 'fetch', return_value=(200, b'<html></html>')):
            with self.assertRaises(ValueError):
                v.discover_archive_symbols()

    def test_discover_months(self):
        prefix = v.S3_ROOT_PREFIX+'CLUSDT/1h/'
        with patch.object(v, 's3_list_prefixes', return_value=[
            prefix+'CLUSDT-1h-2026-09.zip',
            prefix+'CLUSDT-1h-2026-09.zip.CHECKSUM',
            prefix+'CLUSDT-1h-2026-08.zip',
            prefix+'CLUSDT-1h-2026-09.zip.bogus',
        ]):
            self.assertEqual(v.discover_archive_months('CLUSDT', '1h'), ['2026-08', '2026-09'])

    def test_zip_sha_and_market_data(self):
        raw = make_zip()
        digest = hashlib.sha256(raw).hexdigest()
        checksum = f'{digest}  CLUSDT-1h-2026-09.zip\n'.encode()
        def fake_fetch(url, **kw):
            return (200, checksum) if url.endswith('.CHECKSUM') else (200, raw)
        with tempfile.TemporaryDirectory() as t, patch.object(v, 'fetch', side_effect=fake_fetch):
            url, name = v.archive_url('CLUSDT', '1h', '2026-09')
            status, path, got = v.download_archive(url=url, name=name, raw_dir=Path(t))
            self.assertEqual(status, 'downloaded')
            self.assertEqual(got, digest)
            frame = v.parse_kline_zip(path, month='2026-09', timeframe='1h')
            self.assertEqual(len(frame), 2)
            self.assertEqual(v.gaps_summary(frame, '1h')['gap_bars'], 0)
            status, *_ = v.download_archive(url=url, name=name, raw_dir=Path(t))
            self.assertEqual(status, 'cached')
        with tempfile.TemporaryDirectory() as t:
            file = Path(t)/'bad.zip'
            file.write_bytes(make_zip(duplicate=True))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                v.parse_kline_zip(file, month='2026-09', timeframe='1h')

    def test_discovery_auto_start_and_excludes_unclassified(self):
        with tempfile.TemporaryDirectory() as t, \
             patch.object(v, 'discover_archive_symbols', return_value=['CLUSDT', 'BTCUSDT']), \
             patch.object(v, 'discover_archive_months', return_value=['2026-09']):
            status = v.main(['--discover', '--category', 'oil', '--check-only',
                             '--datadir', t, '--end', '2026-09'])
            self.assertEqual(status, 0)
            report = json.loads((Path(t)/'vision_import_report.json').read_text())
            self.assertEqual(report['discovery']['status'], 'ok')
            self.assertIn('BTCUSDT', report['discovery']['unclassified_symbols'])
            self.assertTrue(all(r['symbol'] != 'BTCUSDT' for r in report['archives']))
            self.assertEqual([r['month'] for r in report['archives'] if r['symbol']=='CLUSDT'], ['2026-09'])

    def test_discovery_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as t, \
             patch.object(v, 'discover_archive_symbols', side_effect=RuntimeError('forbidden')):
            status = v.main(['--discover', '--category', 'oil', '--check-only',
                             '--datadir', t, '--end', '2026-09'])
            self.assertEqual(status, 1)
            report = json.loads((Path(t)/'vision_import_report.json').read_text())
            self.assertEqual(report['discovery']['status'], 'FAILED')
            self.assertEqual(report['archives'], [])

    def test_known_archive_checksum_problem_blocks_pair_export(self):
        with tempfile.TemporaryDirectory() as t, \
             patch.object(v, 'discover_archive_symbols', return_value=['CLUSDT']), \
             patch.object(v, 'discover_archive_months', return_value=['2026-09']), \
             patch.object(v, 'download_archive', return_value=('missing_checksum', None, None)):
            status = v.main(['--discover', '--symbols', 'CLUSDT', '--datadir', t,
                             '--start', '2026-09', '--end', '2026-09'])
            self.assertEqual(status, 1)
            report = json.loads((Path(t)/'vision_import_report.json').read_text())
            self.assertEqual(report['datasets'][0]['status'], 'BLOCKED_ON_ERROR')

if __name__ == '__main__':
    unittest.main(verbosity=2)
