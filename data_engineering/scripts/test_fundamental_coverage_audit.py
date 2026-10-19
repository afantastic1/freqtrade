import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

# Run from repository root after placing script in data_engineering/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fundamental_coverage_audit import _dates_and_entities, build_report, save_report
from data_engineering.catalog import publish


class FundamentalCoverageAuditTest(unittest.TestCase):
    def setUp(self):
        self.h = tempfile.TemporaryDirectory()
        self.addCleanup(self.h.cleanup)
        self.root = Path(self.h.name)

    def _create(self, name, rows, source):
        src = self.root / f"{name}.csv"
        with src.open("w", encoding="utf8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["asset_id", "metric", "event_time", "value", "published_at"])
            w.writerows(rows)
        return publish(self.root, dataset_id=name, kind="fundamental", source=source, input_path=src)

    def test_token_chain_and_ecosystem_are_not_conflated(self):
        self._create("token", [["bnb", "CapMrktEstUSD", "2022-01-01T00:00:00+00:00", "100", ""]], "coinmetrics-community")
        self._create("chain", [["bnb", "TxCnt", "2022-01-02T00:00:00+00:00", "50", ""]], "coinmetrics-community")
        self._create("ecosystem", [["chain:BSC", "ecosystem_daily_fees_usd", "2022-01-03T00:00:00+00:00", "20", ""]], "defillama-chain-fees-overview")
        results = build_report(self.root)
        assert not results['errors'], results['errors']
        assert len(results['metrics']) == 3
        assert {r['entity_scope'] for r in results['metrics']} == {'token', 'chain', 'ecosystem'}
        assert {r['asset_id'] for r in results['metrics']} == {'token:BNB', 'chain:BSC', 'ecosystem:BSC'}
        assert {r['asset'] for r in results['metrics']} == {'BNB'}
        assert all(r['research_safe'] is False for r in results['metrics'])

    def test_zero_padded_history_is_not_invented(self):
        self._create("solfees", [["chain:Solana", "ecosystem_daily_fees_usd", "2022-02-14T00:00:00+00:00", "20", ""]], "defillama-chain-fees-overview")
        r = build_report(self.root)['metrics'][0]
        assert r['rows'] == 1 and r['missing_at_start'] == 44
        assert r['internal_missing_days'] == 0
        assert r['coverage_pct'] < 1

    def test_integrity_check_catches_tampering(self):
        manifest = self._create("btc", [["btc", "AdrActCnt", "2022-01-01T00:00:00+00:00", "1", ""]], "coinmetrics-community")
        p = self.root / 'snapshots' / 'btc' / (manifest['sha256'] + '.csv')
        p.write_text(p.read_text().replace(',1,', ',2,'))
        report = build_report(self.root)
        assert len(report['errors']) == 1
        assert report['metrics'] == []

    def test_unknown_mapping_fails_closed(self):
        self._create('unknown', [["chain:UNKNOWN", "chain_tvl_usd", "2022-01-01T00:00:00+00:00", "100", ""]], "defillama-chain-tvl")
        report = build_report(self.root)
        assert report['errors'] and not report['metrics']

    def test_report_contains_only_development_metadata(self):
        self._create('xrpl', [["chain:XRPL", "chain_tvl_usd", "2024-03-23T00:00:00+00:00", "3", ""]], "defillama-chain-tvl")
        report = build_report(self.root)
        jp, cp = save_report(report, self.root / 'reports')
        d = json.loads(jp.read_text())
        assert d['window_end_exclusive'] == '2025-01-01'
        assert d['metrics'][0]['evaluation_status'] == 'EXPLORATORY_ONLY'
        assert cp.exists()


if __name__ == '__main__':
    unittest.main()
