from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Settings(BaseSettings):
    app_name: str = "APEX Algo AI"
    app_env: str = "development"
    database_url: str = "sqlite:///./apex.db"
    cors_origins_raw: str = "http://localhost:5173"

    trading_mode: str = "OFF"
    auto_trading_enabled: bool = True
    auto_scan_interval_seconds: int = 30
    position_monitor_interval_seconds: float = 2.0
    timezone: str = "Asia/Kolkata"
    market_open_time: str = "09:15"
    market_close_time: str = "15:30"
    trade_start_time: str = "09:20"
    stop_new_trade_time: str = "15:15"
    force_exit_time: str = "15:20"

    capital: float = 40000
    min_trading_capital: float = 5000
    capital_usage_pct: float = 90.0
    auto_compound_profits: bool = True
    max_deployable_capital: float = 0.0
    dynamic_risk_limits: bool = True
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 2.5
    max_weekly_loss_pct: float = 4.0
    max_monthly_drawdown_pct: float = 10.0
    monthly_profit_target_pct: float = 15.0
    monthly_target_lock: bool = True
    max_risk_per_trade: float = 400
    max_daily_loss: float = 1000
    max_weekly_loss: float = 800
    max_monthly_drawdown: float = 2000
    max_trades_per_day: int = 5
    max_concurrent_positions: int = 1
    allow_live_orders: bool = False

    candle_interval_minutes: int = 1
    signal_min_score: float = 0.65
    smc_override_min_score: float = 0.55
    reward_risk_ratio: float = 1.8
    active_indices_raw: str = "SENSEX"
    sensex_lot_size: int = 20
    scalp_target_points: float = 12.0
    scalp_entry_pullback_points: float = 10.0
    scalp_max_stop_points: float = 15.0
    scalp_min_target_points: float = 10.0
    scalp_max_target_points: float = 15.0

    # Explicit dashboard-triggered aggressive PAPER trade mode.
    manual_do_trade_enabled: bool = True
    manual_do_trade_cutoff_time: str = "15:20"
    manual_do_trade_target_points: float = 30.0
    manual_do_trade_stop_points: float = 15.0
    manual_do_trade_capital_usage_pct: float = 70.0
    manual_do_trade_min_mtf_score: float = 0.50
    manual_do_trade_carry_forward: bool = False
    swing_first_target_points: float = 55.0
    swing_runner_target_points: float = 150.0
    swing_partial_pct: float = 50.0
    option_stop_pct: float = 18.0
    max_option_spread_pct: float = 2.5
    min_option_volume: int = 1000
    paper_fallback_max_spread_pct: float = 5.0
    paper_fallback_min_volume: int = 100
    paper_trade_risk_pct_of_deployed: float = 15.0
    paper_stop_buffer_points: float = 2.0
    target_delta: float = 0.42
    max_otm_steps: int = 3
    candle_confirmation_required: bool = True

    adaptive_learning_enabled: bool = True
    exploration_rate: float = 0.10
    learning_rate: float = 0.08
    minimum_learning_trades: int = 20
    research_prior_weight: float = 0.20
    allow_scalp_strategy: bool = True
    min_capital_for_scalp: float = 30000

    daily_research_enabled: bool = True
    daily_research_time: str = "15:45"
    premarket_research_enabled: bool = True
    premarket_research_time: str = "08:00"
    backtest_lookback_days: int = 30
    backtest_max_hold_bars: int = 6
    backtest_atr_stop_mult: float = 1.0

    force_exit_retry_count: int = 3
    force_exit_retry_delay_seconds: float = 1.0

    market_data_provider: str = "upstox"
    paper_broker: str = "internal"
    execution_broker: str = "dhan"

    live_trade_candle_max_age_seconds: int = 180

    synthetic_roundtrip_cost_pct: float = 0.20
    option_brokerage_per_order: float = 20.0
    option_stt_sell_pct: float = 0.15
    nse_option_transaction_pct: float = 0.03553
    bse_option_transaction_pct: float = 0.0325
    sebi_turnover_pct: float = 0.0001
    option_stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0

    ollama_enabled: bool = False
    ollama_base_url: str = ""
    ollama_model: str = "llama3.1:8b"
    ollama_api_key: str = ""
    ollama_timeout_seconds: float = 12.0
    ollama_web_search_enabled: bool = False
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_timeout_seconds: float = 15.0
    news_query_limit: int = 6
    news_results_per_query: int = 5

    learning_worker_enabled: bool = True
    learning_worker_interval_minutes: int = 60
    learning_worker_daily_hours: int = 15
    learning_worker_window_start_time: str = "17:00"
    learning_worker_window_end_time: str = "08:00"
    premarket_plan_time: str = "08:00"
    learning_worker_max_sources: int = 120
    research_web_enabled: bool = True
    research_max_articles_per_cycle: int = 16
    research_source_timeout_seconds: float = 8.0

    neural_training_enabled: bool = True
    neural_min_labeled_samples: int = 500
    neural_hidden_units: int = 24
    neural_epochs: int = 45
    neural_batch_size: int = 256
    neural_learning_rate: float = 0.015
    neural_l2: float = 0.0005
    neural_promotion_min_auc: float = 0.54
    neural_promotion_max_brier: float = 0.25
    neural_inference_weight: float = 0.15

    push_notifications_enabled: bool = True
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:officialtarun070@gmail.com"

    upstox_analytics_token: str = ""
    upstox_access_token: str = ""
    upstox_sandbox_token: str = ""
    upstox_sandbox_product: str = "I"
    upstox_nifty_key: str = "NSE_INDEX|Nifty 50"
    upstox_banknifty_key: str = "NSE_INDEX|Nifty Bank"
    upstox_sensex_key: str = "BSE_INDEX|SENSEX"

    # DhanHQ market-data provider. Keys are exchangeSegment|securityId|instrument.
    dhan_client_id: str = ""
    dhan_access_token: str = ""
    dhan_access_token_file: str = ""
    dhan_auto_renew_enabled: bool = False
    dhan_nifty_key: str = "IDX_I|13|INDEX"
    dhan_banknifty_key: str = "IDX_I|25|INDEX"
    dhan_sensex_key: str = "IDX_I|51|INDEX"
    dhan_intraday_lookback_days: int = 5
    dhan_product_type: str = "INTRADAY"

    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.cors_origins_raw.split(",") if x.strip()]

    @property
    def active_indices(self) -> set[str]:
        return {x.strip().upper() for x in self.active_indices_raw.split(",") if x.strip()}

    @property
    def underlying_keys(self) -> dict[str, str]:
        provider = self.market_data_provider.strip().lower()
        if provider == "dhan":
            keys = {
                "NIFTY": self.dhan_nifty_key,
                "BANKNIFTY": self.dhan_banknifty_key,
                "SENSEX": self.dhan_sensex_key,
            }
        elif provider == "upstox":
            keys = {
                "NIFTY": self.upstox_nifty_key,
                "BANKNIFTY": self.upstox_banknifty_key,
                "SENSEX": self.upstox_sensex_key,
            }
        else:
            raise ValueError(f"Unsupported market data provider: {self.market_data_provider}")
        active = self.active_indices
        return {name: key for name, key in keys.items() if name in active}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# MongoDB persistence adapter. The trading/research services keep their existing
