"""Versioned economic entity and metric definitions for fundamental data."""

from dataclasses import dataclass
from types import MappingProxyType


ENTITY_SCOPES = frozenset({"token", "chain", "ecosystem", "protocol"})
ASSET_REGISTRY_VERSION = "asset-registry-v1"
METRIC_REGISTRY_VERSION = "metric-registry-v1"


@dataclass(frozen=True)
class AssetIdentity:
    asset_id: str
    tradable_asset_id: str | None
    entity_scope: str
    source: str
    source_asset_id: str

    def __post_init__(self):
        if self.entity_scope not in ENTITY_SCOPES:
            raise ValueError(f"unsupported entity scope: {self.entity_scope}")
        if not all((self.asset_id, self.source, self.source_asset_id)):
            raise ValueError("asset identity fields must be non-empty")


@dataclass(frozen=True)
class MetricDefinition:
    metric: str
    unit: str
    definition: str
    source: str
    entity_scope: str
    family: str


_TRADABLES = MappingProxyType({
    "BTC/USDT:USDT": "BTC",
    "ETH/USDT:USDT": "ETH",
    "SOL/USDT:USDT": "SOL",
    "BNB/USDT:USDT": "BNB",
    "XRP/USDT:USDT": "XRP",
})


def _asset_entries():
    entries = {}
    chains = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana",
              "BNB": "BSC", "XRP": "XRPL"}
    for ticker, chain in chains.items():
        entries["coinmetrics-community", ticker.lower(), "token"] = AssetIdentity(
            asset_id=f"token:{ticker}", tradable_asset_id=ticker,
            entity_scope="token", source="coinmetrics-community",
            source_asset_id=ticker.lower(),
        )
        entries["coinmetrics-community", ticker.lower(), "chain"] = AssetIdentity(
            asset_id=f"chain:{chain}", tradable_asset_id=ticker,
            entity_scope="chain", source="coinmetrics-community",
            source_asset_id=ticker.lower(),
        )
    ecosystems = {
        "Bitcoin": "BTC", "Ethereum": "ETH", "Solana": "SOL",
        "BSC": "BNB", "XRPL": "XRP",
    }
    for source in ("defillama-chain-tvl", "defillama-chain-fees-overview"):
        for chain, ticker in ecosystems.items():
            source_id = f"chain:{chain}"
            entries[source, source_id, "ecosystem"] = AssetIdentity(
                asset_id=f"ecosystem:{chain}", tradable_asset_id=ticker,
                entity_scope="ecosystem", source=source, source_asset_id=source_id,
            )
    return MappingProxyType(entries)


class AssetRegistry:
    """Maps source entity IDs and exchange symbols without collapsing scopes."""

    version = ASSET_REGISTRY_VERSION

    def __init__(self):
        self._entries = _asset_entries()
        self._tradables = _TRADABLES

    @property
    def entries(self):
        return self._entries

    @property
    def tradable_assets(self):
        return tuple(self._tradables.values())

    def resolve_source_id(
        self, source: str, source_asset_id: str, *, entity_scope: str | None = None
    ) -> AssetIdentity:
        if entity_scope is not None:
            try:
                return self._entries[source, source_asset_id, entity_scope]
            except KeyError as exc:
                raise ValueError(
                    f"unknown source asset: {source}/{source_asset_id} ({entity_scope})"
                ) from exc
        try:
            return self._entries[source, source_asset_id, "token"]
        except KeyError as exc:
            matches = [
                identity for (registered_source, registered_id, _), identity
                in self._entries.items()
                if registered_source == source and registered_id == source_asset_id
            ]
            if len(matches) == 1:
                return matches[0]
            raise ValueError(f"unknown source asset: {source}/{source_asset_id}") from exc

    def resolve_symbol(self, symbol: str) -> str:
        try:
            return self._tradables[symbol]
        except KeyError as exc:
            raise ValueError(f"unknown market symbol: {symbol}") from exc

    def matches_identity(
        self, source: str, asset_id: str, tradable_asset_id: str, entity_scope: str
    ) -> bool:
        return any(
            registered_source == source
            and identity.asset_id == asset_id
            and identity.tradable_asset_id == tradable_asset_id
            and identity.entity_scope == entity_scope
            for (registered_source, _, _), identity in self._entries.items()
        )


def _metric_definitions():
    rows = [
        ("AdrActCnt", "addresses", "Daily active-address count (source-defined)", "coinmetrics-community", "chain", "network_activity"),
        ("TxCnt", "transactions", "Daily on-chain transaction count (source-defined)", "coinmetrics-community", "chain", "network_activity"),
        ("SplyCur", "native_tokens", "Coin Metrics current supply (asset-specific definition)", "coinmetrics-community", "token", "tokenomics"),
        ("CapMrktCurUSD", "USD", "Coin Metrics current market capitalization", "coinmetrics-community", "token", "valuation"),
        ("CapMrktEstUSD", "USD", "Coin Metrics estimated market capitalization", "coinmetrics-community", "token", "valuation"),
        ("CapMVRVCur", "ratio", "MVRV using source-defined capitalization terms", "coinmetrics-community", "token", "valuation"),
        ("HashRate", "source_defined", "Proof-of-work hash rate", "coinmetrics-community", "chain", "network_security"),
        ("chain_tvl_usd", "USD", "DefiLlama chain ecosystem TVL; not token market capitalization", "defillama-chain-tvl", "ecosystem", "ecosystem_tvl"),
        ("ecosystem_daily_fees_usd", "USD", "Aggregated ecosystem protocol fees; not necessarily chain gas", "defillama-chain-fees-overview", "ecosystem", "ecosystem_activity"),
        ("ecosystem_daily_revenue_usd", "USD", "Aggregated ecosystem protocol revenue; not token-holder revenue", "defillama-chain-fees-overview", "ecosystem", "ecosystem_activity"),
    ]
    return MappingProxyType({
        (source, metric): MetricDefinition(metric, unit, definition, source, scope, family)
        for metric, unit, definition, source, scope, family in rows
    })


class MetricRegistry:
    """Validates both source and economic scope for a metric."""

    version = METRIC_REGISTRY_VERSION

    def __init__(self):
        self._definitions = _metric_definitions()

    @property
    def definitions(self):
        return self._definitions

    def resolve(self, source: str, metric: str, entity_scope: str) -> MetricDefinition:
        if not any(registered_metric == metric for _, registered_metric in self._definitions):
            raise ValueError(f"unknown metric: {metric}")
        definition = self.resolve_source(source, metric)
        if definition.entity_scope != entity_scope:
            raise ValueError(
                f"metric {metric} is not registered for entity scope {entity_scope}"
            )
        return definition

    def resolve_source(self, source: str, metric: str) -> MetricDefinition:
        if not any(registered_metric == metric for _, registered_metric in self._definitions):
            raise ValueError(f"unknown metric: {metric}")
        definition = self._definitions.get((source, metric))
        if definition is None:
            raise ValueError(f"metric {metric} is not registered for source {source}")
        return definition

    def resolve_metric(self, metric: str) -> MetricDefinition:
        definitions = [
            definition for (_, registered_metric), definition in self._definitions.items()
            if registered_metric == metric
        ]
        if not definitions:
            raise ValueError(f"unknown metric: {metric}")
        if len(definitions) != 1:
            raise ValueError(f"metric is ambiguous without a source: {metric}")
        return definitions[0]
