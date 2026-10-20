# ResearchRequest v2 and CampaignPolicy v1 acceptance

**Implementation HEAD before this report update:** `74a1c23a46c79037b7355060cfb9c74efce0c9f6`  
**Base:** `33438bc944`  
**Scope:** PR-01 contract, registry, governance, and CI changes only.

## Verification

| Command | Result |
| --- | --- |
| `/home/von/workspace/venv/bin/python -m pytest -o addopts='' -q alpha_lab_contracts/tests alpha_lab/tests/test_config.py alpha_lab_service/tests/test_manifest.py alpha_lab_service/tests/test_ci_protection.py` | PASS: 85 passed |
| `/home/von/workspace/venv/bin/python -m pytest -o addopts='' alpha_lab_contracts/tests alpha_lab/tests/test_config.py alpha_lab/tests/test_runner_research_key.py -q` | PASS: 82 passed; includes repeated v1.1 research-key reuse regression |
| `/home/von/workspace/venv/bin/python -m alpha_lab_service.governance` | PASS: v1.1 frozen core, v1.3 governance, and service v2 valid |
| `git diff --check 33438bc944..HEAD` | PASS: no whitespace errors |

The pytest command uses `-o addopts=''` because the available environment does not include pytest-xdist, while the repository defaults include its `--dist` option. The root project dependencies were installed into the workspace interpreter to run the legacy imports and tests.

## Manifest evidence

| Manifest | SHA-256 | Status |
| --- | --- | --- |
| `alpha_lab/freeze-manifest-v1.1.json` | `692eda36c07f9dfd113dc57d470f7db15507fc75321bc5a79bf5edf9ff9561c2` | Preserved byte-for-byte |
| `alpha_lab/freeze-manifest-v1.2.json` | `7a0df0e0889bc784d79f5b5b0fd5242105768e2b7b1de103b7eae69d1ea024fd` | Preserved byte-for-byte |
| `alpha_lab_service/protocol-manifest-v1.json` | `2b9121505500e6e64e653e73a0dc20c465b80731a0276b82cbeb9d8235be3291` | Preserved byte-for-byte |
| `alpha_lab/freeze-manifest-v1.3.json` | `e3cd4379741c3164c1af23a8785136e004a7605c570537d81cde149ac2bc9196` | New active governance manifest |
| `alpha_lab_service/protocol-manifest-v2.json` | `c2007224c47afec184cea115c81381673bae7626a533e5f8b946bbc5a8a47e02` | New active service manifest |

The three historical manifest digests above match their pinned baseline values and the post-change digests. The new manifests are generated from the committed contract files and current verifier/CI controls. The final review fix pass preserves policy fractional-second identity, rejects malformed offsets and unsupported timestamp precision, and makes authorization mappings immutable.

## Scope review

The implementation diff contains no changes under historical market data, labels, model/evaluation artifacts, frozen Alpha Lab code/tests, SQLite databases, Evidence, or Decisions. It does not change `alpha_lab/config.py`, legacy readers, inference, data loading, service adapter behavior, or trading behavior.

The example campaign is an authorization fixture only; it makes no claim about actual market-data coverage. Local Code Owner declarations are present for contract and manifest paths. Remote branch protection and required-review settings were not queried, so this report does not treat local ownership rules as proof of remote enforcement.