# SQLAlchemy model and query expressions, while this adapter stores documents in Atlas.
# No PostgreSQL/Neon connection is created by this application.
import os
import operator
from datetime import datetime, timezone
from pymongo import MongoClient, ReturnDocument
from sqlalchemy import func, update
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, BindParameter, UnaryExpression
from sqlalchemy.sql.selectable import Select

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "apgoai").strip() or "apgoai"

_client = None
_mongo_db = None


def _mongo():
    global _client, _mongo_db
    if _mongo_db is None:
        if not MONGODB_URI:
            raise RuntimeError("MONGODB_URI is not configured; set the MongoDB Atlas connection string.")
        _client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=20000,
            retryWrites=True,
            appname="APEX-Algo-AI",
        )
        _client.admin.command("ping")
        _mongo_db = _client[MONGODB_DATABASE]
    return _mongo_db


def _model_for_table(table):
    name = getattr(table, "name", None)
    if not name:
        return None
    for mapper in Base.registry.mappers:
        if mapper.local_table.name == name:
            return mapper.class_
    return None


def _model_for_select(stmt):
    for raw in stmt._raw_columns:
        entity = getattr(raw, "entity", None)
        if entity is not None and isinstance(entity, type) and hasattr(entity, "__tablename__"):
            return entity
        if isinstance(raw, type) and hasattr(raw, "__tablename__"):
            return raw
        table = getattr(raw, "table", None)
        model = _model_for_table(table) if table is not None else None
        if model:
            return model
    for table in stmt.get_final_froms():
        model = _model_for_table(table)
        if model:
            return model
    return None


