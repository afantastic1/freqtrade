import pytest

from data_engineering.registries import (
    AssetIdentity,
    AssetRegistry,
    MetricRegistry,
)


def test_source_aliases_keep_token_and_ecosystem_economic_entities_separate():
    assets = AssetRegistry()

    token = assets.resolve_source_id("coinmetrics-community", "btc")
    ecosystem = assets.resolve_source_id("defillama-chain-tvl", "chain:Bitcoin")

    assert (token.asset_id, token.tradable_asset_id, token.entity_scope) == (
        "token:BTC", "BTC", "token"
    )
    assert (ecosystem.asset_id, ecosystem.tradable_asset_id, ecosystem.entity_scope) == (
        "ecosystem:Bitcoin", "BTC", "ecosystem"
    )
    assert token.asset_id != ecosystem.asset_id


def test_registry_resolves_all_five_tradable_markets():
    assets = AssetRegistry()

    assert [
        assets.resolve_symbol(f"{ticker}/USDT:USDT") for ticker in
        ("BTC", "ETH", "SOL", "BNB", "XRP")
    ] == ["BTC", "ETH", "SOL", "BNB", "XRP"]


def test_coinmetrics_network_metrics_belong_to_chain_not_token():
    assets = AssetRegistry()
    metrics = MetricRegistry()

    definition = metrics.resolve("coinmetrics-community", "TxCnt", "chain")
    chain = assets.resolve_source_id(
        "coinmetrics-community", "btc", entity_scope=definition.entity_scope
    )

    assert definition.entity_scope == "chain"
    assert chain.asset_id == "chain:Bitcoin"
    assert chain.tradable_asset_id == "BTC"
    assert chain.asset_id != assets.resolve_source_id(
        "coinmetrics-community", "btc"
    ).asset_id


def test_protocol_is_a_supported_entity_scope():
    identity = AssetIdentity(
        asset_id="protocol:example",
        tradable_asset_id="ETH",
        entity_scope="protocol",
        source="defillama-protocol",
        source_asset_id="example-protocol",
    )
    assert identity.entity_scope == "protocol"
    with pytest.raises((AttributeError, TypeError)):
        identity.entity_scope = "token"


def test_registry_definitions_cannot_be_mutated():
    with pytest.raises(TypeError):
        AssetRegistry().entries[("coinmetrics-community", "doge")] = None
    with pytest.raises(TypeError):
        MetricRegistry().definitions[("coinmetrics-community", "unregistered")] = None


def test_unknown_or_cross_scope_source_mapping_fails_closed():
    assets = AssetRegistry()

    with pytest.raises(ValueError, match="unknown source asset"):
        assets.resolve_source_id("coinmetrics-community", "chain:Bitcoin")
    with pytest.raises(ValueError, match="unknown source asset"):
        assets.resolve_source_id("coinmetrics-community", "DOGE")
    with pytest.raises(ValueError, match="unknown market symbol"):
        assets.resolve_symbol("DOGE/USDT:USDT")


def test_metric_registry_checks_source_and_entity_scope():
    metrics = MetricRegistry()

    definition = metrics.resolve(
        "defillama-chain-fees-overview", "ecosystem_daily_fees_usd", "ecosystem"
    )
    assert definition.unit == "USD"
    assert definition.entity_scope == "ecosystem"
    with pytest.raises(ValueError, match="not registered for source"):
        metrics.resolve("coinmetrics-community", "ecosystem_daily_fees_usd", "ecosystem")
    with pytest.raises(ValueError, match="not registered for entity scope"):
        metrics.resolve("coinmetrics-community", "HashRate", "ecosystem")
    with pytest.raises(ValueError, match="unknown metric"):
        metrics.resolve("coinmetrics-community", "made_up_metric", "token")
