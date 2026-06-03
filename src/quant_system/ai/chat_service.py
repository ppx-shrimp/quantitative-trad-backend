"""AI 多轮对话服务。

围绕一次 AI 分析结果展开追问对话，支持：
- 从分析结果创建对话会话
- 发送追问消息并获取 AI 回复
- 获取历史消息列表
- Mock 模式下的规则回复
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from quant_system.ai.llm_client import OpenAICompatibleClient
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import AIChatMessageModel, AIAnalysisRecordModel


class AIChatService:
    def __init__(self, llm_client: OpenAICompatibleClient | None = None) -> None:
        init_sqlalchemy_tables()
        self.llm_client = llm_client or OpenAICompatibleClient()

    def create_session(self, analysis_id: str) -> dict[str, Any]:
        """基于已有分析记录创建对话会话，返回 session_id。"""
        # 验证 analysis_id 存在
        with SessionLocal() as session:
            record = session.scalar(
                select(AIAnalysisRecordModel).where(AIAnalysisRecordModel.analysis_id == analysis_id)
            )
        if record is None:
            return {"error": f"分析记录 {analysis_id} 不存在", "session_id": None}

        session_id = f"chat_{uuid.uuid4().hex[:16]}"
        now = self._now()

        # 将原始分析结果摘要作为 system 类型的首条消息存入
        analysis_summary = self._build_analysis_summary(record)
        self._save_message(
            session_id=session_id,
            analysis_id=analysis_id,
            seq=0,
            role="system",
            content=analysis_summary,
            now=now,
        )
        return {
            "session_id": session_id,
            "analysis_id": analysis_id,
            "created_at": now,
        }

    def send_message(self, session_id: str, user_message: str) -> dict[str, Any]:
        """用户发送追问消息，返回 AI 回复。"""
        if not user_message.strip():
            return {"error": "消息不能为空"}

        # 获取该会话的元数据
        with SessionLocal() as db:
            first_msg = db.scalar(
                select(AIChatMessageModel)
                .where(AIChatMessageModel.session_id == session_id, AIChatMessageModel.seq == 0)
            )
        if first_msg is None:
            return {"error": f"会话 {session_id} 不存在，请先创建会话"}

        analysis_id = first_msg.analysis_id
        now = self._now()

        # 获取当前最大 seq
        next_seq = self._next_seq(session_id)

        # 保存用户消息
        self._save_message(
            session_id=session_id,
            analysis_id=analysis_id,
            seq=next_seq,
            role="user",
            content=user_message.strip(),
            now=now,
        )

        # 构建对话历史并调用 LLM 或 Mock
        history = self._load_history(session_id)
        analysis_type = self._get_analysis_type(analysis_id)

        if self.llm_client.enabled():
            reply = self._call_llm_chat(history, analysis_type)
            model_name = self.llm_client.model
        else:
            reply = self._mock_reply(user_message, history)
            model_name = "mock_chat_v1"

        # 保存 AI 回复
        reply_seq = next_seq + 1
        self._save_message(
            session_id=session_id,
            analysis_id=analysis_id,
            seq=reply_seq,
            role="assistant",
            content=reply,
            now=self._now(),
            model_name=model_name,
        )

        return {
            "session_id": session_id,
            "seq": reply_seq,
            "role": "assistant",
            "content": reply,
            "model_name": model_name,
            "created_at": self._now(),
        }

    def get_messages(self, session_id: str) -> dict[str, Any]:
        """获取会话的全部消息。"""
        with SessionLocal() as db:
            rows = db.scalars(
                select(AIChatMessageModel)
                .where(AIChatMessageModel.session_id == session_id)
                .order_by(AIChatMessageModel.seq)
            ).all()
        messages = [
            {
                "seq": row.seq,
                "role": row.role,
                "content": row.content,
                "model_name": row.model_name,
                "created_at": row.created_at,
            }
            for row in rows
        ]
        return {
            "session_id": session_id,
            "count": len(messages),
            "messages": messages,
        }

    # ── 内部方法 ──────────────────────────────────────────

    def _call_llm_chat(self, history: list[dict[str, str]], analysis_type: str) -> str:
        """用多轮消息调用 LLM。"""
        # history 中 seq=0 的 system 消息作为上下文注入 user 角色（因为 system prompt 由 complete_chat 统一注入）
        messages: list[dict[str, str]] = []
        for msg in history:
            role = msg["role"]
            if role == "system":
                # 原始分析摘要作为首条 user 消息的前置上下文
                messages.append({"role": "user", "content": f"[分析上下文]\n{msg['content']}"})
                messages.append({"role": "assistant", "content": "好的，我已了解这次分析的完整上下文。请问你有什么想追问的？"})
            else:
                messages.append({"role": role, "content": msg["content"]})

        return self.llm_client.complete_chat(
            messages,
            analysis_type=analysis_type,
            system_prompt=self._chat_system_prompt(analysis_type),
        )

    def _chat_system_prompt(self, analysis_type: str) -> str:
        type_hint = {
            "buy_decision": "买入决策追问",
            "position_review": "持仓复盘追问",
            "risk_review": "风险审查追问",
        }.get(analysis_type, "AI 分析追问")
        return f"""你是 A 股量化模拟交易系统的 AI 追问助手，当前场景是【{type_hint}】。
