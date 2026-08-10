from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from tkinter import (
    BOTH,
    Button,
    DoubleVar,
    END,
    Frame,
    LEFT,
    RIGHT,
    TOP,
    X,
    StringVar,
    Text,
    Toplevel,
    messagebox,
    simpledialog,
    ttk,
)
from typing import Any

from powertrade_crawler.agent.elecheck_tools import build_elecheck_tool_registry
from powertrade_crawler.agent.loop import AgentLoop, build_configured_provider
from powertrade_crawler.agent.repository import AgentRepository
from powertrade_crawler.agent.schemas import (
    AgentRunResult,
    PendingApproval,
    RiskLevel,
    RunStatus,
)
from powertrade_crawler.llm_router import (
    DEFAULT_MODEL_STRATEGY,
    FreeRouterManagementClient,
)


STAGE_LABELS = {
    "model_request": "请求模型",
    "model_retry": "模型超时重试",
    "model_completed": "模型响应",
    "intent_routed": "识别明确意图",
    "collection_details_required": "补充采集参数",
    "request_declined": "安全边界说明",
    "tool_proposed": "提出工具",
    "approval_required": "等待审批",
    "approval_rejected": "审批拒绝",
    "tool_running": "执行中",
    "tool_progress": "下载进度",
    "tool_completed": "执行完成",
    "tool_failed": "执行失败",
    "tool_reused": "复用已完成调用",
    "conclusion_generated": "生成结论",
    "format_correction": "纠正输出格式",
    "protocol_fallback": "切换工具协议",
    "failed": "失败",
    "stopped": "已停止",
}

AGENT_COLORS = {
    "shell": "#F3F6FA",
    "surface": "#FFFFFF",
    "surface_alt": "#F7F9FC",
    "forest": "#102A43",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "mint": "#E9F0FF",
    "mint_hover": "#DCE7FF",
    "text": "#132238",
    "muted": "#718096",
    "border": "#DCE4EC",
    "danger": "#B64242",
    "danger_hover": "#983535",
    "danger_soft": "#FBEAEA",
    "warning": "#A96500",
    "warning_soft": "#FFF4DF",
    "disabled": "#A5B2C1",
}


