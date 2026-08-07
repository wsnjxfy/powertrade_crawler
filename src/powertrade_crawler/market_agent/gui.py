from __future__ import annotations

import json
import threading
from datetime import datetime
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    VERTICAL,
    WORD,
    Listbox,
    StringVar,
    Text,
    Toplevel,
    messagebox,
)
from tkinter import ttk
from typing import Any

from powertrade_crawler.market_agent.data_tools import (
    DataOverviewArgs,
    collection_preview,
    data_overview,
)
from powertrade_crawler.market_agent.loop import (
    MarketAgentLoop,
    build_configured_provider,
)
from powertrade_crawler.market_agent.provider import FakeProvider
from powertrade_crawler.market_agent.repository import MarketAgentRepository
from powertrade_crawler.market_agent.schemas import AgentRunResult, RunStatus
from powertrade_crawler.market_agent.tools import ToolContext
from powertrade_crawler.llm_router import (
    DEFAULT_MODEL_STRATEGY,
    FreeRouterManagementClient,
)


GREEN = "#174d3a"
GREEN_DARK = "#103b2d"
GREEN_LIGHT = "#e8f2ee"
MUTED = "#61726b"
EXAMPLE_PROMPT = "查询五个数据源的数据覆盖范围和最新更新时间"

EVENT_LABELS = {
    "run_started": "开始运行",
    "intent_routed": "确定性路由",
    "model_completed": "模型响应",
    "model_timeout": "模型超时",
    "protocol_fallback": "协议回退",
    "tool_started": "开始工具",
    "tool_progress": "采集进度",
    "tool_completed": "工具完成",
    "tool_reused": "复用工具结果",
    "tool_failed": "工具失败",
    "approval_required": "等待审批",
    "approval_granted": "已批准",
    "approval_rejected": "已拒绝",
    "conclusion_generated": "生成结论",
    "failed": "运行失败",
    "stopped": "已停止",
}


