from __future__ import annotations

import html
import json
import re
from math import isfinite
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx
import yfinance as yf
from sqlalchemy import func, select

from .core import SessionLocal, settings
from .models import DailyMarketPlan, ResearchActivity, ResearchKnowledge, StrategyHypothesis

IST = ZoneInfo(settings.timezone)

# Bounded, public educational material only. APEX does not copy or ingest paid/copyrighted books.
EDUCATION_SOURCES = [
    {"category": "TECHNICAL_ANALYSIS", "title": "Zerodha Varsity - Technical Analysis", "url": "https://zerodha.com/varsity/module/technical-analysis/", "tags": ["price-action", "candlestick", "trend"]},
    {"category": "RISK", "title": "Zerodha Varsity - Risk Management & Trading Psychology", "url": "https://zerodha.com/varsity/module/risk-management-and-trading-psychology/", "tags": ["risk", "psychology", "position-sizing"]},
    {"category": "OPTIONS", "title": "Zerodha Varsity - Option Theory", "url": "https://zerodha.com/varsity/module/option-theory/", "tags": ["options", "greeks", "volatility"]},
    {"category": "OPTIONS", "title": "Zerodha Varsity - Option Strategies", "url": "https://zerodha.com/varsity/module/option-strategies/", "tags": ["options", "strategy", "defined-risk"]},
    {"category": "FUTURES", "title": "Zerodha Varsity - Futures Trading", "url": "https://zerodha.com/varsity/module/futures-trading/", "tags": ["futures", "market-structure"]},
    {"category": "MARKETS", "title": "Zerodha Varsity - Markets and Taxation", "url": "https://zerodha.com/varsity/module/markets-and-taxation/", "tags": ["market-mechanics", "india"]},
    {"category": "MARKET_EDUCATION", "title": "NSE - Learn", "url": "https://www.nseindia.com/learn", "tags": ["market", "education", "india"]},
    {"category": "INVESTOR_EDUCATION", "title": "SEBI Investor", "url": "https://investor.sebi.gov.in/", "tags": ["risk", "investor-protection", "india"]},
]

NEWS_QUERIES = [
    "Sensex Indian stock market today",
    "India VIX volatility stock market today",
    "RBI liquidity interest rates rupee markets",
    "Federal Reserve global markets India stocks",
    "crude oil dollar rupee India equity market",
    "Indian stock market sector rotation today",
    "FII DII flows Indian stock market today",
    "Sensex gap up gap down global cues",
]

SECTOR_SYMBOLS = {
    "BANK": "^NSEBANK",
    "IT": "^CNXIT",
    "AUTO": "^CNXAUTO",
    "PHARMA": "^CNXPHARMA",
    "FMCG": "^CNXFMCG",
    "METAL": "^CNXMETAL",
    "REALTY": "^CNXREALTY",
    "ENERGY": "^CNXENERGY",
    "PSU_BANK": "^CNXPSUBANK",
}

HYPOTHESES = [
    {
        "name": "SMC_LIQUIDITY_REVERSAL_SCALP",
        "family": "SMC_REVERSAL",
        "description": "Liquidity sweep reclaim with directional candle/structure confirmation.",
        "rules": {"timeframe": "1m-5m", "requires": ["liquiditySweep", "reclaim", "confirmation"]},
    },
    {
        "name": "BOS_FVG_CONTINUATION_SCALP",
        "family": "SMC_CONTINUATION",
        "description": "BOS plus FVG/momentum continuation, avoiding late chase entries.",
        "rules": {"timeframe": "1m-5m", "requires": ["BOS", "FVG_or_momentum"], "avoid": ["late_entry"]},
    },
    {
        "name": "EMA_PULLBACK_MOMENTUM",
        "family": "MOMENTUM",
        "description": "EMA-aligned pullback with RSI and structure confirmation.",
        "rules": {"timeframe": "1m-5m", "requires": ["EMA_alignment", "pullback", "RSI"]},
    },
    {
        "name": "FAKE_BREAKOUT_REVERSAL",
        "family": "PRICE_ACTION",
        "description": "Failed break of support/resistance followed by reclaim.",
        "rules": {"requires": ["fakeBreakout", "reclaim"], "avoid": ["chase"]},
    },
    {
        "name": "BREAKOUT_RETEST_MOMENTUM",
        "family": "BREAKOUT",
        "description": "Directional structure break with momentum and retest context.",
        "rules": {"requires": ["BOS_or_breakout", "EMA", "RSI"]},
    },
    {
        "name": "FIB_STRUCTURE_RETRACE",
        "family": "FIBONACCI",
        "description": "38.2%-61.8% retracement aligned with directional market structure.",
        "rules": {"requires": ["fib_zone", "trend", "structure"]},
    },
]


