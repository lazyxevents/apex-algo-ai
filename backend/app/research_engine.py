from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx
import yfinance as yf
from sqlalchemy import func, select

from .core import SessionLocal, settings
from .models import DailyMarketPlan, ResearchKnowledge, StrategyHypothesis

IST = ZoneInfo(settings.timezone)

EDUCATION_SOURCES = [
    {"category": "TECHNICAL_ANALYSIS", "title": "Zerodha Varsity - Technical Analysis", "url": "https://zerodha.com/varsity/module/technical-analysis/", "tags": ["price-action", "candlestick", "trend"]},
    {"category": "RISK", "title": "Zerodha Varsity - Risk Management & Trading Psychology", "url": "https://zerodha.com/varsity/module/risk-management-and-trading-psychology/", "tags": ["risk", "psychology", "position-sizing"]},
    {"category": "OPTIONS", "title": "Zerodha Varsity - Option Theory", "url": "https://zerodha.com/varsity/module/option-theory/", "tags": ["options", "greeks", "volatility"]},
    {"category": "OPTIONS", "title": "Zerodha Varsity - Option Strategies", "url": "https://zerodha.com/varsity/module/option-strategies/", "tags": ["options", "strategy", "defined-risk"]},
    {"category": "MARKET_EDUCATION", "title": "NSE - Learn", "url": "https://www.nseindia.com/learn", "tags": ["market", "education", "india"]},
    {"category": "INVESTOR_EDUCATION", "title": "SEBI Investor", "url": "https://investor.sebi.gov.in/", "tags": ["risk", "investor-protection", "india"]},
]

NEWS_QUERIES = [
    "Sensex Indian stock market today",
    "India VIX market volatility today",
    "RBI liquidity interest rates markets",
    "Federal Reserve global markets India",
    "Indian stock market sector rotation",
    "oil dollar rupee India equity market",
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
    "FIN_SERVICES": "^CNXFINANCE",
}

HYPOTHESES = [
    {
        "name": "SMC_LIQUIDITY_REVERSAL_SCALP",
        "family": "SCALP",
        "description": "Trade only after a liquidity sweep is reclaimed with directional confirmation and nearby invalidation.",
        "rules": {"timeframe": "1m", "requires": ["liquiditySweep", "confirmation"], "avoid": ["flat_low_volatility"]},
    },
    {
        "name": "BOS_FVG_CONTINUATION_SCALP",
        "family": "SCALP",
        "description": "Continuation setup using BOS followed by a fair-value-gap/pullback confirmation instead of chasing the breakout candle.",
        "rules": {"timeframe": "1m-5m", "requires": ["BOS", "FVG_or_pullback"], "avoid": ["late_entry"]},
    },
    {
        "name": "HTF_TREND_PULLBACK_SWING",
        "family": "SWING",
        "description": "Intraday swing setup aligning 15m trend with 5m pullback and 1m entry confirmation.",
        "rules": {"timeframe": "15m/5m/1m", "requires": ["HTF_alignment", "pullback", "confirmation"]},
    },
    {
        "name": "PINBAR_STRUCTURE_REVERSAL",
        "family": "PRICE_ACTION",
        "description": "Pin-bar/hammer rejection becomes actionable only when it occurs at a structure/liquidity level.",
        "rules": {"requires": ["pin_bar_or_hammer", "structure_level"], "avoid": ["mid_range_signal"]},
    },
    {
        "name": "ENGULFING_STRUCTURE_CONFIRMATION",
        "family": "PRICE_ACTION",
        "description": "Engulfing candle used as confirmation after BOS/CHOCH or liquidity event, not as a standalone signal.",
        "rules": {"requires": ["engulfing", "structure_event"]},
    },
]


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


