# APEX Forex Provider Research

Status snapshot: 2026-11-26. Engineering integration status only; not legal or account approval.

## Provider matrix

| Provider | API | Forex capabilities | Demo/paper | APEX status |
|---|---|---|---|---|
| OANDA v20 | REST + HTTP pricing stream | Currency pairs, bid/ask, candles, account endpoints | fxTrade Practice | Integrated, practice-only |
| Dukascopy | JForex Java SDK + FIX | Forex stream, historical ticks/bars, strategy backtest | JForex demo; FIX demo access by request | Research candidate |
| Interactive Brokers | TWS API / Web API | CASH FX pairs, market data, historical bars | Paper accounts supported | Research candidate |
| FXCM | FCLite REST/API | Forex API, API-key/secret login | Demo account path documented | Research candidate |

## OANDA — first integrated provider

Official documentation:
- [v20 introduction and account setup](https://developer.oanda.com/rest-live-v20/introduction/)
- [Development guide](https://developer.oanda.com/rest-live-v20/development-guide/)
- [Authentication](https://developer.oanda.com/rest-live-v20/authentication/)
- [Pricing endpoint/stream](https://developer.oanda.com/rest-live-v20/pricing-ep/)
- [Instrument candles](https://developer.oanda.com/rest-live-v20/instrument-ep/)

The official development guide specifies distinct practice hosts:
- REST: `https://api-fxpractice.oanda.com`
- Streaming: `https://stream-fxpractice.oanda.com`

APEX implements practice-only token auth, account discovery/summary, dynamic account instrument discovery, multi-pair pricing snapshots, bid/ask stream, historical candles and normalized quote events. It deliberately has no OANDA order submission method.

OANDA v20 account availability varies by division; its docs exclude OANDA Global Markets and OANDA TMS Brokers S.A. from v20 accounts. India onboarding is not confirmed by this technical documentation.

## Dukascopy — next candidate

Official [JForex API](https://www.dukascopy.com/swiss/english/forex/api/jforex-api/) supports external Java applications, live strategy data and historical backtesting. Its [FAQ](https://www.dukascopy.com/swiss/english/about/faq/?toEng=1) says FIX API demo access can be requested after opening a JForex demo account and emailing the account login. APEX does not yet contain a JForex Java bridge or FIX adapter.

## Interactive Brokers — next candidate

Official [TWS API](https://www.interactivebrokers.com/docs/tws-api/doc/introduction) supports streaming and historical market data through TWS or IB Gateway. Its [FX contract documentation](https://www.interactivebrokers.com/docs/tws-api/contracts/fx-pairs) specifies FX pairs as CASH contracts. IBKR confirms TWS/Web API paper account use, subject to account setup and market-data permissions. APEX does not yet contain a TWS Gateway adapter.

## FXCM — research only

Official [FCLite authentication](https://www.fxcm.com/public-docs/fclite/gettingstarted/authentication/) describes API key/secret login and a demo account path. FXCM warns demo execution and quotes may differ from live, including throttled prices. Country-specific onboarding and eligible product have not been verified.

## Forex data quality requirements

- OTC Forex is fragmented. A broker's bid/ask stream is venue-specific, not a consolidated global tape.
- Do not merge provider prices into one synthetic executable book without source and timestamp provenance.
- Use bid to close longs and ask to close shorts.
- Preserve event time and receive time; reject stale, crossed and outlier quotes.
- Keep each provider's spread and liquidity fields separate.
- Never silently splice historical candles from one provider with live quotes from another.

## India regulatory boundary

RBI's [Forex Transactions FAQ](https://rbi.org.in/Scripts/FAQView.aspx?Id=146) says residents may transact in Forex only with authorised persons and for permitted purposes; electronic transactions must be on RBI-authorised ETPs or recognised exchanges as applicable. Not appearing on the RBI Alert List does not mean a provider is authorised.

Technical demo access is not live trading approval. Offshore leveraged OTC Forex/CFD execution remains disabled pending jurisdiction-specific legal and account eligibility review.