def _json_safe(value):
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _clean_text(raw: str, limit: int = 1600) -> str:
    raw = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style.*?>.*?</style>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:limit]


def _next_trading_day(day: date) -> date:
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _parse_json(raw: str | None, fallback):
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


class ResearchIntelligenceEngine:
    def __init__(self) -> None:
        self.timeout = max(3.0, settings.research_source_timeout_seconds)
        self._last_news_health: dict = {
            "status": "NOT_RUN",
            "lastFetchAt": None,
            "fetchedCount": 0,
            "successfulQueries": 0,
            "queryCount": 0,
            "errors": [],
        }

    def _activity(self, kind: str, stage: str, title: str, detail: dict | None = None) -> None:
        try:
            with SessionLocal() as db:
                db.add(ResearchActivity(
                    kind=kind[:48],
                    stage=stage[:48],
                    title=title[:300],
                    detail_json=json.dumps(detail or {}, default=str)[:20000],
                ))
                db.commit()
        except Exception:
            pass

    def _upsert_knowledge(self, *, source_type: str, category: str, title: str, url: str, summary: str, tags: list[str]) -> None:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            row = db.scalar(select(ResearchKnowledge).where(ResearchKnowledge.url == url))
            if row is None:
                row = ResearchKnowledge(
                    source_type=source_type,
                    category=category,
                    title=title[:300],
                    url=url[:900],
                    summary=summary[:5000],
                    tags_json=json.dumps(tags),
                    status="RAW_RESEARCH",
                    discovered_at=now,
                    last_checked_at=now,
                )
                db.add(row)
            else:
                row.title = title[:300] or row.title
                row.category = category
                row.source_type = source_type
                row.summary = summary[:5000] or row.summary
                row.tags_json = json.dumps(tags)
                row.last_checked_at = now
            db.commit()

    def collect_education(self) -> list[dict]:
        if not settings.research_web_enabled:
            return []
        out: list[dict] = []
        now = datetime.now(timezone.utc)
        headers = {"User-Agent": "APEX-Research/2.0 (+paper-trading research)"}
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            for src in EDUCATION_SOURCES:
                cached = None
                with SessionLocal() as db:
                    cached = db.scalar(select(ResearchKnowledge).where(ResearchKnowledge.url == src["url"]))
                if cached and cached.last_checked_at and (now - cached.last_checked_at).total_seconds() < 20 * 3600:
                    out.append({**src, "summary": cached.summary[:900], "status": "cached", "lastCheckedAt": cached.last_checked_at.isoformat()})
                    continue
                try:
                    res = client.get(src["url"])
                    res.raise_for_status()
                    text = _clean_text(res.text)
                    if not text:
                        raise RuntimeError("empty response")
                    item = {**src, "summary": text[:900], "status": "ok"}
                    self._upsert_knowledge(
                        source_type="EDUCATION",
                        category=src["category"],
                        title=src["title"],
                        url=src["url"],
                        summary=text,
                        tags=src["tags"],
                    )
                    out.append(item)
                    self._activity("EDUCATION_READ", "SOURCE_RESEARCH", f"Reviewed {src['title']}", {"url": src["url"], "category": src["category"]})
                except Exception as exc:
                    if cached:
                        out.append({**src, "summary": cached.summary[:900], "status": "cached_after_error", "error": str(exc)[:140]})
                    else:
                        out.append({**src, "summary": "", "status": "error", "error": str(exc)[:140]})
        return out

    @staticmethod
    def _rss_rows(xml_text: str, query: str, limit: int) -> list[dict]:
        root = ElementTree.fromstring(xml_text)
        rows: list[dict] = []
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            description = _clean_text(item.findtext("description") or "", 700)
            source_node = item.find("source")
            source = (source_node.text or "").strip() if source_node is not None and source_node.text else ""
            if not link or not title:
                continue
            published_iso = pub
            if pub:
                try:
                    published_iso = parsedate_to_datetime(pub).astimezone(IST).isoformat()
                except Exception:
                    pass
            rows.append({
                "category": "NEWS",
                "query": query,
                "title": title[:300],
                "url": link,
                "published": published_iso,
                "summary": description,
                "source": source,
                "tags": ["news", "macro", "india"],
                "status": "ok",
            })
        return rows

    def collect_news(self) -> list[dict]:
        now = datetime.now(IST)
        if not settings.research_web_enabled:
            self._last_news_health = {
                "status": "DISABLED",
                "lastFetchAt": now.isoformat(),
                "fetchedCount": 0,
                "successfulQueries": 0,
                "queryCount": 0,
                "errors": [],
            }
            return []

        results: list[dict] = []
        seen: set[str] = set()
        errors: list[dict] = []
        successful = 0
        query_count = min(len(NEWS_QUERIES), max(1, settings.news_query_limit))
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; APEX-Research/2.0; +paper-trading research)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            for query in NEWS_QUERIES[:query_count]:
                try:
                    url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-IN&gl=IN&ceid=IN:en"
                    res = client.get(url)
                    res.raise_for_status()
                    rows = self._rss_rows(res.text, query, settings.news_results_per_query)
                    if rows:
                        successful += 1
                    for row in rows:
                        dedupe = row["title"].lower().strip()
                        if dedupe in seen:
                            continue
                        seen.add(dedupe)
                        self._upsert_knowledge(
                            source_type="NEWS_RSS",
                            category="NEWS",
                            title=row["title"],
                            url=row["url"],
                            summary=row["summary"],
                            tags=["news", "macro", "india", query],
                        )
                        results.append(row)
                        if len(results) >= settings.research_max_articles_per_cycle:
                            break
                    if len(results) >= settings.research_max_articles_per_cycle:
                        break
                except Exception as exc:
                    errors.append({"query": query, "error": str(exc)[:160]})

            # Fallback: Google News India top stories. This keeps the news panel alive even if search feeds fail.
            if not results:
                try:
                    fallback_url = "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
                    res = client.get(fallback_url)
                    res.raise_for_status()
                    rows = self._rss_rows(res.text, "India top stories fallback", settings.research_max_articles_per_cycle)
                    for row in rows:
                        dedupe = row["title"].lower().strip()
                        if dedupe in seen:
                            continue
                        seen.add(dedupe)
                        self._upsert_knowledge(
                            source_type="NEWS_RSS_FALLBACK",
                            category="NEWS",
                            title=row["title"],
                            url=row["url"],
                            summary=row["summary"],
                            tags=["news", "india", "fallback"],
                        )
                        results.append(row)
                    if rows:
                        successful += 1
                except Exception as exc:
                    errors.append({"query": "India top stories fallback", "error": str(exc)[:160]})

        status = "OK" if results else "ERROR" if errors else "EMPTY"
        self._last_news_health = {
            "status": status,
            "lastFetchAt": now.isoformat(),
            "fetchedCount": len(results),
            "successfulQueries": successful,
            "queryCount": query_count,
            "errors": errors[-6:],
            "source": "Google News RSS + cached DB fallback",
        }
        self._activity(
            "NEWS_FETCH",
            "SOURCE_RESEARCH",
            f"News fetch {status}: {len(results)} fresh headlines",
            self._last_news_health,
        )
        return results

    def sector_snapshot(self) -> list[dict]:
        rows: list[dict] = []
        for name, symbol in SECTOR_SYMBOLS.items():
            try:
                frame = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False, actions=False)
                if frame is None or frame.empty or len(frame) < 2:
                    continue
                prev = float(frame["Close"].iloc[-2])
                last = float(frame["Close"].iloc[-1])
                if not (isfinite(prev) and isfinite(last)) or prev <= 0:
                    continue
                pct = (last - prev) / prev * 100
                if not isfinite(pct):
                    continue
                rows.append({"sector": name, "symbol": symbol, "last": round(last, 2), "changePct": round(pct, 3)})
            except Exception:
                continue
        return sorted(rows, key=lambda x: x["changePct"], reverse=True)

    def seed_hypotheses(self) -> int:
        touched = 0
        with SessionLocal() as db:
            for item in HYPOTHESES:
                row = db.scalar(select(StrategyHypothesis).where(StrategyHypothesis.name == item["name"]))
                if row is None:
                    row = StrategyHypothesis(
                        name=item["name"],
                        family=item["family"],
                        description=item["description"],
                        evidence_json="[]",
                        rules_json=json.dumps(item["rules"]),
                        status="UNVERIFIED",
                        score=0.0,
                    )
                    db.add(row)
                else:
                    row.description = item["description"]
                    row.family = item["family"]
                    row.rules_json = json.dumps(item["rules"])
                touched += 1
            db.commit()
        return touched

    def build_plan(self, analytics: dict, sectors: list[dict], research: dict, session: str) -> dict:
        now = datetime.now(IST)
        sensex = ((analytics.get("markets") or {}).get("SENSEX") or {})
        frames = sensex.get("frames") or {}
        f1, f5, f15 = frames.get("1m") or {}, frames.get("5m") or {}, frames.get("15m") or {}
        levels = (f5.get("structure") or {}).get("levels") or {}
        profile = sensex.get("sessionProfile") or {}
        strong = sectors[:3]
        weak = list(reversed(sectors[-3:])) if sectors else []
        pattern_pool: list[str] = []
        structure_events: list[str] = []
        for tf, frame in (("1m", f1), ("5m", f5), ("15m", f15)):
            pats = frame.get("patterns") or {}
            pattern_pool.extend(pats.get("bullish") or [])
            pattern_pool.extend(pats.get("bearish") or [])
            pattern_pool.extend(pats.get("neutral") or [])
            structure = frame.get("structure") or {}
            for key in ("bos", "choch", "liquiditySweep", "fairValueGap", "fakeBreakout"):
                value = structure.get(key)
                if value and value != "NONE":
                    structure_events.append(f"{tf}:{key}={value}")

        plan_date = _next_trading_day(now.date()).isoformat() if session in {"POSTMARKET", "NIGHT_RESEARCH"} else now.date().isoformat()
        bias = sensex.get("state") or "UNKNOWN"
        prev_high = profile.get("previousDayHigh")
        prev_low = profile.get("previousDayLow")
        prev_close = profile.get("previousDayClose")
        day_high = profile.get("dayHigh")
        day_low = profile.get("dayLow")
        gap_pct = profile.get("gapPct")

        plan = {
            "planDate": plan_date,
            "generatedAt": now.isoformat(),
            "session": session,
            "marketBias": bias,
            "timeframeBias": {
                "1m": f1.get("trend"),
                "5m": f5.get("trend"),
                "15m": f15.get("trend"),
            },
            "support": levels.get("support"),
            "resistance": levels.get("resistance"),
            "dayHigh": day_high,
            "dayLow": day_low,
            "previousDayHigh": prev_high,
            "previousDayLow": prev_low,
            "previousDayClose": prev_close,
            "gapPct": gap_pct,
            "strongSectors": strong,
            "weakSectors": weak,
            "patternsSeen": list(dict.fromkeys(pattern_pool))[:16],
            "structureSeen": list(dict.fromkeys(structure_events))[:16],
            "openingScenarios": [
                {
                    "name": "GAP_UP_BULLISH",
                    "trigger": "Open above previous close/high; wait for 5m hold or sweep-reclaim before CE scalp.",
                    "invalidation": "Opening range loses reclaimed level or 5m structure turns down.",
                },
                {
                    "name": "GAP_DOWN_BEARISH",
                    "trigger": "Open below previous close/low; wait for failed reclaim or bearish BOS before PE scalp.",
                    "invalidation": "Price reclaims previous-day low/close and holds.",
                },
                {
                    "name": "INSIDE_RANGE",
                    "trigger": "Trade only confirmed liquidity sweep/reclaim or BOS + retest near marked levels.",
                    "invalidation": "Mid-range/no structure; stay NO_TRADE.",
                },
            ],
            "scalpPlaybook": {
                "timeframes": ["1m", "5m", "15m"],
                "requires": [
                    "fresh 1m data",
                    "5m/15m directional context or strong structure transition",
                    "BOS/CHOCH/liquidity sweep/FVG/fake-breakout evidence",
                    "EMA/RSI/ATR context",
                    "defined invalidation before entry",
                ],
                "avoid": ["stale feed", "late chase", "mid-range noise", "post cutoff new entry"],
            },
            "researchStats": {
                "sourcesReviewed": len(research.get("sources") or []),
                "newsReviewed": len(research.get("news") or []),
                "newsHealth": research.get("newsHealth") or {},
            },
            "note": "Research plan for paper testing; levels and scenarios are hypotheses, not guaranteed forecasts.",
        }

        with SessionLocal() as db:
            row = DailyMarketPlan(
                plan_date=plan_date,
                session=session,
                market_json=json.dumps(analytics, default=str),
                sectors_json=json.dumps(sectors, default=str),
                research_json=json.dumps(research, default=str),
                plan_json=json.dumps(plan, default=str),
            )
            db.add(row)
            db.add(ResearchActivity(
                kind="TRADE_PLAN",
                stage="PREMARKET" if session == "PREMARKET" else "RESEARCH",
                title=f"Generated market plan for {plan_date}",
                detail_json=json.dumps(plan, default=str)[:20000],
            ))
            db.commit()
        return plan

    def run(self, analytics: dict, session: str) -> dict:
        education = self.collect_education()
        news = self.collect_news()
        sectors = self.sector_snapshot()
        hypotheses = self.seed_hypotheses()
        good_education = [x for x in education if x.get("status") in {"ok", "cached", "cached_after_error"}]
        bundle = {
            "session": session,
            "sources": good_education,
            "news": news,
            "newsHealth": self._last_news_health,
            "sectors": sectors,
            "hypothesesSeeded": hypotheses,
            "readingQueue": [{"title": x["title"], "category": x["category"], "url": x["url"]} for x in EDUCATION_SOURCES],
        }
        bundle["plan"] = self.build_plan(analytics, sectors, bundle, session)
        self._activity(
            "RESEARCH_CYCLE",
            session,
            f"{session} research: {len(good_education)} education sources, {len(news)} headlines, {len(sectors)} sectors",
            {
                "newsHealth": self._last_news_health,
                "planDate": bundle["plan"].get("planDate"),
                "sources": len(good_education),
                "news": len(news),
                "sectors": len(sectors),
            },
        )
        return bundle

    def snapshot(self) -> dict:
        now_utc = datetime.now(timezone.utc)
        day_start_ist = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = day_start_ist.astimezone(timezone.utc)
        with SessionLocal() as db:
            total_sources = db.scalar(select(func.count()).select_from(ResearchKnowledge)) or 0
            source_rows = list(db.execute(
                select(ResearchKnowledge).order_by(ResearchKnowledge.last_checked_at.desc()).limit(18)
            ).scalars().all())
            news_rows = list(db.execute(
                select(ResearchKnowledge).where(ResearchKnowledge.category == "NEWS")
                .order_by(ResearchKnowledge.last_checked_at.desc()).limit(12)
            ).scalars().all())
            hypothesis_rows = list(db.execute(
                select(StrategyHypothesis).order_by(StrategyHypothesis.updated_at.desc()).limit(16)
            ).scalars().all())
            plan_row = db.scalar(select(DailyMarketPlan).order_by(DailyMarketPlan.id.desc()).limit(1))
            activity_rows = list(db.execute(
                select(ResearchActivity).order_by(ResearchActivity.id.desc()).limit(30)
            ).scalars().all())
            today_activity_count = int(db.scalar(
                select(func.count()).select_from(ResearchActivity).where(ResearchActivity.created_at >= day_start_utc)
            ) or 0)
            today_news_count = int(db.scalar(
                select(func.count()).select_from(ResearchKnowledge).where(
                    ResearchKnowledge.category == "NEWS",
                    ResearchKnowledge.last_checked_at >= day_start_utc,
                )
            ) or 0)

        latest_news_health = self._last_news_health
        if latest_news_health.get("status") == "NOT_RUN":
            for row in activity_rows:
                if row.kind == "NEWS_FETCH":
                    latest_news_health = _parse_json(row.detail_json, latest_news_health)
                    break

        return _json_safe({
            "knowledgeCount": int(total_sources),
            "todayActivityCount": today_activity_count,
            "todayNewsCount": today_news_count,
            "newsHealth": latest_news_health,
            "readingQueue": [{"title": x["title"], "category": x["category"], "url": x["url"]} for x in EDUCATION_SOURCES],
            "latestNews": [{
                "title": r.title,
                "url": r.url,
                "summary": r.summary[:360],
                "type": r.source_type,
                "lastCheckedAt": r.last_checked_at.isoformat() if r.last_checked_at else None,
            } for r in news_rows],
            "sources": [{
                "category": r.category,
                "type": r.source_type,
                "title": r.title,
                "url": r.url,
                "summary": r.summary[:360],
                "tags": _parse_json(r.tags_json, []),
                "status": r.status,
                "lastCheckedAt": r.last_checked_at.isoformat() if r.last_checked_at else None,
            } for r in source_rows],
            "hypotheses": [{
                "name": r.name,
                "family": r.family,
                "description": r.description,
                "status": r.status,
                "score": r.score,
                "rules": _parse_json(r.rules_json, {}),
                "evidence": _parse_json(r.evidence_json, {}),
                "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
            } for r in hypothesis_rows],
            "activities": [{
                "kind": r.kind,
                "stage": r.stage,
                "title": r.title,
                "detail": _parse_json(r.detail_json, {}),
                "createdAt": r.created_at.isoformat() if r.created_at else None,
            } for r in activity_rows],
            "latestPlan": _parse_json(plan_row.plan_json, {}) if plan_row else {},
            "latestSectors": _parse_json(plan_row.sectors_json, []) if plan_row else [],
            "snapshotAt": now_utc.isoformat(),
        })


research_engine = ResearchIntelligenceEngine()