class ResearchIntelligenceEngine:
    def __init__(self) -> None:
        self.timeout = max(3.0, settings.research_source_timeout_seconds)

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
        headers = {"User-Agent": "APEX-Research/1.0 (+paper-trading research)"}
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            for src in EDUCATION_SOURCES:
                try:
                    res = client.get(src["url"])
                    res.raise_for_status()
                    text = _clean_text(res.text)
                    if not text:
                        continue
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
                except Exception as exc:
                    out.append({**src, "summary": "", "status": "error", "error": str(exc)[:140]})
        return out

    def collect_news(self) -> list[dict]:
        if not settings.research_web_enabled:
            return []
        results: list[dict] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "APEX-Research/1.0"}) as client:
            for query in NEWS_QUERIES[: settings.news_query_limit]:
                try:
                    url = (
                        "https://news.google.com/rss/search?q="
                        + quote_plus(query)
                        + "&hl=en-IN&gl=IN&ceid=IN:en"
                    )
                    res = client.get(url)
                    res.raise_for_status()
                    root = ElementTree.fromstring(res.text)
                    for item in root.findall(".//item")[: settings.news_results_per_query]:
                        title = (item.findtext("title") or "").strip()
                        link = (item.findtext("link") or "").strip()
                        pub = (item.findtext("pubDate") or "").strip()
                        description = _clean_text(item.findtext("description") or "", 700)
                        if not link or not title:
                            continue
                        row = {
                            "category": "NEWS",
                            "query": query,
                            "title": title[:300],
                            "url": link,
                            "published": pub,
                            "summary": description,
                            "tags": ["news", "macro", "india"],
                            "status": "ok",
                        }
                        self._upsert_knowledge(
                            source_type="NEWS_RSS",
                            category="NEWS",
                            title=row["title"],
                            url=link,
                            summary=description,
                            tags=["news", "macro", "india", query],
                        )
                        results.append(row)
                        if len(results) >= settings.research_max_articles_per_cycle:
                            return results
                except Exception:
                    continue
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
                pct = ((last - prev) / prev * 100) if prev else 0.0
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
                    touched += 1
                else:
                    row.description = item["description"]
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
        strong = sectors[:3]
        weak = list(reversed(sectors[-3:])) if sectors else []
        pattern_pool: list[str] = []
        for frame in (f1, f5, f15):
            pats = frame.get("patterns") or {}
            pattern_pool.extend(pats.get("bullish") or [])
            pattern_pool.extend(pats.get("bearish") or [])
            pattern_pool.extend(pats.get("neutral") or [])
        plan_date = _next_trading_day(now.date()).isoformat() if session == "POSTMARKET" else now.date().isoformat()
        bias = sensex.get("state") or "UNKNOWN"
        plan = {
            "planDate": plan_date,
            "generatedAt": now.isoformat(),
            "session": session,
            "marketBias": bias,
            "support": levels.get("support"),
            "resistance": levels.get("resistance"),
            "strongSectors": strong,
            "weakSectors": weak,
            "patternsSeen": list(dict.fromkeys(pattern_pool))[:12],
            "scalpPlaybook": {
                "timeframes": ["1m", "5m"],
                "requires": ["BOS/CHOCH or liquidity sweep", "directional candle confirmation", "defined invalidation"],
                "avoid": ["chasing breakout", "flat low-volatility regime", "post 15:15 new entry"],
            },
            "swingPlaybook": {
                "timeframes": ["15m", "5m", "1m"],
                "requires": ["15m trend alignment", "5m pullback/structure", "1m confirmation"],
                "avoid": ["counter-trend mid-range entries", "undefined stop"],
            },
            "researchStats": {
                "sourcesReviewed": len(research.get("sources") or []),
                "newsReviewed": len(research.get("news") or []),
            },
            "note": "Plan is a research hypothesis for paper testing; it is not a guaranteed forecast.",
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
            db.commit()
        return plan

    def run(self, analytics: dict, session: str) -> dict:
        education = self.collect_education()
        news = self.collect_news()
        sectors = self.sector_snapshot()
        hypotheses = self.seed_hypotheses()
        good_education = [x for x in education if x.get("status") == "ok"]
        bundle = {
            "session": session,
            "sources": good_education,
            "news": news,
            "sectors": sectors,
            "hypothesesSeeded": hypotheses,
        }
        bundle["plan"] = self.build_plan(analytics, sectors, bundle, session)
        return bundle

    def snapshot(self) -> dict:
        with SessionLocal() as db:
            total_sources = db.scalar(select(func.count()).select_from(ResearchKnowledge)) or 0
            source_rows = list(db.execute(
                select(ResearchKnowledge).order_by(ResearchKnowledge.last_checked_at.desc()).limit(16)
            ).scalars().all())
            hypothesis_rows = list(db.execute(
                select(StrategyHypothesis).order_by(StrategyHypothesis.updated_at.desc()).limit(12)
            ).scalars().all())
            plan_row = db.scalar(select(DailyMarketPlan).order_by(DailyMarketPlan.id.desc()).limit(1))
        def parse(raw: str, fallback):
            try:
                return json.loads(raw or "")
            except Exception:
                return fallback
        return {
            "knowledgeCount": int(total_sources),
            "sources": [{
                "category": r.category,
                "type": r.source_type,
                "title": r.title,
                "url": r.url,
                "summary": r.summary[:320],
                "tags": parse(r.tags_json, []),
                "status": r.status,
                "lastCheckedAt": r.last_checked_at.isoformat() if r.last_checked_at else None,
            } for r in source_rows],
            "hypotheses": [{
                "name": r.name,
                "family": r.family,
                "description": r.description,
                "status": r.status,
                "score": r.score,
                "rules": parse(r.rules_json, {}),
            } for r in hypothesis_rows],
            "latestPlan": parse(plan_row.plan_json, {}) if plan_row else {},
            "latestSectors": parse(plan_row.sectors_json, []) if plan_row else [],
        }


research_engine = ResearchIntelligenceEngine()