class MarketAgentApp:
    """Independent Tk application embedded as a top-level project tab."""

    def __init__(
        self,
        parent,
        repository: MarketAgentRepository | None = None,
    ) -> None:
        self.parent = parent
        self.repository = repository or MarketAgentRepository()
        self.session_id: str | None = None
        self.active_run_id: str | None = None
        self.pending_call: dict[str, Any] | None = None
        self.session_rows: list[dict[str, Any]] = []
        self.busy = False
        self.status_var = StringVar(value="就绪")
        self.source_status_var = StringVar(value="正在读取五类来源状态…")
        self._build_styles()
        self._build_ui()
        self.refresh_sessions(select_first=True)
        self.refresh_source_status()

    def _build_styles(self) -> None:
        style = ttk.Style(self.parent)
        style.configure(
            "MarketAgent.Header.TFrame",
            background=GREEN,
            padding=(14, 10),
        )
        style.configure(
            "MarketAgent.Title.TLabel",
            background=GREEN,
            foreground="white",
            font=("Microsoft YaHei UI", 14, "bold"),
        )
        style.configure(
            "MarketAgent.Subtitle.TLabel",
            background=GREEN,
            foreground="#d7ebe3",
        )
        style.configure(
            "MarketAgent.Primary.TButton",
            padding=(12, 6),
        )
        style.configure(
            "MarketAgent.Card.TLabelframe",
            background="white",
            padding=8,
        )
        style.configure(
            "MarketAgent.Card.TLabelframe.Label",
            foreground=GREEN_DARK,
            font=("Microsoft YaHei UI", 10, "bold"),
        )

    def _build_ui(self) -> None:
        self.parent.columnconfigure(0, weight=1)
        self.parent.rowconfigure(3, weight=1)

        header = ttk.Frame(self.parent, style="MarketAgent.Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        title_box = ttk.Frame(header, style="MarketAgent.Header.TFrame")
        title_box.pack(side=LEFT, fill="x", expand=True)
        ttk.Label(
            title_box,
            text="多数据源电力市场 Agent",
            style="MarketAgent.Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            title_box,
            text="Elecheck · ENTSO-E · Elexon · GridStatus · 广州电力交易中心",
            style="MarketAgent.Subtitle.TLabel",
        ).pack(anchor="w")
        ttk.Button(header, text="模型设置", command=self.open_settings).pack(
            side=RIGHT, padx=(6, 0)
        )
        ttk.Button(header, text="刷新状态", command=self.refresh_source_status).pack(
            side=RIGHT
        )

        status = ttk.Frame(self.parent, padding=(10, 6))
        status.grid(row=1, column=0, sticky="ew")
        ttk.Label(status, textvariable=self.source_status_var, foreground=MUTED).pack(
            side=LEFT
        )
        ttk.Label(status, textvariable=self.status_var, foreground=GREEN_DARK).pack(
            side=RIGHT
        )

        body = ttk.Panedwindow(self.parent, orient="horizontal")
        body.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 8))

        left = ttk.Frame(body, width=230)
        body.add(left, weight=0)
        session_card = ttk.LabelFrame(
            left,
            text="历史会话",
            style="MarketAgent.Card.TLabelframe",
        )
        session_card.pack(fill=BOTH, expand=True)
        session_toolbar = ttk.Frame(session_card)
        session_toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(session_toolbar, text="新建", command=self.new_session).pack(
            side=LEFT
        )
        ttk.Button(session_toolbar, text="刷新", command=self.refresh_sessions).pack(
            side=LEFT, padx=4
        )
        self.session_list = Listbox(
            session_card,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#c6d6cf",
            activestyle="none",
            exportselection=False,
            font=("Microsoft YaHei UI", 10),
        )
        self.session_list.pack(fill=BOTH, expand=True)
        self.session_list.bind("<<ListboxSelect>>", self._on_session_selected)

        right = ttk.Panedwindow(body, orient=VERTICAL)
        body.add(right, weight=1)
        conversation_card = ttk.LabelFrame(
            right,
            text="对话",
            style="MarketAgent.Card.TLabelframe",
        )
        result_card = ttk.LabelFrame(
            right,
            text="结构化结果",
            style="MarketAgent.Card.TLabelframe",
        )
        timeline_card = ttk.LabelFrame(
            right,
            text="阶段时间线",
            style="MarketAgent.Card.TLabelframe",
        )
        right.add(conversation_card, weight=3)
        right.add(result_card, weight=2)
        right.add(timeline_card, weight=1)

        self.conversation = Text(
            conversation_card,
            wrap=WORD,
            state="disabled",
            height=8,
            font=("Microsoft YaHei UI", 10),
            padx=8,
            pady=8,
        )
        self.conversation.pack(fill=BOTH, expand=True)
        self.conversation.tag_configure("user", foreground=GREEN_DARK, spacing1=8)
        self.conversation.tag_configure("assistant", foreground="#26342f", spacing1=8)
        self.conversation.tag_configure("system", foreground=MUTED, spacing1=6)

        self.result_text = Text(
            result_card,
            wrap=WORD,
            state="disabled",
            height=6,
            font=("Microsoft YaHei UI", 10),
            padx=8,
            pady=8,
        )
        self.result_text.pack(fill=BOTH, expand=True)

        self.timeline = ttk.Treeview(
            timeline_card,
            columns=("time", "stage", "detail"),
            show="headings",
            height=4,
        )
        self.timeline.heading("time", text="时间")
        self.timeline.heading("stage", text="阶段")
        self.timeline.heading("detail", text="说明")
        self.timeline.column("time", width=90, stretch=False)
        self.timeline.column("stage", width=110, stretch=False)
        self.timeline.column("detail", width=540)
        self.timeline.pack(fill=BOTH, expand=True)

        input_frame = ttk.LabelFrame(
            self.parent,
            text="提问区",
            style="MarketAgent.Card.TLabelframe",
            padding=(10, 8),
        )
        input_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        input_frame.columnconfigure(0, weight=1)
        ttk.Label(
            input_frame,
            text="在这里输入市场问题或采集指令",
            foreground=GREEN_DARK,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.input_text = Text(
            input_frame,
            height=3,
            wrap=WORD,
            font=("Microsoft YaHei UI", 11),
            padx=8,
            pady=7,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#9ab5aa",
            highlightcolor=GREEN,
            undo=True,
        )
        self.input_text.grid(row=1, column=0, sticky="ew")
        self.input_text.bind("<Control-Return>", self._submit_from_keyboard)
        ttk.Label(
            input_frame,
            text="Enter 换行 · Ctrl+Enter 发送",
            foreground=MUTED,
        ).grid(row=2, column=0, sticky="w", pady=(5, 0))
        buttons = ttk.Frame(input_frame)
        buttons.grid(row=0, column=1, rowspan=3, sticky="ns", padx=(10, 0))
        self.send_button = ttk.Button(
            buttons,
            text="发送请求",
            style="MarketAgent.Primary.TButton",
            command=self.send_message,
            width=14,
        )
        self.send_button.pack(fill="x")
        ttk.Button(
            buttons,
            text="填入示例",
            command=self._fill_example,
            width=14,
        ).pack(fill="x", pady=(4, 0))
        self.stop_button = ttk.Button(
            buttons,
            text="停止",
            command=self.stop_run,
            state="disabled",
        )
        self.stop_button.pack(fill="x", pady=(4, 0))
        self.approve_button = ttk.Button(
            buttons,
            text="批准",
            command=lambda: self.decide_pending(True),
            state="disabled",
        )
        self.approve_button.pack(side=LEFT, pady=(4, 0))
        self.reject_button = ttk.Button(
            buttons,
            text="拒绝",
            command=lambda: self.decide_pending(False),
            state="disabled",
        )
        self.reject_button.pack(side=RIGHT, pady=(4, 0))
        self.parent.after_idle(self.input_text.focus_set)

    def _submit_from_keyboard(self, _event=None) -> str:
        self.send_message()
        return "break"

    def _fill_example(self) -> None:
        self.input_text.delete("1.0", END)
        self.input_text.insert("1.0", EXAMPLE_PROMPT)
        self.input_text.focus_set()

    def refresh_source_status(self) -> None:
        def worker() -> None:
            try:
                payload = data_overview(
                    DataOverviewArgs(),
                    ToolContext(
                        session_id="gui-status",
                        run_id="gui-status",
                        tool_call_id="gui-status",
                    ),
                )
                parts = [
                    f"{item['display_name']}: {item['latest_business_date'] or '暂无'}"
                    for item in payload["sources"]
                ]
                text = "  |  ".join(parts)
            except Exception as exc:
                text = f"读取来源状态失败：{exc}"
            self.parent.after(0, lambda: self.source_status_var.set(text))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_sessions(self, select_first: bool = False) -> None:
        selected_id = self.session_id
        self.session_rows = self.repository.list_sessions()
        self.session_list.delete(0, END)
        for row in self.session_rows:
            updated = row["updated_at"].replace("T", " ")[:16]
            self.session_list.insert(END, f"{row['title']}\n{updated}")
        target = None
        if selected_id:
            target = next(
                (index for index, row in enumerate(self.session_rows) if row["id"] == selected_id),
                None,
            )
        if target is None and (select_first or self.session_rows):
            target = 0 if self.session_rows else None
        if target is not None:
            self.session_list.selection_clear(0, END)
            self.session_list.selection_set(target)
            self.session_list.activate(target)
            self._load_session(self.session_rows[target]["id"])

    def new_session(self) -> None:
        self.session_id = self.repository.create_session()
        self.pending_call = None
        self._clear_views()
        self.refresh_sessions()

    def _on_session_selected(self, _event=None) -> None:
        selection = self.session_list.curselection()
        if not selection:
            return
        self._load_session(self.session_rows[selection[0]]["id"])

    def _load_session(self, session_id: str) -> None:
        self.session_id = session_id
        record = self.repository.get_session_record(session_id)
        self._set_text(self.conversation, "")
        for message in record["messages"]:
            content = message["content"]
            if message["role"] == "user":
                text = (
                    str(content.get("content") or "")
                    if isinstance(content, dict)
                    else str(content)
                )
                self._append_chat("你", text, "user")
            elif message["role"] == "assistant":
                text = (
                    str(content.get("conclusion") or content)
                    if isinstance(content, dict)
                    else str(content)
                )
                self._append_chat("Agent", text, "assistant")
        self._set_text(self.result_text, "")
        self.timeline.delete(*self.timeline.get_children())
        self.pending_call = None
        latest_run_id = record.get("latest_run_id")
        if latest_run_id:
            run = self.repository.get_run(latest_run_id)
            if run.get("answer"):
                self._set_text(self.result_text, self._format_result(run["answer"]))
            for event in self.repository.list_events(latest_run_id):
                self._insert_event(event)
            calls = self.repository.list_tool_calls(latest_run_id)
            pending = next(
                (call for call in calls if call["approval_status"] == "pending"),
                None,
            )
            self.pending_call = pending
        self._update_controls()

    def send_message(self) -> None:
        if self.busy:
            self.status_var.set("当前任务仍在运行，可先点击停止")
            return
        message = self.input_text.get("1.0", END).strip()
        if not message:
            self.status_var.set("请先在提问区输入问题")
            self.input_text.focus_set()
            return
        if not self.session_id:
            self.session_id = self.repository.create_session()
        self.input_text.delete("1.0", END)
        self.input_text.focus_set()
        self._append_chat("你", message, "user")
        self._set_busy(True, "正在分析…")

        def worker() -> None:
            try:
                loop = MarketAgentLoop.from_config(
                    repository=self.repository,
                    event_callback=lambda event: self.parent.after(
                        0, lambda item=event: self._on_live_event(item)
                    ),
                )
                result = loop.chat(message, session_id=self.session_id)
                self.parent.after(0, lambda: self._finish_result(result))
            except Exception as exc:
                self.parent.after(0, lambda error=exc: self._finish_exception(error))

        threading.Thread(target=worker, daemon=True).start()

    def stop_run(self) -> None:
        if self.active_run_id:
            self.repository.request_stop(self.active_run_id)
            self.status_var.set("已请求停止，将在当前步骤结束后停止")

    def decide_pending(self, approved: bool) -> None:
        if self.busy or not self.pending_call:
            return
        call = dict(self.pending_call)
        action = "批准" if approved else "拒绝"
        if not messagebox.askyesno(
            f"确认{action}",
            self._approval_summary(call) + f"\n\n确认{action}？",
            parent=self.parent,
        ):
            return
        self._set_busy(True, f"正在{action}…")

        def worker() -> None:
            try:
                def event_callback(event: dict[str, Any]) -> None:
                    self.parent.after(
                        0,
                        lambda item=event: self._on_live_event(item),
                    )

                loop = (
                    MarketAgentLoop.from_config(
                        repository=self.repository,
                        event_callback=event_callback,
                    )
                    if approved
                    else MarketAgentLoop(
                        FakeProvider([]),
                        repository=self.repository,
                        event_callback=event_callback,
                    )
                )
                result = loop.resume_approval(
                    call["id"],
                    approved=approved,
                    expected_arguments_hash=call["arguments_hash"],
                )
                self.parent.after(0, lambda: self._finish_result(result))
            except Exception as exc:
                self.parent.after(0, lambda error=exc: self._finish_exception(error))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_result(self, result: AgentRunResult) -> None:
        self.active_run_id = result.run_id
        self.pending_call = None
        if result.answer:
            self._append_chat("Agent", result.answer.conclusion, "assistant")
            self._set_text(
                self.result_text,
                self._format_result(result.answer.model_dump(mode="json")),
            )
        elif result.error:
            self._append_chat("系统", result.error.message, "system")
        if result.pending_approval:
            self.pending_call = self.repository.get_tool_call(
                result.pending_approval.tool_call_id
            )
            self._append_chat(
                "系统",
                "操作需要审批：\n" + self._approval_summary(self.pending_call),
                "system",
            )
        self._set_busy(False, self._status_text(result.status))
        self.input_text.focus_set()
        self.refresh_sessions()
        self.refresh_source_status()

    def _finish_exception(self, exc: Exception) -> None:
        self._append_chat("系统", f"运行失败：{exc}", "system")
        self._set_busy(False, "运行失败")
        self.input_text.focus_set()

    def _on_live_event(self, event: dict[str, Any]) -> None:
        self.active_run_id = event.get("run_id") or self.active_run_id
        self._insert_event(event)
        if event.get("stage") == "tool_progress":
            self.status_var.set(self._event_summary(event.get("detail") or {}))

    def _insert_event(self, event: dict[str, Any]) -> None:
        created = str(event.get("created_at") or "")
        time_text = created.replace("T", " ")[11:19] or datetime.now().strftime("%H:%M:%S")
        stage = str(event.get("stage") or "")
        detail = event.get("detail") or {}
        self.timeline.insert(
            "",
            END,
            values=(
                time_text,
                EVENT_LABELS.get(stage, stage),
                self._event_summary(detail),
            ),
        )
        children = self.timeline.get_children()
        if children:
            self.timeline.see(children[-1])

    def _set_busy(self, busy: bool, status: str) -> None:
        self.busy = busy
        self.status_var.set(status)
        self.send_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")
        self._update_controls()

    def _update_controls(self) -> None:
        state = "normal" if self.pending_call and not self.busy else "disabled"
        self.approve_button.configure(state=state)
        self.reject_button.configure(state=state)

    def _clear_views(self) -> None:
        self._set_text(self.conversation, "")
        self._set_text(self.result_text, "")
        self.timeline.delete(*self.timeline.get_children())

    def _append_chat(self, speaker: str, text: str, tag: str) -> None:
        self.conversation.configure(state="normal")
        self.conversation.insert(END, f"{speaker}\n", tag)
        self.conversation.insert(END, f"{text}\n\n", tag)
        self.conversation.configure(state="disabled")
        self.conversation.see(END)

    @staticmethod
    def _set_text(widget: Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def open_settings(self) -> None:
        config = self.repository.get_config()
        window = Toplevel(self.parent)
        window.title("多数据源 Agent 模型设置")
        window.geometry("640x310")
        window.transient(self.parent.winfo_toplevel())
        fields = {
            "endpoint": StringVar(value=config["endpoint"]),
            "model_id": StringVar(value=config["model_id"]),
            "protocol": StringVar(value=config["protocol"]),
        }
        form = ttk.Frame(window, padding=14)
        form.pack(fill=BOTH, expand=True)
        ttk.Label(form, text="Endpoint").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(
            form, textvariable=fields["endpoint"], state="readonly"
        ).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="免费模型策略").grid(
            row=1, column=0, sticky="w", pady=5
        )
        strategy_combo = ttk.Combobox(
            form,
            textvariable=fields["model_id"],
            values=(DEFAULT_MODEL_STRATEGY, fields["model_id"].get()),
            state="readonly",
        )
        strategy_combo.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="工具协议").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Combobox(
            form,
            textvariable=fields["protocol"],
            values=("auto", "native", "json"),
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", pady=5)
        form.columnconfigure(1, weight=1)
        status = StringVar(value="正在读取免费渠道和告警…")
        ttk.Label(form, textvariable=status, foreground=MUTED).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(12, 4),
        )
        ttk.Label(
            form,
            text=(
                "密钥只从项目外 client-free.env 读取；smart-auto 不会调用付费模型。"
            ),
            foreground=MUTED,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 6))
        buttons = ttk.Frame(form)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e")

        def save() -> None:
            try:
                self.repository.set_config(
                    endpoint=fields["endpoint"].get(),
                    model_id=fields["model_id"].get(),
                    model_profile="free",
                    protocol=fields["protocol"].get(),
                )
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=window)
                return
            window.destroy()
            self.status_var.set("模型配置已保存")

        def check_connection() -> None:
            status.set("正在检查连接…")

            def worker() -> None:
                try:
                    provider, active = build_configured_provider(self.repository)
                    try:
                        models = provider.list_models()
                    finally:
                        provider.close()
                    text = (
                        f"连接正常；当前策略 {active['active_model_id']}；"
                        f"可见模型 {len(models)} 个。"
                    )
                except Exception as exc:
                    text = f"连接失败：{exc}"
                window.after(0, lambda: status.set(text))

            threading.Thread(target=worker, daemon=True).start()

        def probe_protocol() -> None:
            status.set("正在探测 Function Calling 协议…")

            def worker() -> None:
                try:
                    provider, _active = build_configured_provider(self.repository)
                    try:
                        response = provider.complete(
                            messages=[
                                {
                                    "role": "user",
                                    "content": "请调用 protocol_probe，参数 value=ok。",
                                }
                            ],
                            tools=[
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "protocol_probe",
                                        "description": "协议探测。",
                                        "parameters": {
                                            "type": "object",
                                            "properties": {
                                                "value": {"type": "string"}
                                            },
                                            "required": ["value"],
                                            "additionalProperties": False,
                                        },
                                    },
                                }
                            ],
                        )
                    finally:
                        provider.close()
                    detected = "native" if response.tool_calls else "json"
                    self.repository.set_config(detected_protocol=detected)
                    text = (
                        f"协议：{detected}；命中渠道："
                        f"{response.router_provider or '未返回'}；上游模型："
                        f"{response.upstream_model or '未返回'}；告警："
                        f"{response.router_alert_count if response.router_alert_count is not None else '未知'}"
                    )
                except Exception as exc:
                    text = f"协议探测失败：{exc}"
                window.after(0, lambda: status.set(text))

            threading.Thread(target=worker, daemon=True).start()

        def refresh_channels() -> None:
            status.set("正在读取免费渠道和告警…")

            def worker() -> None:
                try:
                    with FreeRouterManagementClient.from_external_config() as router:
                        providers = router.list_providers()
                        alerts = router.list_alerts()
                    strategies = [
                        str(row["directModel"])
                        for row in providers
                        if row.get("tier") == "free"
                        and row.get("available") is True
                        and row.get("directModel")
                    ]
                    values = tuple(
                        dict.fromkeys([DEFAULT_MODEL_STRATEGY, *strategies])
                    )
                    text = (
                        f"可用免费渠道 {len(strategies)} 个；"
                        f"当前告警 {len(alerts)} 条。"
                    )
                except Exception as exc:
                    values = (DEFAULT_MODEL_STRATEGY, fields["model_id"].get())
                    text = f"免费池状态读取失败：{exc}"

                def apply_result() -> None:
                    if window.winfo_exists():
                        strategy_combo.configure(values=values)
                        status.set(text)

                window.after(0, apply_result)

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(buttons, text="刷新渠道", command=refresh_channels).pack(
            side=LEFT, padx=4
        )
        ttk.Button(buttons, text="连接检查", command=check_connection).pack(
            side=LEFT, padx=4
        )
        ttk.Button(buttons, text="协议探测", command=probe_protocol).pack(
            side=LEFT, padx=4
        )
        ttk.Button(buttons, text="保存", command=save).pack(side=LEFT, padx=4)
        ttk.Button(buttons, text="取消", command=window.destroy).pack(side=LEFT)
        refresh_channels()

    @staticmethod
    def _approval_summary(call: dict[str, Any]) -> str:
        preview = collection_preview(call["tool_name"], call["arguments"])
        return "\n".join(
            [
                f"工具：{call['tool_name']}",
                f"参数：{json.dumps(call['arguments'], ensure_ascii=False)}",
                f"参数哈希：{call['arguments_hash']}",
                f"来源：{preview['source']}；数据集：{preview['dataset']}",
                (
                    f"范围：{preview.get('start') or '当前快照'}"
                    f" 至 {preview.get('end') or '当前快照'}；"
                    f"预计请求：{preview['estimated_requests']}"
                ),
                "副作用：将采集并写入对应业务数据表。",
            ]
        )

    @staticmethod
    def _format_result(payload: dict[str, Any]) -> str:
        sections = [f"结论\n{payload.get('conclusion', '')}"]
        catalog_notes = []
        for item in payload.get("dataset_catalog_summaries") or []:
            note = str(item.get("scope_note") or "").strip()
            if note:
                catalog_notes.append(f"• {note}")
            hint = str(item.get("lookup_hint") or "").strip()
            if hint:
                catalog_notes.append(f"  查找方式：{hint}")
        if catalog_notes:
            sections.append("数据集口径\n" + "\n".join(catalog_notes))
        dataset_lines = []
        for item in payload.get("datasets") or []:
            title = item.get("title") or item.get("dataset") or "未命名数据集"
            dataset_id = item.get("dataset") or ""
            dataset_lines.append(
                f"• {title}" + (f"（{dataset_id}）" if dataset_id else "")
            )
            details = []
            if item.get("metric"):
                details.append(f"指标：{item['metric']}")
            if item.get("unit"):
                details.append(f"单位：{item['unit']}")
            if item.get("time_basis"):
                details.append(f"时间口径：{item['time_basis']}")
            if details:
                dataset_lines.append("  " + "；".join(details))
            local_count = item.get("local_record_count")
            local_earliest = item.get("local_earliest")
            local_latest = item.get("local_latest")
            if local_count is not None:
                coverage = ""
                if local_earliest or local_latest:
                    coverage = (
                        f"；范围：{local_earliest or '未知'}"
                        f" 至 {local_latest or '未知'}"
                    )
                dataset_lines.append(f"  本地记录：{local_count} 条{coverage}")
        if dataset_lines:
            sections.append("数据集目录\n" + "\n".join(dataset_lines))
        reference_lines = []
        for item in payload.get("reference_items") or []:
            title = item.get("title") or "未命名内容"
            metadata = [
                str(value)
                for value in (item.get("publish_date"), item.get("category"))
                if value
            ]
            reference_lines.append(
                f"• {title}" + (f"（{' · '.join(metadata)}）" if metadata else "")
            )
            if item.get("summary"):
                reference_lines.append(f"  摘要：{item['summary']}")
            if item.get("url"):
                reference_lines.append(f"  链接：{item['url']}")
        if reference_lines:
            sections.append("公开信息 / 参考内容\n" + "\n".join(reference_lines))
        if payload.get("data_sources"):
            sections.append("数据来源\n" + "\n".join(
                f"• {item}" for item in payload["data_sources"]
            ))
        if payload.get("data_range"):
            sections.append("数据范围\n" + "\n".join(
                f"• {item}" for item in payload["data_range"]
            ))
        metrics = []
        for item in payload.get("business_metrics") or []:
            unit = f" {item.get('unit')}" if item.get("unit") else ""
            description = (
                f" · {item['description']}" if item.get("description") else ""
            )
            metrics.append(
                f"• {item.get('name')}：{item.get('value')}{unit}{description}"
            )
        if metrics:
            sections.append("业务指标\n" + "\n".join(metrics))
        comparison = payload.get("comparison_status")
        if comparison and comparison != "none":
            label = "可严格比较" if comparison == "comparable" else "仅并列展示"
            basis = "\n".join(
                f"• {item}" for item in payload.get("comparison_basis") or []
            )
            sections.append(f"比较状态\n{label}" + (f"\n{basis}" if basis else ""))
        if payload.get("warnings"):
            sections.append("警告\n" + "\n".join(
                f"• {item}" for item in payload["warnings"]
            ))
        if payload.get("generated_files"):
            sections.append("生成文件\n" + "\n".join(
                f"• {item}" for item in payload["generated_files"]
            ))
        if payload.get("executed_actions"):
            sections.append("执行记录\n" + "\n".join(
                f"• {item}" for item in payload["executed_actions"]
            ))
        return "\n\n".join(sections)

    @staticmethod
    def _event_summary(detail: dict[str, Any]) -> str:
        parts = []
        for key in (
            "tool_name",
            "source",
            "dataset",
            "category",
            "stage",
            "records_written",
            "message",
            "code",
            "router_provider",
            "upstream_model",
            "router_alert_count",
        ):
            value = detail.get(key)
            if value not in {None, ""}:
                parts.append(f"{key}={value}")
        return "；".join(parts) or json.dumps(detail, ensure_ascii=False, default=str)

    @staticmethod
    def _status_text(status: RunStatus) -> str:
        return {
            RunStatus.COMPLETED: "已完成",
            RunStatus.AWAITING_APPROVAL: "等待审批",
            RunStatus.REJECTED: "已拒绝",
            RunStatus.STOPPED: "已停止",
            RunStatus.FAILED: "运行失败",
            RunStatus.RUNNING: "运行中",
        }[status]

    @staticmethod
    def _event_tag(stage: str) -> str:
        if stage in {
            "tool_completed",
            "tool_reused",
            "conclusion_generated",
            "approval_granted",
        }:
            return "success"
        if stage in {"approval_required", "protocol_fallback"}:
            return "warning"
        if stage in {"failed", "tool_failed", "approval_rejected"}:
            return "error"
        return "info"
