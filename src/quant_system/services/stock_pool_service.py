from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import func, select

from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import StockPoolMemberModel, StockPoolModel


DEFAULT_POOLS = [
    ("favorites", "自选池", "同花顺自选股导入及前端手动自选", "manual"),
    ("candidates", "候选池", "后续策略扫描和模拟交易候选股票", "strategy"),
    ("watchlist", "观察池", "暂时关注但不一定交易的股票", "manual"),
    ("blacklist", "黑名单", "明确排除，不参与策略扫描", "risk"),
    ("themes", "主题/板块池", "同花顺概念、板块、行业等非个股对象", "theme"),
]

INITIAL_FAVORITES = [
    ("002709", "天赐材料"),
    ("000586", "汇源通信"),
    ("002902", "铭普光磁"),
    ("002580", "圣阳股份"),
    ("000988", "华工科技"),
    ("002364", "中恒电气"),
    ("603002", "宏昌电子"),
    ("002213", "大为股份"),
    ("600482", "中国动力"),
    ("600176", "中国巨石"),
    ("601991", "大唐发电"),
    ("600426", "华鲁恒升"),
    ("603399", "永杉锂业"),
    ("603618", "杭电股份"),
    ("603629", "利通电子"),
    ("002407", "多氟多"),
    ("600487", "亨通光电"),
    ("600522", "中天科技"),
    ("002240", "盛新锂能"),
    ("600236", "桂冠电力"),
    ("000880", "潍柴重机"),
    ("002498", "汉缆股份"),
    ("600330", "天通股份"),
    ("000066", "中国长城"),
    ("002428", "云南锗业"),
    ("002759", "天际股份"),
    ("001896", "豫能控股"),
    ("600396", "华电辽能"),
    ("600150", "中国船舶"),
    ("002463", "沪电股份"),
    ("000831", "中国稀土"),
    ("600259", "中稀有色"),
    ("002031", "巨轮智能"),
    ("002050", "三花智控"),
    ("600703", "三安光电"),
    ("600986", "浙文互联"),
    ("600875", "东方电气"),
    ("601727", "上海电气"),
]

INITIAL_THEMES = [
    ("884215", "稀土"),
    ("885710", "锂电池概念"),
    ("885959", "PCB概念"),
    ("885957", "东数西算(算力)"),
    ("881278", "电网设备"),
]


