"""通用分页辅助模块。

提供统一的分页参数解析和响应格式，供 routes 和 services 使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


@dataclass
class PageParams:
    """分页参数。page 从 1 开始。"""
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


@dataclass
class PageResult:
    """统一分页响应。"""
    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int

    def to_dict(self) -> dict:
        return {
            "items": self.items,
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "total_pages": self.total_pages,
            "has_more": self.page < self.total_pages,
        }


def resolve_page_params(
    page: int | None,
    page_size: int | None,
    limit: int | None,
    default_page_size: int = 20,
) -> PageParams:
    """从 query 参数解析分页参数。

    优先使用 page/page_size；如果只传了 limit（向后兼容），当作 page_size=limit, page=1。
    """
    if page is not None or page_size is not None:
        return PageParams(
            page=max(1, page or 1),
            page_size=max(1, min(5000, page_size or default_page_size)),
        )
    if limit is not None:
        return PageParams(page=1, page_size=max(1, min(500, limit)))
    return PageParams(page=1, page_size=default_page_size)


def paginate(
    session: Session,
    stmt: Select,
    count_stmt: Select | None,
    page_params: PageParams,
    to_dict_fn=None,
    use_scalars: bool = True,
) -> PageResult:
    """执行分页查询，返回 PageResult。

    Args:
        session: SQLAlchemy session
        stmt: 数据查询语句（不含 LIMIT/OFFSET）
        count_stmt: 计数语句，为 None 时自动从 stmt 推导
        page_params: 分页参数
        to_dict_fn: 可选的行转字典函数
        use_scalars: 是否使用 session.scalars()。ORM model 查询用 True（默认），
                     多列子查询用 False 以避免只返回第一列。
    """
    if count_stmt is None:
        count_stmt = select(func.count()).select_from(stmt.subquery())

    total = int(session.scalar(count_stmt) or 0)
    total_pages = max(1, (total + page_params.page_size - 1) // page_params.page_size) if total > 0 else 1

    paged_stmt = stmt.offset(page_params.offset).limit(page_params.limit)
    if use_scalars:
        rows = session.scalars(paged_stmt).all()
    else:
        rows = session.execute(paged_stmt).all()

    if to_dict_fn:
        items = [to_dict_fn(row) for row in rows]
    else:
        items = [dict(row.__dict__) if hasattr(row, "__dict__") else row for row in rows]
        for item in items:
            item.pop("_sa_instance_state", None)

    return PageResult(
        items=items,
        total=total,
        page=page_params.page,
        page_size=page_params.page_size,
        total_pages=total_pages,
    )
