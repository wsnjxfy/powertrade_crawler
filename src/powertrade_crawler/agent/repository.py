from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from urllib.parse import urlparse

from sqlalchemy import desc

from powertrade_crawler.agent.schemas import AgentProtocol, RiskLevel, RunStatus
from powertrade_crawler.agent.security import arguments_hash, redact_text, redact_value
from powertrade_crawler.storage import (
    AgentEventRow,
    AgentMessageRow,
    AgentRunRow,
    AgentSessionRow,
    AgentSettingRow,
    AgentToolCallRow,
    get_session,
    init_db,
)


DEFAULT_ENDPOINT = "https://api.siliconflow.cn/v1"
DEFAULT_FREE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
DEFAULT_ADVANCED_MODEL = "deepseek-ai/DeepSeek-V4-Flash"


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


class AgentRepository:
    def __init__(self) -> None:
        init_db()

    def get_config(self) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(AgentSettingRow, 1)
            if row is None:
                return {
                    "endpoint": DEFAULT_ENDPOINT,
                    "model_id": DEFAULT_FREE_MODEL,
                    "advanced_model_id": DEFAULT_ADVANCED_MODEL,
                    "model_profile": "free",
                    "reasoning_effort": "high",
                    "protocol": AgentProtocol.AUTO.value,
                    "detected_protocol": None,
                }
            return {
                "endpoint": row.endpoint,
                "model_id": row.model_id,
                "advanced_model_id": row.advanced_model_id,
                "model_profile": row.model_profile,
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
        next_endpoint = (endpoint if endpoint is not None else current["endpoint"]).strip()
        next_model = (model_id if model_id is not None else current["model_id"]).strip()
        next_advanced_model = (
            advanced_model_id
            if advanced_model_id is not None
            else current["advanced_model_id"]
        )
        next_advanced_model = next_advanced_model.strip() if next_advanced_model else None
        next_profile = model_profile or current["model_profile"]
        next_reasoning_effort = reasoning_effort or current["reasoning_effort"]
        next_protocol = protocol if protocol is not None else current["protocol"]
        parsed_endpoint = urlparse(next_endpoint)
        if (
            parsed_endpoint.scheme != "https"
            or parsed_endpoint.hostname != "api.siliconflow.cn"
        ):
            raise ValueError(
                "SiliconFlow API Key 只能发送到 https://api.siliconflow.cn。"
            )
        if next_protocol not in {item.value for item in AgentProtocol}:
            raise ValueError("protocol must be auto, native, or json.")
        if next_profile not in {"free", "advanced"}:
            raise ValueError("model_profile must be free or advanced.")
        if next_reasoning_effort not in {None, "low", "medium", "high"}:
            raise ValueError("reasoning_effort must be low, medium, or high.")
        if next_profile == "advanced" and not next_advanced_model:
            raise ValueError("advanced model profile requires advanced_model_id.")
        now = utc_now_naive()
        with get_session() as session:
            row = session.get(AgentSettingRow, 1)
            if row is None:
                row = AgentSettingRow(
                    id=1,
                    endpoint=next_endpoint.rstrip("/"),
                    model_id=next_model,
                    advanced_model_id=next_advanced_model,
                    model_profile=next_profile,
                    reasoning_effort=next_reasoning_effort,
                    protocol=next_protocol,
                    detected_protocol=detected_protocol,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.endpoint = next_endpoint.rstrip("/")
                row.model_id = next_model
                row.advanced_model_id = next_advanced_model
                row.model_profile = next_profile
                row.reasoning_effort = next_reasoning_effort
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
                AgentSessionRow(
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
            row = session.get(AgentSessionRow, session_id)
            if row is None:
                raise ValueError(f"Unknown agent session: {session_id}")
        return session_id

    def list_sessions(self) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(AgentSessionRow)
                .order_by(desc(AgentSessionRow.updated_at))
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
            row = session.get(AgentSessionRow, session_id)
            if row is None:
                raise ValueError(f"Unknown agent session: {session_id}")
            messages = (
                session.query(AgentMessageRow)
                .filter(AgentMessageRow.session_id == session_id)
                .order_by(AgentMessageRow.id)
                .all()
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
            }

    def delete_session(self, session_id: str) -> None:
        with get_session() as session:
            row = session.get(AgentSessionRow, session_id)
            if row is None:
                raise ValueError(f"Unknown agent session: {session_id}")
            run_ids = [
                value
                for (value,) in session.query(AgentRunRow.id)
                .filter(AgentRunRow.session_id == session_id)
                .all()
            ]
            if run_ids:
                session.query(AgentEventRow).filter(
                    AgentEventRow.run_id.in_(run_ids)
                ).delete(synchronize_session=False)
                session.query(AgentToolCallRow).filter(
                    AgentToolCallRow.run_id.in_(run_ids)
                ).delete(synchronize_session=False)
                session.query(AgentRunRow).filter(
                    AgentRunRow.id.in_(run_ids)
                ).delete(synchronize_session=False)
            session.query(AgentMessageRow).filter(
                AgentMessageRow.session_id == session_id
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
            row = AgentMessageRow(
                session_id=session_id,
                run_id=run_id,
                role=role,
                content_json=json_dumps(content),
                created_at=now,
            )
            session.add(row)
            session_row = session.get(AgentSessionRow, session_id)
            if session_row is None:
                raise ValueError(f"Unknown agent session: {session_id}")
            session_row.updated_at = now
            if role == "user" and session_row.title == "新会话":
                if isinstance(content, dict):
                    text = str(content.get("content") or "")
                else:
                    text = str(content)
                session_row.title = (redact_text(text).strip() or "新会话")[:60]
            session.commit()
            session.refresh(row)
            return int(row.id)

    def model_messages(self, session_id: str) -> list[dict[str, Any]]:
        record = self.get_session_record(session_id)
        messages: list[dict[str, Any]] = []
        for row in record["messages"]:
            if row["role"] not in {"user", "assistant"}:
                continue
            content = row["content"]
            if isinstance(content, dict) and "content" in content:
                messages.append({"role": row["role"], "content": content["content"]})
            else:
                messages.append(
                    {
                        "role": row["role"],
                        "content": json.dumps(content, ensure_ascii=False),
                    }
                )
        return messages

    def create_run(self, session_id: str, *, protocol: str, model_id: str) -> str:
        run_id = str(uuid4())
        now = utc_now_naive()
        with get_session() as session:
            session.add(
                AgentRunRow(
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
            row = session.get(AgentRunRow, run_id)
            if row is None:
                raise ValueError(f"Unknown agent run: {run_id}")
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
                "stop_requested": row.stop_requested,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        model_calls: int | None = None,
        pending_context: dict[str, Any] | None = None,
        clear_pending_context: bool = False,
        answer: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        stop_requested: bool | None = None,
    ) -> None:
        with get_session() as session:
            row = session.get(AgentRunRow, run_id)
            if row is None:
                raise ValueError(f"Unknown agent run: {run_id}")
            if status is not None:
                row.status = status
            if model_calls is not None:
                row.model_calls = model_calls
            if pending_context is not None:
                row.pending_context_json = json_dumps(pending_context)
            elif clear_pending_context:
                row.pending_context_json = None
            if answer is not None:
                row.answer_json = json_dumps(answer)
            if error is not None:
                row.error_json = json_dumps(error)
            if usage is not None:
                row.usage_json = json_dumps(usage)
            if stop_requested is not None:
                row.stop_requested = stop_requested
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
            row = AgentEventRow(
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
                session.query(AgentEventRow)
                .filter(AgentEventRow.run_id == run_id)
                .order_by(AgentEventRow.id)
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
            row = session.get(AgentToolCallRow, call_id)
            if row is not None:
                if row.arguments_hash != digest or row.tool_name != tool_name:
                    raise ValueError(
                        "Tool call id was reused with changed parameters; prior approval is invalid."
                    )
                return self._tool_call_dict(row)
            duplicate = (
                session.query(AgentToolCallRow)
                .filter(
                    AgentToolCallRow.run_id == run_id,
                    AgentToolCallRow.tool_name == tool_name,
                    AgentToolCallRow.arguments_hash == digest,
                )
                .order_by(AgentToolCallRow.created_at)
                .first()
            )
            if duplicate is not None:
                return self._tool_call_dict(duplicate)
            approval_status = (
                "not_required" if risk_level == RiskLevel.AUTO else "pending"
            )
            row = AgentToolCallRow(
                id=call_id,
                run_id=run_id,
                session_id=session_id,
                tool_name=tool_name,
                risk_level=risk_level.value,
                arguments_json=json_dumps(arguments),
                arguments_hash=digest,
                approval_status=approval_status,
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
            row = session.get(AgentToolCallRow, call_id)
            if row is None:
                raise ValueError(f"Unknown agent tool call: {call_id}")
            return self._tool_call_dict(row)

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(AgentToolCallRow)
                .filter(AgentToolCallRow.approval_status == "pending")
                .order_by(AgentToolCallRow.created_at)
                .all()
            )
            return [self._tool_call_dict(row) for row in rows]

    def list_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        with get_session() as session:
            rows = (
                session.query(AgentToolCallRow)
                .filter(AgentToolCallRow.run_id == run_id)
                .order_by(AgentToolCallRow.created_at, AgentToolCallRow.id)
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
            row = session.get(AgentToolCallRow, call_id)
            if row is None:
                raise ValueError(f"Unknown agent tool call: {call_id}")
            if row.approval_status != "pending":
                raise ValueError(
                    f"Tool call {call_id} is not pending approval "
                    f"(current: {row.approval_status})."
                )
            if expected_arguments_hash and row.arguments_hash != expected_arguments_hash:
                raise ValueError("Tool parameters changed; approval is no longer valid.")
            row.approval_status = "approved" if approved else "rejected"
            row.status = "approved" if approved else "rejected"
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
            row = session.get(AgentToolCallRow, call_id)
            if row is None:
                raise ValueError(f"Unknown agent tool call: {call_id}")
            if row.status == "success":
                return self._tool_call_dict(row)
            row.status = "success" if error is None else "failed"
            row.result_json = json_dumps(result) if result is not None else None
            row.error_json = json_dumps(error) if error is not None else None
            row.updated_at = utc_now_naive()
            session.commit()
            session.refresh(row)
            return self._tool_call_dict(row)

    def begin_tool_call(self, call_id: str) -> dict[str, Any]:
        with get_session() as session:
            row = session.get(AgentToolCallRow, call_id)
            if row is None:
                raise ValueError(f"Unknown agent tool call: {call_id}")
            if row.status == "success":
                return self._tool_call_dict(row)
            if row.status in {"executing", "failed"}:
                raise ValueError(
                    "Tool call execution is incomplete or failed; automatic retry is blocked "
                    "to avoid duplicate side effects. Check local data or task state first."
                )
            if row.approval_status not in {"not_required", "approved"}:
                raise ValueError("Tool call has not been approved for execution.")
            row.status = "executing"
            row.updated_at = utc_now_naive()
            session.commit()
            session.refresh(row)
            return self._tool_call_dict(row)

    @staticmethod
    def _tool_call_dict(row: AgentToolCallRow) -> dict[str, Any]:
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