你只能基于用户本次分析上下文和后续追问回答，不允许编造行情、新闻、持仓或交易结果。
回答必须是面向用户可读的自然语言，不要返回 JSON，不要返回 Markdown 代码块，不要把 suggested_plan、data_quality 或上下文原文整段贴出。
如果需要引用已有分析结论，请用简短中文解释，例如“技术面偏弱”“消息面证据不足”“当前未持仓”。
如果涉及交易，只能说“可考虑/建议观察/需要手动确认”，不得说系统会自动下单。
回答建议控制在 3 到 6 句话；如用户问止损止盈，可以列出关键价位或条件，但不要输出原始 JSON。"""

    def _mock_reply(self, user_message: str, history: list[dict[str, str]]) -> str:
        """Mock 模式下的规则回复。"""
        msg = user_message.lower()
        turn = sum(1 for m in history if m["role"] == "user")

        if any(kw in msg for kw in ["为什么", "原因", "理由"]):
            return (
                "（Mock 模式回复）基于当前的技术评分和风控规则：\n"
                "1. 趋势评分和技术评分是判断方向的核心依据\n"
                "2. 如果存在持仓，盈亏比例会触发系统风控规则\n"
                "3. 资讯面的利好/利空也会影响置信度\n\n"
                "如需更详细的分析，请配置真实 LLM 模型。"
            )
        if any(kw in msg for kw in ["风险", "止损", "止盈"]):
            return (
                "（Mock 模式回复）系统风控规则如下：\n"
                "- 成本价下跌 5% → 减半仓（100 股直接清仓）\n"
                "- 成本价下跌 10% → 清仓\n"
                "- 成本价上涨 10% → 减 1/3 仓位\n"
                "- 成本价上涨 20% → 减半仓\n"
                "- 成本价上涨 50% → 清仓\n\n"
                "建议严格执行，避免情绪化操作。"
            )
        if any(kw in msg for kw in ["买入", "加仓", "建仓"]):
            return (
                "（Mock 模式回复）关于买入建议：\n"
                "- 当前 Mock 模式无法给出实时判断\n"
                "- 建议参考初始分析结果中的 action 和 confidence\n"
                "- 首次建仓建议 100 股最小单位试探\n"
                "- 配置真实模型后可获得更精准的买入时机分析。"
            )
        return (
            f"（Mock 模式回复 · 第 {turn} 轮对话）\n"
            "当前使用规则引擎回复，暂不支持深度追问。\n"
            "如需真实的多轮 AI 对话，请在 .env 中配置：\n"
            "QUANT_AI_MOCK_ENABLED=false\n"
            "QUANT_LLM_BASE_URL=...\n"
            "QUANT_LLM_API_KEY=..."
        )

    def _build_analysis_summary(self, record: AIAnalysisRecordModel) -> str:
        """从分析记录构建上下文摘要文本。"""
        parts = [
            f"股票代码: {record.symbol}",
            f"分析类型: {record.analysis_type}",
            f"AI 建议: {record.action or '-'}",
            f"置信度: {record.confidence or '-'}",
            f"风险等级: {record.risk_level or '-'}",
            f"模型: {record.model_provider or 'mock'} / {record.model_name or '-'}",
            f"状态: {record.status}",
        ]
        # 尝试附加输出 JSON 中的 summary 和 reasons
        if record.output_json:
            try:
                output = json.loads(record.output_json)
                if isinstance(output, dict):
                    if output.get("summary"):
                        parts.append(f"分析摘要: {output['summary']}")
                    if output.get("reasons"):
                        parts.append(f"核心理由: {', '.join(output['reasons'])}")
                    if output.get("risk_warnings"):
                        parts.append(f"风险提示: {', '.join(output['risk_warnings'])}")
            except Exception:
                pass
        # 附加上下文 JSON（截断避免过长）
        if record.context_json:
            ctx_text = record.context_json[:2000]
            parts.append(f"\n完整上下文（截断）:\n{ctx_text}")
        return "\n".join(parts)

    def _load_history(self, session_id: str) -> list[dict[str, str]]:
        """加载会话历史消息。"""
        with SessionLocal() as db:
            rows = db.scalars(
                select(AIChatMessageModel)
                .where(AIChatMessageModel.session_id == session_id)
                .order_by(AIChatMessageModel.seq)
            ).all()
        return [{"role": row.role, "content": row.content} for row in rows]

    def _next_seq(self, session_id: str) -> int:
        """获取下一个消息序号。"""
        with SessionLocal() as db:
            max_seq = db.scalar(
                select(func.max(AIChatMessageModel.seq))
                .where(AIChatMessageModel.session_id == session_id)
            )
        return (max_seq or 0) + 1

    def _get_analysis_type(self, analysis_id: str) -> str:
        """从分析记录获取分析类型。"""
        with SessionLocal() as db:
            record = db.scalar(
                select(AIAnalysisRecordModel.analysis_type)
                .where(AIAnalysisRecordModel.analysis_id == analysis_id)
            )
        return record or "buy_decision"

    def _save_message(
        self,
        *,
        session_id: str,
        analysis_id: str,
        seq: int,
        role: str,
        content: str,
        now: str,
        model_name: str | None = None,
    ) -> None:
        msg = AIChatMessageModel(
            session_id=session_id,
            analysis_id=analysis_id,
            seq=seq,
            role=role,
            content=content,
            model_name=model_name,
            created_at=now,
            updated_at=now,
            created_by="system",
            updated_by="system",
        )
        with SessionLocal() as db:
            db.add(msg)
            db.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