class ElecheckAgentApp:
    def __init__(
        self,
        root,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        self.root = root
        self.on_navigate = on_navigate
        self.repository = AgentRepository()
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.session_id: str | None = None
        self.current_run_id: str | None = None
        self.pending_result: AgentRunResult | None = None
        self.update_progress_dialog: Toplevel | None = None
        self.update_progress_tool_call_id: str | None = None
        self.update_progress_stopped_early = False
        self.update_progress_message_var = StringVar(value="准备更新现货数据")
        self.update_progress_detail_var = StringVar(value="")
        self.update_progress_value_var = DoubleVar(value=0.0)
        self._compact_actions: bool | None = None
        self._configure_styles()
        self.build_ui()
        self._render_conversation([])
        self._set_text(self.result_text, "当前会话暂无结构化结果。")
        self.refresh_sessions()
        self.root.after(100, self.poll_queue)

    def _configure_styles(self) -> None:
        colors = AGENT_COLORS
        self.style = ttk.Style(self.root)
        self.style.configure("AgentShell.TFrame", background=colors["shell"])
        self.style.configure("AgentHeader.TFrame", background=colors["forest"])
        self.style.configure("AgentCard.TFrame", background=colors["surface"])
        self.style.configure("AgentSoft.TFrame", background=colors["surface_alt"])
        self.style.configure(
            "AgentTitle.TLabel",
            background=colors["forest"],
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 15, "bold"),
        )
        self.style.configure(
            "AgentSubtitle.TLabel",
            background=colors["forest"],
            foreground="#C8DDD5",
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "AgentSection.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.configure(
            "AgentMuted.TLabel",
            background=colors["surface"],
            foreground=colors["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "AgentCard.TLabelframe",
            background=colors["surface"],
            bordercolor=colors["border"],
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "AgentCard.TLabelframe.Label",
            background=colors["surface"],
            foreground=colors["forest"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.configure(
            "Agent.Treeview",
            background=colors["surface"],
            fieldbackground=colors["surface"],
            foreground=colors["text"],
            borderwidth=0,
            rowheight=31,
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "Agent.Treeview.Heading",
            background=colors["mint"],
            foreground=colors["forest"],
            relief="flat",
            padding=(6, 7),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.style.map(
            "Agent.Treeview",
            background=[("selected", colors["primary"])],
            foreground=[("selected", "#FFFFFF")],
        )
        self.style.configure(
            "Agent.Horizontal.TPanedwindow",
            background=colors["shell"],
            sashwidth=7,
        )
        self.style.configure(
            "Agent.Vertical.TPanedwindow",
            background=colors["shell"],
            sashwidth=7,
        )

    def build_ui(self) -> None:
        colors = AGENT_COLORS
        self.root.configure(style="AgentShell.TFrame")
        header = ttk.Frame(
            self.root,
            style="AgentHeader.TFrame",
            padding=(18, 12),
        )
        header.pack(fill=X, side=TOP)
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Elecheck 智能分析 Agent",
            style="AgentTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="自然语言查询 · 安全工具调用 · 可审批业务操作",
            style="AgentSubtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        header_actions = Frame(header, bg=colors["forest"])
        header_actions.grid(row=0, column=1, sticky="e")
        self._make_button(
            header_actions,
            "新建会话",
            self.new_session,
            role="header",
            compact=True,
        ).pack(side=LEFT)
        self._make_button(
            header_actions,
            "删除会话",
            self.delete_session,
            role="header_danger",
            compact=True,
        ).pack(side=LEFT, padx=(6, 0))
        self._make_button(
            header_actions,
            "模型设置",
            self.open_settings,
            role="header",
            compact=True,
        ).pack(side=LEFT, padx=(6, 0))
        self._make_button(
            header_actions,
            "刷新",
            self.refresh_sessions,
            role="header",
            compact=True,
        ).pack(side=LEFT, padx=(6, 0))
        if self.on_navigate is not None:
            self._make_button(
                header_actions,
                "API 配置",
                lambda: self.on_navigate("setup"),
                role="header",
                compact=True,
            ).pack(side=LEFT, padx=(6, 0))
            self._make_button(
                header_actions,
                "定时任务",
                lambda: self.on_navigate("schedule"),
                role="header",
                compact=True,
            ).pack(side=LEFT, padx=(6, 0))
        panes = ttk.Panedwindow(
            self.root,
            orient="horizontal",
            style="Agent.Horizontal.TPanedwindow",
        )
        panes.pack(fill=BOTH, expand=True, padx=12, pady=12)
        left = ttk.Frame(panes, width=238, style="AgentCard.TFrame", padding=12)
        right = ttk.Frame(panes, style="AgentShell.TFrame")
        panes.add(left, weight=1)
        panes.add(right, weight=4)
        self.main_panes = panes

        ttk.Label(left, text="会话历史", style="AgentSection.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            left,
            text="会话与运行轨迹仅保存在本地",
            style="AgentMuted.TLabel",
        ).pack(anchor="w", pady=(2, 9))
        self.sessions_tree = ttk.Treeview(
            left,
            columns=("title", "updated"),
            show="headings",
            height=8,
            style="Agent.Treeview",
        )
        self.sessions_tree.heading("title", text="会话")
        self.sessions_tree.heading("updated", text="更新时间")
        self.sessions_tree.column("title", width=137, minwidth=90)
        self.sessions_tree.column("updated", width=92, minwidth=78)
        session_scroll = ttk.Scrollbar(
            left,
            orient="vertical",
            command=self.sessions_tree.yview,
        )
        self.sessions_tree.configure(yscrollcommand=session_scroll.set)
        session_scroll.pack(side=RIGHT, fill="y")
        self.sessions_tree.pack(side=LEFT, fill=BOTH, expand=True)
        self.sessions_tree.bind("<<TreeviewSelect>>", self.on_session_selected)

        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        vertical = ttk.Frame(
            right,
            style="AgentShell.TFrame",
        )
        vertical.grid(row=1, column=0, sticky="nsew")
        vertical.columnconfigure(0, weight=1)
        vertical.rowconfigure(0, weight=3)
        vertical.rowconfigure(1, weight=2)

        conversation_frame = ttk.LabelFrame(
            vertical,
            text="对话",
            style="AgentCard.TLabelframe",
            padding=8,
        )
        self.conversation_text = Text(
            conversation_frame,
            wrap="word",
            height=4,
            state="disabled",
            bg=colors["surface"],
            fg=colors["text"],
            insertbackground=colors["primary"],
            selectbackground=colors["mint"],
            selectforeground=colors["text"],
            font=("Microsoft YaHei UI", 10),
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
        )
        conversation_scroll = ttk.Scrollbar(
            conversation_frame,
            orient="vertical",
            command=self.conversation_text.yview,
        )
        self.conversation_text.configure(yscrollcommand=conversation_scroll.set)
        conversation_scroll.pack(side=RIGHT, fill="y")
        self.conversation_text.pack(side=LEFT, fill=BOTH, expand=True)
        self._configure_conversation_tags()
        conversation_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 5))

        detail_panes = ttk.Panedwindow(
            vertical,
            orient="horizontal",
            style="Agent.Horizontal.TPanedwindow",
        )
        event_frame = ttk.LabelFrame(
            detail_panes,
            text="执行轨迹",
            style="AgentCard.TLabelframe",
            padding=8,
        )
        self.event_tree = ttk.Treeview(
            event_frame,
            columns=("time", "stage", "detail"),
            show="headings",
            height=3,
            style="Agent.Treeview",
        )
        self.event_tree.heading("time", text="时间")
        self.event_tree.heading("stage", text="阶段")
        self.event_tree.heading("detail", text="摘要")
        self.event_tree.column("time", width=72, minwidth=62)
        self.event_tree.column("stage", width=96, minwidth=82)
        self.event_tree.column("detail", width=255, minwidth=140)
        event_scroll = ttk.Scrollbar(
            event_frame,
            orient="vertical",
            command=self.event_tree.yview,
        )
        self.event_tree.configure(yscrollcommand=event_scroll.set)
        event_scroll.pack(side=RIGHT, fill="y")
        self.event_tree.pack(side=LEFT, fill=BOTH, expand=True)
        self.event_tree.tag_configure("success", foreground=colors["primary"])
        self.event_tree.tag_configure("active", foreground="#256C91")
        self.event_tree.tag_configure("warning", foreground=colors["warning"])
        self.event_tree.tag_configure("error", foreground=colors["danger"])
        result_frame = ttk.LabelFrame(
            detail_panes,
            text="结构化结果",
            style="AgentCard.TLabelframe",
            padding=8,
        )
        self.result_text = Text(
            result_frame,
            wrap="word",
            height=3,
            state="disabled",
            bg=colors["surface_alt"],
            fg=colors["text"],
            selectbackground=colors["mint"],
            selectforeground=colors["text"],
            font=("Microsoft YaHei UI", 9),
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
        )
        result_scroll = ttk.Scrollbar(
            result_frame,
            orient="vertical",
            command=self.result_text.yview,
        )
        self.result_text.configure(yscrollcommand=result_scroll.set)
        result_scroll.pack(side=RIGHT, fill="y")
        self.result_text.pack(side=LEFT, fill=BOTH, expand=True)
        detail_panes.add(event_frame, weight=1)
        detail_panes.add(result_frame, weight=1)
        detail_panes.grid(row=1, column=0, sticky="nsew", pady=(5, 0))

        composer = ttk.Frame(
            right,
            style="AgentCard.TFrame",
            padding=(10, 8),
        )
        composer.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        composer.columnconfigure(0, weight=1)
        self.input_text = Text(
            composer,
            wrap="word",
            height=2,
            bg=colors["surface_alt"],
            fg=colors["text"],
            insertbackground=colors["primary"],
            selectbackground=colors["mint"],
            selectforeground=colors["text"],
            font=("Microsoft YaHei UI", 9),
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"],
            highlightcolor=colors["primary"],
            padx=8,
            pady=5,
        )
        self.input_text.grid(row=0, column=0, sticky="ew")
        self.input_text.bind("<Control-Return>", self._send_shortcut)
        self.input_text.bind("<Control-KP_Enter>", self._send_shortcut)

        actions = Frame(composer, bg=colors["surface"])
        actions.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.action_frame = actions
        self.send_button = self._make_button(
            actions,
            text="发送",
            command=self.send_message,
            role="primary",
        )
        self.stop_button = self._make_button(
            actions,
            text="停止",
            command=self.stop_run,
            role="warning",
            state="disabled",
        )
        self.reject_button = self._make_button(
            actions,
            text="拒绝操作",
            command=lambda: self.decide_pending(False),
            role="danger_soft",
            state="disabled",
        )
        self.approve_button = self._make_button(
            actions,
            text="批准操作",
            command=lambda: self.decide_pending(True),
            role="approve",
            state="disabled",
        )
        self._layout_action_buttons(compact=False)
        right.bind("<Configure>", self._on_right_resize, add="+")
        self.root.after(120, lambda: self._set_initial_sashes(detail_panes))

    def _make_button(
        self,
        parent,
        text: str,
        command,
        *,
        role: str,
        state: str = "normal",
        compact: bool = False,
    ) -> Button:
        colors = AGENT_COLORS
        palette = {
            "primary": (
                colors["primary"],
                "#FFFFFF",
                colors["primary_hover"],
                "#FFFFFF",
            ),
            "approve": (
                colors["forest"],
                "#FFFFFF",
                colors["primary_hover"],
                "#FFFFFF",
            ),
            "warning": (
                colors["warning_soft"],
                colors["warning"],
                "#F2E1C3",
                colors["warning"],
            ),
            "danger_soft": (
                colors["danger_soft"],
                colors["danger"],
                "#F0D7D4",
                colors["danger"],
            ),
            "secondary": (
                colors["mint"],
                colors["forest"],
                colors["mint_hover"],
                colors["forest"],
            ),
            "header": (
                "#183B59",
                "#F2F7FC",
                "#214B6C",
                "#FFFFFF",
            ),
            "header_danger": (
                "#6D3841",
                "#FFEFF1",
                "#864650",
                "#FFFFFF",
            ),
        }
        background, foreground, active_background, active_foreground = palette[role]
        return Button(
            parent,
            text=text,
            command=command,
            state=state,
            bg=background,
            fg=foreground,
            activebackground=active_background,
            activeforeground=active_foreground,
            disabledforeground=colors["disabled"],
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8 if compact else 9, "bold"),
            padx=8 if compact else 12,
            pady=3 if compact else 5,
            highlightthickness=0,
        )

    def _configure_conversation_tags(self) -> None:
        self.conversation_text.tag_configure(
            "user_name",
            foreground=AGENT_COLORS["primary"],
            font=("Microsoft YaHei UI", 9, "bold"),
            spacing1=8,
        )
        self.conversation_text.tag_configure(
            "agent_name",
            foreground=AGENT_COLORS["forest"],
            font=("Microsoft YaHei UI", 9, "bold"),
            spacing1=8,
        )
        self.conversation_text.tag_configure(
            "message_body",
            foreground=AGENT_COLORS["text"],
            font=("Microsoft YaHei UI", 10),
            lmargin1=8,
            lmargin2=8,
            spacing1=5,
            spacing3=9,
        )
        self.conversation_text.tag_configure(
            "divider",
            foreground=AGENT_COLORS["border"],
            spacing3=2,
        )
        self.conversation_text.tag_configure(
            "empty",
            foreground=AGENT_COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
            justify="center",
            spacing1=10,
        )

    def _set_initial_sashes(self, detail_panes) -> None:
        try:
            detail_panes.sashpos(0, int(detail_panes.winfo_width() * 0.5))
            self.main_panes.sashpos(0, int(self.main_panes.winfo_width() * 0.25))
        except Exception:
            return

    def _on_right_resize(self, event) -> None:
        self._layout_action_buttons(compact=event.width < 620)

    def _layout_action_buttons(self, *, compact: bool) -> None:
        if self._compact_actions == compact:
            return
        self._compact_actions = compact
        buttons = (
            self.send_button,
            self.stop_button,
            self.reject_button,
            self.approve_button,
        )
        for button in buttons:
            button.grid_forget()
        for column in range(4):
            self.action_frame.columnconfigure(column, weight=0)
        if compact:
            self.action_frame.columnconfigure(0, weight=1)
            self.action_frame.columnconfigure(1, weight=1)
            self.send_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
            self.stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
            self.reject_button.grid(
                row=1,
                column=0,
                sticky="ew",
                padx=(0, 4),
                pady=(8, 0),
            )
            self.approve_button.grid(
                row=1,
                column=1,
                sticky="ew",
                padx=(4, 0),
                pady=(8, 0),
            )
            return
        for column in range(4):
            self.action_frame.columnconfigure(column, weight=1)
        self.send_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=4)
        self.reject_button.grid(row=0, column=2, sticky="ew", padx=4)
        self.approve_button.grid(row=0, column=3, sticky="ew", padx=(4, 0))

    def _send_shortcut(self, _event=None) -> str:
        self.send_message()
        return "break"

    def new_session(self) -> None:
        self.session_id = self.repository.create_session()
        self.pending_result = None
        self.refresh_sessions(select_id=self.session_id)
        self.load_session(self.session_id)

    def delete_session(self) -> None:
        if not self.session_id:
            return
        if not messagebox.askyesno(
            "删除会话",
            "确认删除当前本地会话、消息、运行轨迹和待审批记录？",
            parent=self.root,
        ):
            return
        try:
            self.repository.delete_session(self.session_id)
        except ValueError as exc:
            messagebox.showerror("删除失败", str(exc), parent=self.root)
            return
        self.session_id = None
        self.pending_result = None
        self.refresh_sessions()
        self._set_text(self.conversation_text, "")
        self._set_text(self.result_text, "")

    def refresh_sessions(self, select_id: str | None = None) -> None:
        for item in self.sessions_tree.get_children():
            self.sessions_tree.delete(item)
        rows = self.repository.list_sessions()
        for row in rows:
            self.sessions_tree.insert(
                "",
                END,
                iid=row["id"],
                values=(row["title"], row["updated_at"][:19]),
            )
        target = select_id or self.session_id
        if target and self.sessions_tree.exists(target):
            self.sessions_tree.selection_set(target)
            self.sessions_tree.focus(target)
        elif rows:
            first_id = rows[0]["id"]
            self.sessions_tree.selection_set(first_id)
            self.sessions_tree.focus(first_id)
            self.load_session(first_id)

    def on_session_selected(self, _event=None) -> None:
        selected = self.sessions_tree.selection()
        if not selected:
            return
        self.load_session(selected[0])

    def load_session(self, session_id: str) -> None:
        self.session_id = session_id
        try:
            record = self.repository.get_session_record(session_id)
        except ValueError:
            return
        self._render_conversation(record["messages"])
        pending_rows = [
            row
            for row in self.repository.list_pending_approvals()
            if row["session_id"] == session_id
        ]
        if pending_rows:
            row = pending_rows[0]
            tool = build_elecheck_tool_registry().get(row["tool_name"])
            pending = PendingApproval(
                tool_call_id=row["id"],
                run_id=row["run_id"],
                tool_name=row["tool_name"],
                risk_level=RiskLevel(row["risk_level"]),
                arguments=row["arguments"],
                arguments_hash=row["arguments_hash"],
                side_effect=tool.side_effect,
            )
            self.pending_result = AgentRunResult(
                ok=False,
                status=RunStatus.AWAITING_APPROVAL,
                session_id=session_id,
                run_id=row["run_id"],
                pending_approval=pending,
                events=self.repository.list_events(row["run_id"]),
            )
            self.current_run_id = row["run_id"]
            self.approve_button.configure(state="normal")
            self.reject_button.configure(state="normal")
            self._set_text(
                self.result_text,
                self._format_result(pending.model_dump(mode="json")),
            )
        else:
            self.pending_result = None
            self.approve_button.configure(state="disabled")
            self.reject_button.configure(state="disabled")

    def send_message(self) -> None:
        prompt = self.input_text.get("1.0", END).strip()
        if not prompt:
            return
        if not self.session_id:
            self.session_id = self.repository.create_session()
        self.input_text.delete("1.0", END)
        self.pending_result = None
        self.current_run_id = None
        self._set_busy(True, "Agent 正在运行")
        self._clear_events()
        thread = threading.Thread(
            target=self._chat_worker,
            args=(prompt, self.session_id),
            daemon=True,
        )
        thread.start()

    def _chat_worker(self, prompt: str, session_id: str) -> None:
        try:
            loop = AgentLoop.from_config(event_callback=self._queue_event)
            result = loop.chat(prompt, session_id=session_id)
            self.queue.put(("result", result))
        except Exception as exc:
            self.queue.put(("error", str(exc)))

    def _queue_event(self, event: dict[str, Any]) -> None:
        self.queue.put(("event", event))

    def stop_run(self) -> None:
        if self.current_run_id:
            try:
                self.repository.request_stop(self.current_run_id)
            except ValueError as exc:
                messagebox.showerror("停止失败", str(exc), parent=self.root)

    def decide_pending(self, approved: bool) -> None:
        pending = self.pending_result.pending_approval if self.pending_result else None
        if pending is None:
            return
        tool = build_elecheck_tool_registry().get(pending.tool_name)
        summary = (
            f"工具：{tool.name}\n"
            f"风险：{tool.risk_level.value}\n"
            f"参数：\n{json.dumps(pending.arguments, ensure_ascii=False, indent=2)}\n\n"
            f"副作用：{tool.side_effect}"
        )
        if approved and not messagebox.askyesno(
            "批准 Agent 操作",
            summary + "\n\n确认执行？",
            parent=self.root,
        ):
            return
        if not approved and not messagebox.askyesno(
            "拒绝 Agent 操作",
            summary + "\n\n确认拒绝且不执行？",
            parent=self.root,
        ):
            return
        if approved and tool.risk_level == RiskLevel.STRONG_APPROVAL:
            value = simpledialog.askstring(
                "强化审批",
                f"此操作会修改 Windows 任务计划。\n请输入工具名进行二次确认：\n{tool.name}",
                parent=self.root,
            )
            if value != tool.name:
                messagebox.showerror("未批准", "二次确认文本不匹配。", parent=self.root)
                return
        self._set_busy(True, "正在恢复审批运行")
        thread = threading.Thread(
            target=self._approval_worker,
            args=(pending.tool_call_id, approved, pending.arguments_hash),
            daemon=True,
        )
        thread.start()

    def _approval_worker(
        self,
        tool_call_id: str,
        approved: bool,
        expected_hash: str,
    ) -> None:
        try:
            self.repository.decide_approval(
                tool_call_id,
                approved=approved,
                expected_arguments_hash=expected_hash,
            )
            loop = AgentLoop.from_config(
                repository=self.repository,
                event_callback=self._queue_event,
            )
            result = loop.resume(tool_call_id)
            self.queue.put(("result", result))
        except Exception as exc:
            self.queue.put(("error", str(exc)))

    def poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "event":
                    self._handle_event(payload)
                elif kind == "result":
                    self._handle_result(payload)
                elif kind == "error":
                    self._set_busy(False, "运行失败")
                    messagebox.showerror("Agent 错误", str(payload), parent=self.root)
                elif kind == "connection":
                    ok, message = payload
                    if ok:
                        messagebox.showinfo("连接测试", message, parent=self.root)
                    else:
                        messagebox.showerror("连接测试", message, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_queue)

    def _handle_event(self, event: dict[str, Any]) -> None:
        self.current_run_id = event.get("run_id") or self.current_run_id
        stage = event["stage"]
        detail = event.get("detail") or {}
        if (
            detail.get("tool_name") == "elecheck_update_all_spot"
            and stage == "tool_running"
        ):
            self._show_update_progress_dialog(detail)
        elif (
            detail.get("tool_name") == "elecheck_update_all_spot"
            and stage == "tool_progress"
        ):
            self._update_download_progress(detail)
        elif (
            detail.get("tool_name") == "elecheck_update_all_spot"
            and stage in {"tool_completed", "tool_failed"}
        ):
            self._finish_update_progress(stage == "tool_completed")
        summary = self._event_summary(detail)
        event_tag = self._event_tag(stage)
        self.event_tree.insert(
            "",
            END,
            values=(
                str(event.get("created_at") or "")[11:19],
                STAGE_LABELS.get(stage, stage),
                summary,
            ),
            tags=(event_tag,),
        )
        self.event_tree.yview_moveto(1.0)

    def _show_update_progress_dialog(self, detail: dict[str, Any]) -> None:
        tool_call_id = str(detail.get("tool_call_id") or "")
        if (
            self.update_progress_dialog is not None
            and self.update_progress_dialog.winfo_exists()
        ):
            if self.update_progress_tool_call_id != tool_call_id:
                self.update_progress_tool_call_id = tool_call_id
                self.update_progress_stopped_early = False
                self.update_progress_value_var.set(0.0)
                self.update_progress_message_var.set("准备更新全部地区现货数据")
                self.update_progress_detail_var.set("")
                self.update_progress_dialog.deiconify()
            self.update_progress_dialog.lift()
            return

        dialog = Toplevel(self.root)
        dialog.title("Agent 正在更新 Elecheck 现货数据")
        dialog.geometry("560x245")
        dialog.resizable(False, False)
        dialog.transient(self.root.winfo_toplevel())
        dialog.protocol("WM_DELETE_WINDOW", dialog.withdraw)
        self.update_progress_dialog = dialog
        self.update_progress_tool_call_id = tool_call_id
        self.update_progress_stopped_early = False
        self.update_progress_value_var.set(0.0)
        self.update_progress_message_var.set("准备更新全部地区现货数据")
        self.update_progress_detail_var.set("")

        ttk.Label(
            dialog,
            textvariable=self.update_progress_message_var,
            wraplength=510,
        ).pack(fill=X, padx=20, pady=(22, 8))
        ttk.Label(
            dialog,
            textvariable=self.update_progress_detail_var,
            wraplength=510,
        ).pack(fill=X, padx=20, pady=(0, 10))
        ttk.Progressbar(
            dialog,
            mode="determinate",
            maximum=100,
            variable=self.update_progress_value_var,
        ).pack(fill=X, padx=20, pady=(0, 18))

        button_bar = ttk.Frame(dialog)
        button_bar.pack(fill=X, padx=20, pady=(0, 16))
        ttk.Button(
            button_bar,
            text="结束并保存",
            command=self.stop_run,
        ).pack(side=LEFT)
        ttk.Button(
            button_bar,
            text="隐藏",
            command=dialog.withdraw,
        ).pack(side=RIGHT)
        ttk.Label(
            button_bar,
            text="关闭或隐藏窗口不会中断下载。",
        ).pack(side=RIGHT, padx=(0, 12))

    def _update_download_progress(self, detail: dict[str, Any]) -> None:
        tool_call_id = str(detail.get("tool_call_id") or "")
        if self.update_progress_tool_call_id != tool_call_id:
            self._show_update_progress_dialog(detail)
        message = str(detail.get("message") or "正在更新现货数据")
        completed = int(detail.get("completed_requests") or 0)
        total = int(detail.get("total_requests") or 0)
        area_index = int(
            detail.get("area_index")
            or detail.get("completed_area_count")
            or 0
        )
        area_count = int(detail.get("area_count") or 0)
        records_produced = int(detail.get("records_produced") or 0)
        records_upserted = int(detail.get("records_upserted") or 0)
        percent = float(
            detail.get("percent")
            if detail.get("percent") is not None
            else (completed / total * 100 if total else 0)
        )
        self.update_progress_message_var.set(message)
        if detail.get("phase") == "stopped":
            self.update_progress_stopped_early = True
        self.update_progress_detail_var.set(
            f"地区 {area_index}/{area_count} | 请求 {completed}/{total} | "
            f"已抓取 {records_produced} 条 | 已写入 {records_upserted} 条"
        )
        self.update_progress_value_var.set(max(0.0, min(percent, 100.0)))

    def _finish_update_progress(self, succeeded: bool) -> None:
        if (
            self.update_progress_dialog is None
            or not self.update_progress_dialog.winfo_exists()
        ):
            return
        if succeeded and not self.update_progress_stopped_early:
            self.update_progress_message_var.set("全部地区现货数据下载完成")
            self.update_progress_value_var.set(100.0)
        elif succeeded:
            self.update_progress_message_var.set("已结束并保存已下载数据")
        else:
            self.update_progress_message_var.set("现货数据更新失败")
        tool_call_id = self.update_progress_tool_call_id
        self.root.after(
            1500,
            lambda: self._close_update_progress_dialog(tool_call_id),
        )

    def _close_update_progress_dialog(self, tool_call_id: str | None) -> None:
        if self.update_progress_tool_call_id != tool_call_id:
            return
        if (
            self.update_progress_dialog is not None
            and self.update_progress_dialog.winfo_exists()
        ):
            self.update_progress_dialog.destroy()
        self.update_progress_dialog = None
        self.update_progress_tool_call_id = None
        self.update_progress_stopped_early = False

    def _handle_result(self, result: AgentRunResult) -> None:
        self.pending_result = result if result.pending_approval else None
        self.current_run_id = result.run_id
        self._set_busy(False, f"状态：{result.status.value}")
        self.approve_button.configure(
            state="normal" if result.pending_approval else "disabled"
        )
        self.reject_button.configure(
            state="normal" if result.pending_approval else "disabled"
        )
        payload = (
            result.answer.model_dump(mode="json")
            if result.answer
            else (
                result.pending_approval.model_dump(mode="json")
                if result.pending_approval
                else result.error.model_dump(mode="json")
                if result.error
                else {}
            )
        )
        self._set_text(
            self.result_text,
            self._format_result(payload),
        )
        self.refresh_sessions(select_id=result.session_id)
        self.load_session(result.session_id)

    def open_settings(self) -> None:
        dialog = AgentSettingsDialog(self.root, self.repository, self.queue)
        self.root.wait_window(dialog.dialog)

    def _set_busy(self, busy: bool, _status: str) -> None:
        self.send_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")
        if busy:
            self.approve_button.configure(state="disabled")
            self.reject_button.configure(state="disabled")

    def _clear_events(self) -> None:
        for item in self.event_tree.get_children():
            self.event_tree.delete(item)

    def _render_conversation(self, messages: list[dict[str, Any]]) -> None:
        widget = self.conversation_text
        widget.configure(state="normal")
        widget.delete("1.0", END)
        if not messages:
            widget.insert(
                END,
                "你可以查询价格、导出数据，或让 Agent 发起需要审批的采集任务。",
                "empty",
            )
        for index, row in enumerate(messages):
            role = row["role"]
            label = "你" if role == "user" else "Agent"
            timestamp = str(row.get("created_at") or "")[11:19]
            widget.insert(
                END,
                f"{label}  {timestamp}\n",
                "user_name" if role == "user" else "agent_name",
            )
            widget.insert(END, f"{self._message_text(row['content'])}\n", "message_body")
            if index < len(messages) - 1:
                widget.insert(END, "─" * 52 + "\n", "divider")
        widget.configure(state="disabled")
        widget.see(END)

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, dict) and set(content) == {"content"}:
            return str(content["content"])
        if isinstance(content, dict) and "conclusion" in content:
            return str(content["conclusion"])
        return json.dumps(content, ensure_ascii=False, indent=2)

    @staticmethod
    def _format_result(payload: dict[str, Any]) -> str:
        if not payload:
            return "当前会话暂无结构化结果。"
        if "conclusion" in payload:
            sections = [f"结论\n{payload['conclusion']}"]
            metrics = payload.get("business_metrics") or []
            if metrics:
                lines = []
                for metric in metrics:
                    unit = f" {metric.get('unit')}" if metric.get("unit") else ""
                    description = (
                        f" · {metric.get('description')}"
                        if metric.get("description")
                        else ""
                    )
                    lines.append(
                        f"• {metric.get('name')}：{metric.get('value')}{unit}{description}"
                    )
                sections.append("业务指标\n" + "\n".join(lines))
            completeness = payload.get("completeness") or []
            if completeness:
                lines = []
                for item in completeness:
                    ratio = item.get("ratio")
                    ratio_text = f"{ratio:.1%}" if isinstance(ratio, (float, int)) else "—"
                    actual = item.get("actual")
                    expected = item.get("expected")
                    count_text = (
                        f"{actual}/{expected}"
                        if actual is not None and expected is not None
                        else "—"
                    )
                    lines.append(f"• {item.get('name')}：{count_text}（{ratio_text}）")
                sections.append("完整度\n" + "\n".join(lines))
            for title, key in (
                ("数据范围", "data_range"),
                ("警告", "warnings"),
                ("生成文件", "generated_files"),
                ("已执行操作", "executed_actions"),
                ("数据来源", "data_sources"),
            ):
                values = payload.get(key) or []
                if values:
                    sections.append(f"{title}\n" + "\n".join(f"• {value}" for value in values))
            return "\n\n".join(sections)
        if "tool_name" in payload:
            arguments = json.dumps(
                payload.get("arguments") or {},
                ensure_ascii=False,
                indent=2,
            )
            return (
                "等待审批\n"
                f"工具：{payload.get('tool_name')}\n"
                f"风险等级：{payload.get('risk_level')}\n\n"
                f"参数\n{arguments}\n\n"
                f"副作用\n{payload.get('side_effect') or '—'}"
            )
        if "code" in payload:
            return (
                "运行失败\n"
                f"错误代码：{payload.get('code')}\n"
                f"原因：{payload.get('message') or '未知错误'}"
            )
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _event_tag(stage: str) -> str:
        if stage in {"tool_completed", "tool_reused", "conclusion_generated"}:
            return "success"
        if stage in {"approval_required", "model_retry", "format_correction"}:
            return "warning"
        if stage in {"failed", "tool_failed", "stopped", "approval_rejected"}:
            return "error"
        return "active"

    @staticmethod
    def _event_summary(detail: dict[str, Any]) -> str:
        if "message" in detail:
            return str(detail["message"])
        if "tool_name" in detail:
            return str(detail["tool_name"])
        if "code" in detail:
            return f"{detail['code']}: {detail.get('message', '')}"
        if "call_number" in detail:
            return f"第 {detail['call_number']} 次"
        if "router_provider" in detail:
            return (
                f"渠道={detail.get('router_provider') or '未返回'}；"
                f"模型={detail.get('upstream_model') or '未返回'}；"
                f"告警={detail.get('router_alert_count', '未知')}"
            )
        return json.dumps(detail, ensure_ascii=False)[:200]

    @staticmethod
    def _set_text(widget: Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")


class AgentSettingsDialog:
    def __init__(
        self,
        root,
        repository: AgentRepository,
        result_queue: queue.Queue,
    ) -> None:
        self.root = root
        self.repository = repository
        self.result_queue = result_queue
        config = repository.get_config()
        self.dialog = Toplevel(root)
        self.dialog.title("Elecheck Agent 模型设置")
        self.dialog.transient(root)
        self.dialog.grab_set()
        self.endpoint_var = StringVar(value=config["endpoint"])
        self.model_var = StringVar(value=config["model_id"])
        self.protocol_var = StringVar(value=config["protocol"])
        self.status_var = StringVar(value="正在读取免费渠道和告警…")
        self._build()
        self.refresh_channels()

    def _build(self) -> None:
        frame = ttk.Frame(self.dialog, padding=12)
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text="Endpoint").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(
            frame,
            textvariable=self.endpoint_var,
            state="readonly",
            width=58,
        ).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="免费模型策略").grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.model_combo = ttk.Combobox(
            frame,
            textvariable=self.model_var,
            values=(DEFAULT_MODEL_STRATEGY, self.model_var.get()),
            state="readonly",
        )
        self.model_combo.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="工具协议").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(
            frame,
            textvariable=self.protocol_var,
            values=("auto", "native", "json"),
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", pady=4)

        note = (
            "密钥运行时只从项目外 client-free.env 读取，不在此显示或保存。\n"
            "smart-auto 只使用免费渠道；固定渠道仅允许 tier=free 且 available=true。"
        )
        ttk.Label(frame, text=note, foreground="#555555").grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 4),
        )
        ttk.Label(frame, textvariable=self.status_var, foreground="#555555").grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(2, 4),
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="刷新渠道", command=self.refresh_channels).pack(
            side=LEFT, padx=4
        )
        ttk.Button(buttons, text="保存", command=self.save).pack(side=LEFT)
        ttk.Button(buttons, text="保存并测试", command=self.save_and_test).pack(
            side=LEFT, padx=4
        )
        ttk.Button(buttons, text="关闭", command=self.dialog.destroy).pack(side=LEFT)
        frame.columnconfigure(1, weight=1)

    def refresh_channels(self) -> None:
        self.status_var.set("正在读取免费渠道和告警…")

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
                values = tuple(dict.fromkeys([DEFAULT_MODEL_STRATEGY, *strategies]))
                status = f"可用免费渠道 {len(strategies)} 个；当前告警 {len(alerts)} 条。"
            except Exception as exc:
                values = (DEFAULT_MODEL_STRATEGY, self.model_var.get())
                status = f"免费池状态读取失败：{exc}"

            def apply_result() -> None:
                if self.dialog.winfo_exists():
                    self.model_combo.configure(values=values)
                    self.status_var.set(status)

            self.dialog.after(0, apply_result)

        threading.Thread(target=worker, daemon=True).start()

    def save(self, *, close: bool = True) -> bool:
        try:
            self.repository.set_config(
                endpoint=self.endpoint_var.get(),
                model_id=self.model_var.get(),
                model_profile="free",
                protocol=self.protocol_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("设置无效", str(exc), parent=self.dialog)
            return False
        if close:
            self.dialog.destroy()
        return True

    def save_and_test(self) -> None:
        if not self.save(close=False):
            return
        threading.Thread(target=self._test_worker, daemon=True).start()

    def _test_worker(self) -> None:
        provider = None
        try:
            provider, config = build_configured_provider(self.repository)
            models = provider.list_models()
            active = config["active_model_id"]
            if active not in models:
                raise ValueError(f"模型 {active} 不在 /models 返回列表中。")
            response = provider.complete(
                messages=[
                    {
                        "role": "system",
                        "content": "必须调用 powertrade_capability_probe。",
                    },
                    {"role": "user", "content": "无副作用协议探测"},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "powertrade_capability_probe",
                            "description": "无副作用能力探测。",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
            )
            detected = "native" if response.tool_calls else "json"
            self.repository.set_config(detected_protocol=detected)
            self.result_queue.put(
                (
                    "connection",
                    (
                        True,
                        f"连接成功；协议：{detected}；命中渠道："
                        f"{response.router_provider or '未返回'}；上游模型："
                        f"{response.upstream_model or '未返回'}；告警："
                        f"{response.router_alert_count if response.router_alert_count is not None else '未知'}",
                    ),
                )
            )
        except Exception as exc:
            self.result_queue.put(("connection", (False, str(exc))))
        finally:
            if provider is not None:
                provider.close()