def _value(expr):
    if isinstance(expr, BindParameter):
        return expr.value
    if hasattr(expr, "value") and not hasattr(expr, "key"):
        return expr.value
    return expr


def _column_name(expr):
    if isinstance(expr, UnaryExpression):
        expr = expr.element
    return getattr(expr, "key", None) or getattr(expr, "name", None)


def _compile_condition(expr):
    if isinstance(expr, BooleanClauseList):
        parts = [_compile_condition(item) for item in expr.clauses]
        if expr.operator is operator.or_:
            return {"$or": parts}
        return {"$and": parts}
    if isinstance(expr, BinaryExpression):
        key = _column_name(expr.left)
        if not key:
            raise ValueError(f"Unsupported MongoDB filter expression: {expr}")
        right = _value(expr.right)
        op = expr.operator
        if op is operator.eq:
            return {key: right}
        if op is operator.ne:
            return {key: {"$ne": right}}
        if op is operator.gt:
            return {key: {"$gt": right}}
        if op is operator.ge:
            return {key: {"$gte": right}}
        if op is operator.lt:
            return {key: {"$lt": right}}
        if op is operator.le:
            return {key: {"$lte": right}}
        if op.__name__ == "in_op":
            return {key: {"$in": list(right)}}
        if op.__name__ == "not_in_op":
            return {key: {"$nin": list(right)}}
        if op.__name__ == "is_":
            return {key: right}
        if op.__name__ == "is_not":
            return {key: {"$ne": right}}
        raise ValueError(f"Unsupported MongoDB comparison operator: {op}")
    raise ValueError(f"Unsupported MongoDB filter: {expr}")


def _combine_filters(criteria):
    if not criteria:
        return {}
    parts = [_compile_condition(item) for item in criteria]
    return parts[0] if len(parts) == 1 else {"$and": parts}


def _serialize(model):
    data = {}
    for column in model.__table__.columns:
        key = column.key
        value = getattr(model, key, None)
        if value is None and column.default is not None:
            default = column.default.arg
            value = default() if callable(default) else default
            setattr(model, key, value)
        data[key] = value
    return data


def _hydrate(model, doc):
    values = {key: value for key, value in doc.items() if key != "_id" and key in model.__table__.columns}
    for column in model.__table__.columns:
        key = column.key
        if key not in values and column.default is not None:
            default = column.default.arg
            values[key] = default() if callable(default) else default
        value = values.get(key)
        if isinstance(value, datetime) and value.tzinfo is None:
            values[key] = value.replace(tzinfo=timezone.utc)
    return model(**values)


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows
    def all(self):
        return [row[0] if isinstance(row, tuple) else row for row in self._rows]
    def first(self):
        rows = self.all()
        return rows[0] if rows else None


class _MongoResult:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount
    def all(self):
        return list(self._rows)
    def scalars(self):
        return _ScalarRows(self._rows)
    def scalar(self):
        if not self._rows:
            return None
        row = self._rows[0]
        return row[0] if isinstance(row, tuple) else row
    def scalar_one_or_none(self):
        return self.scalar()