class StockPoolService:
    def __init__(self) -> None:
        self.initialize()

    def initialize(self) -> None:
        init_sqlalchemy_tables()
        with SessionLocal() as session:
            self._seed_default_pools(session)
            self._seed_initial_members(session)
            session.commit()

    def _seed_default_pools(self, session) -> None:
        now = self._now()
        for code, name, description, pool_type in DEFAULT_POOLS:
            existing = session.scalar(select(StockPoolModel).where(StockPoolModel.code == code))
            if existing is None:
                session.add(
                    StockPoolModel(
                        code=code,
                        name=name,
                        description=description,
                        pool_type=pool_type,
                        created_at=now,
                        updated_at=now,
                        created_by="system",
                        updated_by="system",
                    )
                )

    def _seed_initial_members(self, session) -> None:
        for symbol, name in INITIAL_FAVORITES:
            self._add_member_session(
                session,
                pool_code="favorites",
                symbol=symbol,
                name=name,
                reason="同花顺自选股初始导入",
                tags=["同花顺自选"],
                source="imported_tonghuashun",
            )
        for symbol, name in INITIAL_THEMES:
            self._add_member_session(
                session,
                pool_code="themes",
                symbol=symbol,
                name=name,
                reason="同花顺自选中的主题/板块初始导入",
                tags=["主题", "板块"],
                source="imported_tonghuashun",
            )

    def list_pools(self) -> list[dict]:
        with SessionLocal() as session:
            rows = session.execute(
                select(StockPoolModel, func.count(StockPoolMemberModel.id).label("member_count"))
                .outerjoin(
                    StockPoolMemberModel,
                    (StockPoolModel.code == StockPoolMemberModel.pool_code) & (StockPoolMemberModel.enabled == True),  # noqa: E712
                )
                .group_by(
                    StockPoolModel.id,
                    StockPoolModel.code,
                    StockPoolModel.name,
                    StockPoolModel.description,
                    StockPoolModel.pool_type,
                    StockPoolModel.created_at,
                    StockPoolModel.updated_at,
                    StockPoolModel.created_by,
                    StockPoolModel.updated_by,
                )
                .order_by(StockPoolModel.code)
            ).all()
            return [self._pool_to_dict(pool, int(member_count or 0)) for pool, member_count in rows]

    def list_members(self, pool_code: str, enabled_only: bool = True) -> list[dict]:
        with SessionLocal() as session:
            self._ensure_pool_exists(session, pool_code)
            stmt = select(StockPoolMemberModel).where(StockPoolMemberModel.pool_code == pool_code)
            if enabled_only:
                stmt = stmt.where(StockPoolMemberModel.enabled == True)  # noqa: E712
            stmt = stmt.order_by(StockPoolMemberModel.created_at.asc(), StockPoolMemberModel.id.asc())
            rows = session.scalars(stmt).all()
            return [self._member_to_dict(row) for row in rows]

    def add_member(
        self,
        pool_code: str,
        symbol: str,
        name: str | None = None,
        reason: str | None = None,
        tags: list[str] | None = None,
        source: str = "manual",
    ) -> dict:
        with SessionLocal() as session:
            self._ensure_pool_exists(session, pool_code)
            member = self._add_member_session(session, pool_code, symbol, name, reason, tags, source)
            result = self._member_to_dict(member)
            session.commit()
            return result

    def update_member(
        self,
        pool_code: str,
        symbol: str,
        name: str | None = None,
        reason: str | None = None,
        tags: list[str] | None = None,
        enabled: bool | None = None,
    ) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        with SessionLocal() as session:
            self._ensure_pool_exists(session, pool_code)
            member = self._get_member(session, pool_code, normalized_symbol)
            if member is None:
                raise ValueError(f"股票不在股票池中：{pool_code}/{symbol}")
            if name is not None:
                member.name = name
            if reason is not None:
                member.reason = reason
            if tags is not None:
                member.tags = json.dumps(tags, ensure_ascii=False)
            if enabled is not None:
                member.enabled = enabled
            member.updated_at = self._now()
            member.updated_by = "system"
            result = self._member_to_dict(member)
            session.commit()
            return result

    def remove_member(self, pool_code: str, symbol: str) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        with SessionLocal() as session:
            member = self._get_member(session, pool_code, normalized_symbol)
            if member is None:
                raise ValueError(f"股票不在股票池中：{pool_code}/{symbol}")
            result = self._member_to_dict(member)
            session.delete(member)
            session.commit()
            return result

    def _add_member_session(
        self,
        session,
        pool_code: str,
        symbol: str,
        name: str | None,
        reason: str | None,
        tags: list[str] | None,
        source: str,
    ) -> StockPoolMemberModel:
        now = self._now()
        normalized_symbol = self._normalize_symbol(symbol)
        member = self._get_member(session, pool_code, normalized_symbol)
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        if member is None:
            member = StockPoolMemberModel(
                pool_code=pool_code,
                symbol=normalized_symbol,
                name=name,
                reason=reason,
                tags=tags_json,
                source=source,
                enabled=True,
                created_at=now,
                updated_at=now,
                created_by="system",
                updated_by="system",
            )
            session.add(member)
        else:
            if name is not None:
                member.name = name
            if reason is not None:
                member.reason = reason
            member.tags = tags_json
            member.source = source
            member.enabled = True
            member.updated_at = now
            member.updated_by = "system"
        return member

    def _ensure_pool_exists(self, session, pool_code: str) -> None:
        exists = session.scalar(select(StockPoolModel.id).where(StockPoolModel.code == pool_code))
        if exists is None:
            raise ValueError(f"股票池不存在：{pool_code}")

    def _get_member(self, session, pool_code: str, symbol: str) -> StockPoolMemberModel | None:
        return session.scalar(
            select(StockPoolMemberModel).where(
                StockPoolMemberModel.pool_code == pool_code,
                StockPoolMemberModel.symbol == self._normalize_symbol(symbol),
            )
        )

    def _pool_to_dict(self, row: StockPoolModel, member_count: int) -> dict:
        return {
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "description": row.description,
            "pool_type": row.pool_type,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "created_by": row.created_by,
            "updated_by": row.updated_by,
            "member_count": member_count,
        }

    def _member_to_dict(self, row: StockPoolMemberModel) -> dict:
        try:
            tags = json.loads(row.tags or "[]")
        except json.JSONDecodeError:
            tags = []
        return {
            "id": row.id,
            "pool_code": row.pool_code,
            "symbol": row.symbol,
            "name": row.name,
            "reason": row.reason,
            "tags": tags,
            "source": row.source,
            "enabled": bool(row.enabled),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "created_by": row.created_by,
            "updated_by": row.updated_by,
        }

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper().split(".")[0]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
