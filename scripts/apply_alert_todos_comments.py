from __future__ import annotations

from sqlalchemy import create_engine, text

from quant_system.core.config import settings


TABLE_COMMENT = "预警待办事项表"

COLUMN_COMMENTS = {
    "id": "自增主键",
    "todo_id": "待办唯一 ID",
    "dedupe_key": "去重键，用于避免重复生成同一条待办",
    "source_type": "来源类型，例如 alert / analysis / order / rule",
    "source_id": "来源记录 ID",
    "symbol": "股票代码",
    "stock_name": "股票名称",
    "severity": "严重级别，例如 low / medium / high / critical",
    "status": "待办状态，例如 open / acknowledged / resolved / ignored / snoozed",
    "title": "待办标题",
    "message": "待办详细说明",
    "suggested_action": "建议动作，例如 buy / sell / hold / review",
    "suggested_direction": "建议方向，例如 up / down / neutral",
    "suggested_quantity": "建议数量",
    "current_price": "当前价格",
    "avg_cost": "平均持仓成本",
    "pnl_pct": "盈亏比例",
    "action_required": "是否需要人工操作",
    "analysis_id": "关联分析记录 ID",
    "linked_order_id": "关联订单 ID",
    "snooze_until": "忽略/稍后提醒截止时间",
    "acknowledged_at": "确认查看时间",
    "resolved_at": "处理完成时间",
    "ignored_at": "忽略时间",
    "note": "人工备注",
    "payload_json": "扩展载荷 JSON",
    "created_at": "创建时间 ISO 字符串",
    "updated_at": "更新时间 ISO 字符串",
    "created_by": "创建人",
    "updated_by": "更新人",
}


def main() -> None:
    if not settings.database_url:
        raise RuntimeError("未配置 QUANT_DATABASE_URL，无法更新 MySQL 注释。")

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE alert_todos COMMENT = '{TABLE_COMMENT}'"))
        rows = conn.execute(
            text(
                """
                SELECT column_name, column_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'alert_todos'
                ORDER BY ordinal_position
                """
            )
        ).fetchall()

        for column_name, column_type, is_nullable in rows:
            comment = COLUMN_COMMENTS.get(column_name)
            if not comment:
                continue
            nullable_sql = "NULL" if str(is_nullable).upper() == "YES" else "NOT NULL"
            conn.execute(
                text(
                    f"ALTER TABLE alert_todos MODIFY COLUMN `{column_name}` {column_type} {nullable_sql} COMMENT :comment"
                ),
                {"comment": comment},
            )

    print("alert_todos comments applied")


if __name__ == "__main__":
    main()