class MongoSession:
    def __init__(self):
        self.db = _mongo()
        self.pending = []
        self.identity = {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
        self.close()
        return False

    def add(self, obj):
        self.pending.append(obj)

    def _collection(self, model):
        return self.db[model.__tablename__]

    def _next_id(self, model):
        counter = self.db["_apex_counters"].find_one_and_update(
            {"_id": model.__tablename__},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(counter["seq"])

    def _filter(self, stmt):
        return _combine_filters(stmt._where_criteria)

    def _sort(self, stmt):
        out = []
        for clause in stmt._order_by_clauses:
            direction = -1 if isinstance(clause, UnaryExpression) and clause.modifier.__name__ == "desc_op" else 1
            key = _column_name(clause)
            if key:
                out.append((key, direction))
        return out

    def _count_query(self, stmt, model, collection, filt):
        raw = stmt._raw_columns[0]
        if getattr(raw, "name", None) == "count" or "count(" in str(raw).lower():
            return collection.count_documents(filt)

    def execute(self, stmt):
        if getattr(stmt, "__visit_name__", "") == "update":
            model = _model_for_table(stmt.table)
            if model is None:
                raise ValueError("MongoDB update target could not be resolved")
            filt = _combine_filters(stmt._where_criteria)
            values = {}
            for key, value in (stmt._values or {}).items():
                values[getattr(key, "key", key)] = _value(value)
            result = self._collection(model).update_many(filt, {"$set": values})
            return _MongoResult(rowcount=result.modified_count)

        if not isinstance(stmt, Select):
            raise TypeError(f"Unsupported MongoDB statement: {type(stmt).__name__}")
        model = _model_for_select(stmt)
        if model is None:
            raise ValueError("MongoDB query model could not be resolved")
        collection = self._collection(model)
        filt = self._filter(stmt)
        raw = list(stmt._raw_columns)
        is_count = any("count(" in str(item).lower() for item in raw)
        if is_count and not stmt._group_by_clauses:
            return _MongoResult([(collection.count_documents(filt),)])
        sort = self._sort(stmt)
        limit = stmt._limit
        if stmt._group_by_clauses:
            group_key = _column_name(stmt._group_by_clauses[0])
            pipeline = [{"$match": filt}, {"$group": {"_id": f"${group_key}", "count": {"$sum": 1}}}]
            if sort:
                pipeline.append({"$sort": {"count": sort[0][1]}})
            if limit is not None:
                pipeline.append({"$limit": int(limit.value if hasattr(limit, "value") else limit)})
            rows = [(row["_id"], row["count"]) for row in collection.aggregate(pipeline)]
            return _MongoResult(rows)

        cursor = collection.find(filt)
        if sort:
            cursor = cursor.sort(sort)
        if limit is not None:
            cursor = cursor.limit(int(limit.value if hasattr(limit, "value") else limit))
        rows = list(cursor)
        selected_model = any(item is model for item in raw)
        if selected_model:
            values = []
            for doc in rows:
                obj_id = doc.get("id")
                identity_key = (model, obj_id)
                obj = self.identity.get(identity_key)
                if obj is None:
                    obj = _hydrate(model, doc)
                    self.identity[identity_key] = obj
                values.append(obj)
            return _MongoResult(values)
        keys = [_column_name(item) for item in raw]
        projected = []
        for doc in rows:
            vals = tuple(doc.get(key) for key in keys)
            projected.append(vals[0] if len(vals) == 1 else vals)
        return _MongoResult(projected)

    def scalar(self, stmt):
        return self.execute(stmt).scalar()

    def scalars(self, stmt):
        return self.execute(stmt).scalars()

    def get(self, model, identity):
        key = (model, identity)
        if key in self.identity:
            return self.identity[key]
        doc = self._collection(model).find_one({"id": identity})
        if not doc:
            return None
        obj = _hydrate(model, doc)
        self.identity[key] = obj
        return obj

    def commit(self):
        for obj in self.pending:
            data = _serialize(obj)
            if data.get("id") is None:
                data["id"] = self._next_id(type(obj))
                obj.id = data["id"]
            self._collection(type(obj)).replace_one({"id": data["id"]}, data, upsert=True)
            self.identity[(type(obj), data["id"])] = obj
        self.pending.clear()
        for (model, identity), obj in list(self.identity.items()):
            data = _serialize(obj)
            self._collection(model).replace_one({"id": identity}, data, upsert=True)

    def refresh(self, obj):
        doc = self._collection(type(obj)).find_one({"id": obj.id})
        if doc:
            fresh = _hydrate(type(obj), doc)
            for column in obj.__table__.columns:
                setattr(obj, column.key, getattr(fresh, column.key, None))
        return obj

    def rollback(self):
        self.pending.clear()
        self.identity.clear()

    def close(self):
        self.closed = True


SessionLocal = MongoSession


def init_db() -> None:
    # Import all models before creating indexes.
    from . import models as _models  # noqa: F401
    database = _mongo()
    for mapper in Base.registry.mappers:
        model = mapper.class_
        collection = database[model.__tablename__]
        collection.create_index("id", unique=True, sparse=True)
        for column in model.__table__.columns:
            if column.index:
                collection.create_index(column.key)
            if column.unique:
                collection.create_index(column.key, unique=True, sparse=True)
        for constraint in model.__table__.constraints:
            if isinstance(constraint, UniqueConstraint):
                keys = [(column.key, 1) for column in constraint.columns]
                collection.create_index(keys, unique=True, sparse=True)


def db_health() -> str:
    try:
        _mongo().command("ping")
        return "connected"
    except Exception:
        return "disconnected"
