from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import desc

from powertrade_crawler.market_agent.schemas import AgentProtocol, RiskLevel, RunStatus
from powertrade_crawler.market_agent.security import arguments_hash, redact_text, redact_value
from powertrade_crawler.llm_router import (
    DEFAULT_MODEL_STRATEGY,
    DEFAULT_ROUTER_ENDPOINT,
    FreeRouterManagementClient,
    LLMRouterConfigError,
    validate_model_strategy,
    validate_router_endpoint,
)
from powertrade_crawler.storage import (
    MarketAgentEventRow,
    MarketAgentMessageRow,
    MarketAgentRunRow,
    MarketAgentSessionRow,
    MarketAgentSettingRow,
    MarketAgentToolCallRow,
    get_session,
    init_db,
)


DEFAULT_ENDPOINT = DEFAULT_ROUTER_ENDPOINT
DEFAULT_FREE_MODEL = DEFAULT_MODEL_STRATEGY
DEFAULT_ADVANCED_MODEL = None
MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_CHARS = 24_000


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def json_dumps(value: Any) -> str:
    return json.dumps(redact_value(value), ensure_ascii=False, default=str)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class MarketAgentRepository:
    def __init__(self) -> None:
        init_db()

    def get_config(self) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(MarketAgentSettingRow, 1)
            if row is None:
                return {
                    "endpoint": DEFAULT_ENDPOINT,
                    "model_id": DEFAULT_FREE_MODEL,
                    "advanced_model_id": None,
                    "model_profile": "free",
                    "reasoning_effort": "high",
                    "protocol": AgentProtocol.AUTO.value,
                    "detected_protocol": None,
                }
            try:
                model_id = validate_model_strategy(row.model_id)
            except LLMRouterConfigError:
                model_id = DEFAULT_FREE_MODEL
            endpoint = row.endpoint
            try:
                endpoint = validate_router_endpoint(endpoint)
            except LLMRouterConfigError:
                endpoint = DEFAULT_ENDPOINT
            return {
                "endpoint": endpoint,
                "model_id": model_id,
                "advanced_model_id": None,
                "model_profile": "free",
                "reasoning_effort": row.reasoning_effort,
                "protocol": row.protocol,
                "detected_protocol": row.detected_protocol,
                "updated_at": row.updated_at.isoformat(),
            }

    def set_config(
        self,
        *,
        endpoint: str | None = None,
        model_id: str | None = None,
        advanced_model_id: str | None = None,
        model_profile: str | None = None,
        reasoning_effort: str | None = None,
        protocol: str | None = None,
        detected_protocol: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_config()
        next_endpoint = validate_router_endpoint(endpoint or current["endpoint"])
        next_profile = model_profile or current["model_profile"]
        next_reasoning = reasoning_effort or current["reasoning_effort"]
        next_protocol = protocol or current["protocol"]
        if next_profile != "free":
            raise ValueError("免费 LLM 网关只支持 free 模型档位。")
        if next_protocol not in {item.value for item in AgentProtocol}:
            raise ValueError("protocol must be auto, native, or json.")
        if next_reasoning not in {"low", "medium", "high"}:
            raise ValueError("reasoning_effort must be low, medium, or high.")
        next_model = validate_model_strategy(model_id or current["model_id"])
        next_advanced = None
        if advanced_model_id:
            raise ValueError("请使用 provider/<渠道ID> 指定免费渠道，不再使用 advanced_model_id。")
        if endpoint is not None or model_id is not None:
            with FreeRouterManagementClient.from_external_config() as router:
                if next_endpoint != router.config.api_base:
                    raise ValueError("Endpoint 必须与项目外 client-free.env 保持一致。")
                if model_id is not None:
                    router.ensure_strategy_available(next_model)
        now = utc_now_naive()
        with get_session() as session:
            row = session.get(MarketAgentSettingRow, 1)
            if row is None:
                row = MarketAgentSettingRow(
                    id=1,
                    endpoint=next_endpoint,
                    model_id=next_model,
                    advanced_model_id=next_advanced,
                    model_profile=next_profile,
                    reasoning_effort=next_reasoning,
                    protocol=next_protocol,
                    detected_protocol=detected_protocol,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.endpoint = next_endpoint
                row.model_id = next_model
                row.advanced_model_id = next_advanced
                row.model_profile = next_profile
                row.reasoning_effort = next_reasoning
                row.protocol = next_protocol
                if detected_protocol is not None:
                    row.detected_protocol = detected_protocol
                row.updated_at = now
            session.commit()
        return self.get_config()

    def create_session(self, title: str = "新会话") -> str:
        session_id = str(uuid4())
        now = utc_now_naive()
        with get_session() as session:
            session.add(
                MarketAgentSessionRow(
                    id=session_id,
                    title=(title.strip() or "新会话")[:200],
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        return session_id

    def ensure_session(self, session_id: str | None, title: str = "新会话") -> str:
        if not session_id:
            return self.create_session(title)
        with get_session() as session:
            if session.get(MarketAgentSessionRow, session_id) is None:
                raise ValueError(f"Unknown market agent session: {session_id}")
        return session_id

    def list_sessions(self) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(MarketAgentSessionRow)
                .order_by(desc(MarketAgentSessionRow.updated_at))
                .all()
            )
            return [
                {
                    "id": row.id,
                    "title": row.title,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ]

    def get_session_record(self, session_id: str) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(MarketAgentSessionRow, session_id)
            if row is None:
                raise ValueError(f"Unknown market agent session: {session_id}")
            messages = (
                session.query(MarketAgentMessageRow)
                .filter(MarketAgentMessageRow.session_id == session_id)
                .order_by(MarketAgentMessageRow.id)
                .all()
            )
            latest_run = (
                session.query(MarketAgentRunRow)
                .filter(MarketAgentRunRow.session_id == session_id)
                .order_by(desc(MarketAgentRunRow.created_at))
                .first()
            )
            return {
                "id": row.id,
                "title": row.title,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
                "messages": [
                    {
                        "id": message.id,
                        "run_id": message.run_id,
                        "role": message.role,
                        "content": json_loads(message.content_json, {}),
                        "created_at": message.created_at.isoformat(),
                    }
                    for message in messages
                ],
                "latest_run_id": latest_run.id if latest_run else None,
            }

    def delete_session(self, session_id: str) -> None:
        with get_session() as session:
            row = session.get(MarketAgentSessionRow, session_id)
            if row is None:
                raise ValueError(f"Unknown market agent session: {session_id}")
            run_ids = [
                item
                for (item,) in session.query(MarketAgentRunRow.id)
                .filter(MarketAgentRunRow.session_id == session_id)
                .all()
            ]
            if run_ids:
                session.query(MarketAgentEventRow).filter(
                    MarketAgentEventRow.run_id.in_(run_ids)
                ).delete(synchronize_session=False)
                session.query(MarketAgentToolCallRow).filter(
                    MarketAgentToolCallRow.run_id.in_(run_ids)
                ).delete(synchronize_session=False)
                session.query(MarketAgentRunRow).filter(
                    MarketAgentRunRow.id.in_(run_ids)
                ).delete(synchronize_session=False)
            session.query(MarketAgentMessageRow).filter(
                MarketAgentMessageRow.session_id == session_id
            ).delete(synchronize_session=False)
            session.delete(row)
            session.commit()

    def add_message(
        self,
        session_id: str,
        *,
        role: str,
        content: Any,
        run_id: str | None = None,
    ) -> int:
        now = utc_now_naive()
        with get_session() as session:
            session_row = session.get(MarketAgentSessionRow, session_id)
            if session_row is None:
                raise ValueError(f"Unknown market agent session: {session_id}")
            row = MarketAgentMessageRow(
                session_id=session_id,
                run_id=run_id,
                role=role,
                content_json=json_dumps(content),
                created_at=now,
            )
            session.add(row)
            session_row.updated_at = now
            if role == "user" and session_row.title == "新会话":
                raw = content.get("content", "") if isinstance(content, dict) else str(content)
                session_row.title = (redact_text(str(raw)).strip() or "新会话")[:60]
            session.commit()
            session.refresh(row)
            return int(row.id)

    def model_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.get_session_record(session_id)["messages"]
        candidates: list[dict[str, str]] = []
        for row in rows:
            if row["role"] not in {"user", "assistant"}:
                continue
            content = row["content"]
            text = (
                str(content.get("content"))
                if isinstance(content, dict) and "content" in content
                else json.dumps(content, ensure_ascii=False)
            )
            candidates.append({"role": row["role"], "content": text})
        selected: list[dict[str, str]] = []
        char_count = 0
        for item in reversed(candidates[-MAX_CONTEXT_MESSAGES:]):
            next_count = char_count + len(item["content"])
            if selected and next_count > MAX_CONTEXT_CHARS:
                break
            if next_count > MAX_CONTEXT_CHARS:
                item = {**item, "content": item["content"][-MAX_CONTEXT_CHARS:]}
                next_count = len(item["content"])
            selected.append(item)
            char_count = next_count
        return list(reversed(selected))

    def create_run(self, session_id: str, *, protocol: str, model_id: str) -> str:
        run_id = str(uuid4())
        now = utc_now_naive()
        with get_session() as session:
            session.add(
                MarketAgentRunRow(
                    id=run_id,
                    session_id=session_id,
                    status=RunStatus.RUNNING.value,
                    protocol=protocol,
                    model_id=model_id,
                    model_calls=0,
                    pending_context_json=None,
                    answer_json=None,
                    error_json=None,
                    usage_json="{}",
                    stop_requested=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(MarketAgentRunRow, run_id)
            if row is None:
                raise ValueError(f"Unknown market agent run: {run_id}")
            return {
                "id": row.id,
                "session_id": row.session_id,
                "status": row.status,
                "protocol": row.protocol,
                "model_id": row.model_id,
                "model_calls": row.model_calls,
                "pending_context": json_loads(row.pending_context_json, None),
                "answer": json_loads(row.answer_json, None),
                "error": json_loads(row.error_json, None),
                "usage": json_loads(row.usage_json, {}),
                "router_provider": row.router_provider,
                "upstream_model": row.upstream_model,
                "router_alert_count": row.router_alert_count,
                "stop_requested": row.stop_requested,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }

    def update_run(self, run_id: str, **changes: Any) -> None:
        with get_session() as session:
            row = session.get(MarketAgentRunRow, run_id)
            if row is None:
                raise ValueError(f"Unknown market agent run: {run_id}")
            scalar_fields = {
                "status",
                "model_calls",
                "stop_requested",
                "protocol",
                "router_provider",
                "upstream_model",
                "router_alert_count",
            }
            json_fields = {
                "pending_context": "pending_context_json",
                "answer": "answer_json",
                "error": "error_json",
                "usage": "usage_json",
            }
            for key in scalar_fields:
                if key in changes:
                    setattr(row, key, changes[key])
            for key, column in json_fields.items():
                if key in changes:
                    value = changes[key]
                    setattr(row, column, json_dumps(value) if value is not None else None)
            if changes.get("clear_pending_context"):
                row.pending_context_json = None
            row.updated_at = utc_now_naive()
            session.commit()

    def request_stop(self, run_id: str) -> None:
        self.update_run(run_id, stop_requested=True)

    def is_stopped(self, run_id: str) -> bool:
        return bool(self.get_run(run_id)["stop_requested"])

    def add_event(
        self,
        run_id: str,
        session_id: str,
        stage: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_naive()
        safe_detail = redact_value(detail or {})
        with get_session() as session:
            row = MarketAgentEventRow(
                run_id=run_id,
                session_id=session_id,
                stage=stage,
                detail_json=json_dumps(safe_detail),
                created_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return {
                "id": row.id,
                "run_id": run_id,
                "session_id": session_id,
                "stage": stage,
                "detail": safe_detail,
                "created_at": now.isoformat(),
            }

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(MarketAgentEventRow)
                .filter(MarketAgentEventRow.run_id == run_id)
                .order_by(MarketAgentEventRow.id)
                .all()
            )
            return [
                {
                    "id": row.id,
                    "stage": row.stage,
                    "detail": json_loads(row.detail_json, {}),
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def create_tool_call(
        self,
        *,
        call_id: str,
        run_id: str,
        session_id: str,
        tool_name: str,
        risk_level: RiskLevel,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        digest = arguments_hash(arguments)
        now = utc_now_naive()
        with get_session() as session:
            row = session.get(MarketAgentToolCallRow, call_id)
            if row is not None:
                if row.arguments_hash != digest or row.tool_name != tool_name:
                    raise ValueError("Tool call id was reused with changed parameters.")
                return self._tool_call_dict(row)
            duplicate = (
                session.query(MarketAgentToolCallRow)
                .filter(
                    MarketAgentToolCallRow.run_id == run_id,
                    MarketAgentToolCallRow.tool_name == tool_name,
                    MarketAgentToolCallRow.arguments_hash == digest,
                )
                .first()
            )
            if duplicate is not None:
                return self._tool_call_dict(duplicate)
            approval = "not_required" if risk_level == RiskLevel.AUTO else "pending"
            row = MarketAgentToolCallRow(
                id=call_id,
                run_id=run_id,
                session_id=session_id,
                tool_name=tool_name,
                risk_level=risk_level.value,
                arguments_json=json_dumps(arguments),
                arguments_hash=digest,
                approval_status=approval,
                status="proposed",
                result_json=None,
                error_json=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._tool_call_dict(row)

    def get_tool_call(self, call_id: str) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(MarketAgentToolCallRow, call_id)
            if row is None:
                raise ValueError(f"Unknown market agent tool call: {call_id}")
            return self._tool_call_dict(row)

    def list_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(MarketAgentToolCallRow)
                .filter(MarketAgentToolCallRow.run_id == run_id)
                .order_by(MarketAgentToolCallRow.created_at, MarketAgentToolCallRow.id)
                .all()
            )
            return [self._tool_call_dict(row) for row in rows]

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(MarketAgentToolCallRow)
                .filter(MarketAgentToolCallRow.approval_status == "pending")
                .order_by(MarketAgentToolCallRow.created_at)
                .all()
            )
            return [self._tool_call_dict(row) for row in rows]

    def decide_approval(
        self,
        call_id: str,
        *,
        approved: bool,
        expected_arguments_hash: str | None = None,
    ) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(MarketAgentToolCallRow, call_id)
            if row is None:
                raise ValueError(f"Unknown market agent tool call: {call_id}")
            if row.approval_status != "pending":
                raise ValueError(f"Tool call {call_id} is not pending approval.")
            if expected_arguments_hash and row.arguments_hash != expected_arguments_hash:
                raise ValueError("Tool parameters changed; approval is no longer valid.")
            row.approval_status = "approved" if approved else "rejected"
            row.status = "approved" if approved else "rejected"
            row.updated_at = utc_now_naive()
            session.commit()
            session.refresh(row)
            return self._tool_call_dict(row)

    def begin_tool_call(self, call_id: str) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(MarketAgentToolCallRow, call_id)
            if row is None:
                raise ValueError(f"Unknown market agent tool call: {call_id}")
            if row.status == "success":
                return self._tool_call_dict(row)
            if row.status in {"executing", "failed"}:
                raise ValueError("Incomplete write is not retried automatically.")
            if row.approval_status not in {"not_required", "approved"}:
                raise ValueError("Tool call has not been approved.")
            row.status = "executing"
            row.updated_at = utc_now_naive()
            session.commit()
            session.refresh(row)
            return self._tool_call_dict(row)

    def finish_tool_call(
        self,
        call_id: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(MarketAgentToolCallRow, call_id)
            if row is None:
                raise ValueError(f"Unknown market agent tool call: {call_id}")
            if row.status == "success":
                return self._tool_call_dict(row)
            row.status = "success" if error is None else "failed"
            row.result_json = json_dumps(result) if result is not None else None
            row.error_json = json_dumps(error) if error is not None else None
            row.updated_at = utc_now_naive()
            session.commit()
            session.refresh(row)
            return self._tool_call_dict(row)

    @staticmethod
    def _tool_call_dict(row: MarketAgentToolCallRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "session_id": row.session_id,
            "tool_name": row.tool_name,
            "risk_level": row.risk_level,
            "arguments": json_loads(row.arguments_json, {}),
            "arguments_hash": row.arguments_hash,
            "approval_status": row.approval_status,
            "status": row.status,
            "result": json_loads(row.result_json, None),
            "error": json_loads(row.error_json, None),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
