"""Forex-only provider capability registry; unknown capabilities fail closed."""
from dataclasses import dataclass, field
from enum import StrEnum


class ProviderEnvironment(StrEnum):
    RESEARCH = "RESEARCH"
    DEMO = "DEMO"
    PAPER = "PAPER"
    LIVE = "LIVE"


@dataclass(frozen=True)
class ProviderCapabilities:
    name: str
    asset_classes: frozenset[str]
    environments: frozenset[ProviderEnvironment]
    streaming_quotes: bool = False
    historical_candles: bool = False
    historical_ticks: bool = False
    account_api: bool = False
    live_execution_approved: bool = False
    notes: str = ""


@dataclass
class ProviderRegistry:
    _providers: dict[str, ProviderCapabilities] = field(default_factory=dict)

    def register(self, provider: ProviderCapabilities) -> None:
        key = provider.name.strip().lower()
        if not key:
            raise ValueError("Provider name is required")
        if provider.live_execution_approved:
            raise ValueError("Live execution approval requires a separate compliance sign-off")
        if provider.asset_classes != frozenset({"FOREX"}):
            raise ValueError("APEX Forex registry only accepts Forex providers")
        self._providers[key] = provider

    def get(self, name: str) -> ProviderCapabilities:
        try:
            return self._providers[name.strip().lower()]
        except KeyError as exc:
            raise KeyError(f"Unknown provider: {name}") from exc

    def list_all(self) -> list[ProviderCapabilities]:
        return sorted(self._providers.values(), key=lambda item: item.name.lower())


def default_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(ProviderCapabilities(
        name="OANDA v20", asset_classes=frozenset({"FOREX"}),
        environments=frozenset({ProviderEnvironment.RESEARCH, ProviderEnvironment.DEMO}),
        streaming_quotes=True, historical_candles=True, account_api=True,
        notes="Implemented practice-only adapter; regional account availability must be verified.",
    ))
    registry.register(ProviderCapabilities(
        name="Dukascopy JForex/FIX", asset_classes=frozenset({"FOREX"}),
        environments=frozenset({ProviderEnvironment.RESEARCH, ProviderEnvironment.DEMO}),
        streaming_quotes=True, historical_candles=True, historical_ticks=True, account_api=True,
        notes="Research candidate; JForex/FIX runtime adapter not yet implemented.",
    ))
    registry.register(ProviderCapabilities(
        name="Interactive Brokers TWS", asset_classes=frozenset({"FOREX"}),
        environments=frozenset({ProviderEnvironment.RESEARCH, ProviderEnvironment.DEMO}),
        streaming_quotes=True, historical_candles=True, historical_ticks=True, account_api=True,
        notes="Research candidate; TWS Gateway adapter not yet implemented.",
    ))
    return registry
