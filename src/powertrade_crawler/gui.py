import csv
import calendar
import json
import sqlite3
import threading
import webbrowser
from contextlib import AbstractContextManager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING
from urllib.parse import urlencode
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    TOP,
    VERTICAL,
    W,
    X,
    Y,
    DoubleVar,
    StringVar,
    Tk,
    Toplevel,
    messagebox,
)
from tkinter.filedialog import asksaveasfilename
from tkinter import ttk

from powertrade_crawler.clients.elecheck import ElecheckClient, ElecheckUnauthorizedError
from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import get_credential, get_project_root, save_credential
from powertrade_crawler.elecheck_auth import (
    cache_elecheck_authorization,
    resolve_elecheck_authorization,
    set_elecheck_authorization_for_current_process,
)
from powertrade_crawler.gridstatus_downloader import (
    GRIDSTATUS_DEFAULT_PAGE_SIZE,
    GRIDSTATUS_MAX_PAGE_SIZE,
    DownloadCancelled,
    DownloadControl,
    checkpoint_path,
    download_dataset_csv_adaptive,
    format_gridstatus_time,
    parse_gridstatus_time,
)
from powertrade_crawler.dashboard_gui import ScheduleDataApp
from powertrade_crawler.elecheck_business_dashboard import (
    ElecheckMechanismDashboardApp,
    ElecheckPurchasingDashboardApp,
)
from powertrade_crawler.elecheck_collection import (
    build_clear_price_full_coverage_targets as build_shared_clear_price_targets,
)
from powertrade_crawler.elecheck_dashboard import ElecheckPriceDashboardApp
from powertrade_crawler.spiders.elecheck import (
    ElecheckClearPriceSpider,
    ElecheckMechanismElectricityPriceSpider,
    ElecheckPurchasingNationalRangeSpider,
)
from powertrade_crawler.models import ElexonRecord, EntsoeRecord, MarketRecord
from powertrade_crawler.registry import get_spider
from powertrade_crawler.sqlite_utils import sqlite_row_connection
from powertrade_crawler.spiders.entsoe import ENTSOE_BIDDING_ZONES, load_entsoe_request_configs
from powertrade_crawler.spiders.elexon import load_elexon_request_configs
from powertrade_crawler.storage import (
    init_db,
    upsert_elexon_records,
    upsert_entsoe_records,
    upsert_elecheck_clear_price_records,
    upsert_elecheck_mechanism_electricity_price_records,
    upsert_elecheck_purchasing_records,
    upsert_gridstatus_dataset_metadata_records,
    upsert_records,
)
from powertrade_crawler.ui_dispatch import UiLifecycle, install_ui_dispatcher

if TYPE_CHECKING:
    from powertrade_crawler.app_shell import PowertradeAppShell


GRIDSTATUS_QUERY_BASE_URL = "https://api.gridstatus.io/v1/datasets/{dataset_id}/query"
GRIDSTATUS_API_KEY_PLACEHOLDER = "replace-with-your-gridstatus-api-key"
GRIDSTATUS_API_KEY_SETTINGS_URL = "https://www.gridstatus.io/settings/api"
ENTSOE_DAY_AHEAD_DATASET = "entsoe_day_ahead_prices"
ENTSOE_TOKEN_PLACEHOLDER = "replace-with-your-entsoe-security-token"
ENTSOE_ALL_AREAS_LABEL = "全部区域"
ENTSOE_GB_PUBLICATION_STOP_DATE = date(2021, 6, 15)
ENTSOE_BORDER_AVAILABILITY_PATH = (
    get_project_root() / "configs" / "entsoe" / "border_availability.json"
)
ELEXON_API_KEY_PLACEHOLDER = "replace-with-your-elexon-api-key"
ENTSOE_AREA_NAMES = {
    "AL": "阿尔巴尼亚",
    "AT": "奥地利",
    "BA": "波黑",
    "BE": "比利时",
    "BG": "保加利亚",
    "CH": "瑞士",
    "CZ": "捷克",
    "DE-LU": "德国-卢森堡",
    "DK1": "丹麦西部",
    "DK2": "丹麦东部",
    "EE": "爱沙尼亚",
    "ES": "西班牙",
    "FI": "芬兰",
    "FR": "法国",
    "GB": "英国",
    "GR": "希腊",
    "HR": "克罗地亚",
    "HU": "匈牙利",
    "IE-SEM": "爱尔兰单一电力市场",
    "IT-CENTRE-NORTH": "意大利中北部",
    "IT-CENTRE-SOUTH": "意大利中南部",
    "IT-NORTH": "意大利北部",
    "IT-SARDINIA": "意大利撒丁岛",
    "IT-SICILY": "意大利西西里岛",
    "IT-SOUTH": "意大利南部",
    "LT": "立陶宛",
    "LV": "拉脱维亚",
    "ME": "黑山",
    "MK": "北马其顿",
    "NL": "荷兰",
    "NO1": "挪威 NO1",
    "NO2": "挪威 NO2",
    "NO3": "挪威 NO3",
    "NO4": "挪威 NO4",
    "NO5": "挪威 NO5",
    "PL": "波兰",
    "PT": "葡萄牙",
    "RO": "罗马尼亚",
    "RS": "塞尔维亚",
    "SE1": "瑞典 SE1",
    "SE2": "瑞典 SE2",
    "SE3": "瑞典 SE3",
    "SE4": "瑞典 SE4",
    "SI": "斯洛文尼亚",
    "SK": "斯洛伐克",
    "TR": "土耳其",
}


def resolve_sqlite_path() -> Path:
    database_url = get_settings().database_url
    if not database_url.startswith("sqlite:///"):
        raise ValueError(f"GUI only supports sqlite database URLs, got: {database_url}")
    return Path(database_url.replace("sqlite:///", "", 1)).resolve()


def prepare_gui_database() -> Path:
    db_path = resolve_sqlite_path()
    init_db()
    return db_path


def has_usable_gridstatus_api_key() -> bool:
    api_key = (get_credential("gridstatus_api_key") or "").strip()
    return bool(api_key and api_key != GRIDSTATUS_API_KEY_PLACEHOLDER)


def save_gridstatus_api_key(api_key: str, credential_path: Path | None = None) -> None:
    save_credential("gridstatus_api_key", api_key, path=credential_path)


def current_gridstatus_time_utc(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def suggested_download_range(
    metadata: dict[str, object],
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    earliest_text = str(metadata.get("earliest_available_time_utc") or "")
    earliest = parse_gridstatus_time(earliest_text)
    latest = current_gridstatus_time_utc(now)

    frequency = str(metadata.get("data_frequency") or "").upper()
    if "5_MIN" in frequency or "15_MIN" in frequency:
        window = timedelta(hours=6)
    elif "HOUR" in frequency:
        window = timedelta(days=3)
    elif "DAY" in frequency or "DAILY" in frequency:
        window = timedelta(days=30)
    else:
        window = timedelta(days=1)

    start = latest - window
    if earliest is not None and start < earliest:
        start = earliest
    return format_gridstatus_time(start), format_gridstatus_time(latest)


def full_download_range(
    metadata: dict[str, object],
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    start = str(metadata.get("earliest_available_time_utc") or "")
    status = str(metadata.get("status") or "active").strip().lower()
    if status == "active":
        end = format_gridstatus_time(current_gridstatus_time_utc(now))
    else:
        end = str(metadata.get("latest_available_time_utc") or "")
    return start, end


def gridstatus_dataset_columns(metadata: dict[str, object]) -> list[str]:
    raw_columns = metadata.get("all_columns_json") or []
    if isinstance(raw_columns, str):
        try:
            raw_columns = json.loads(raw_columns)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw_columns, list):
        return []

    columns: list[str] = []
    for item in raw_columns:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("column") or "").strip()
        else:
            name = str(item).strip()
        if name and name not in columns:
            columns.append(name)
    return columns


def suggested_gridstatus_filter(metadata: dict[str, object]) -> tuple[str, str]:
    columns = gridstatus_dataset_columns(metadata)
    if (
        metadata.get("dataset_id") == "ercot_spp_day_ahead_hourly"
        and "location_type" in columns
    ):
        return "location_type", "Load Zone"
    return "", ""


class GridStatusMetadataRepository:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or resolve_sqlite_path()

    def connect(self) -> AbstractContextManager[sqlite3.Connection]:
        return sqlite_row_connection(self.db_path)

    def filter_values(self, column: str) -> list[str]:
        if column not in {"source", "data_frequency", "status"}:
            raise ValueError(f"Unsupported filter column: {column}")
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {column}, COUNT(*) AS row_count
                FROM gridstatus_dataset_metadata
                WHERE {column} IS NOT NULL AND TRIM({column}) != ''
                GROUP BY {column}
                ORDER BY row_count DESC, {column}
                """
            ).fetchall()
        return [str(row[column]) for row in rows]

    def search(
        self,
        keyword: str = "",
        source: str = "",
        data_frequency: str = "",
        status: str = "active",
        limit: int = 300,
    ) -> list[sqlite3.Row]:
        where = []
        params: list[object] = []

        keyword = keyword.strip()
        if keyword:
            like = f"%{keyword}%"
            where.append(
                """
                (
                    dataset_id LIKE ?
                    OR name LIKE ?
                    OR description_chinese LIKE ?
                    OR description LIKE ?
                )
                """
            )
            params.extend([like, like, like, like])
        if source:
            where.append("source = ?")
            params.append(source)
        if data_frequency:
            where.append("data_frequency = ?")
            params.append(data_frequency)
        if status:
            where.append("status = ?")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)

        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT
                    id,
                    dataset_id,
                    name,
                    source,
                    status,
                    data_frequency,
                    earliest_available_time_utc,
                    latest_available_time_utc,
                    description,
                    description_chinese
                FROM gridstatus_dataset_metadata
                {where_sql}
                ORDER BY
                    CASE WHEN popularity_rank IS NULL THEN 1 ELSE 0 END,
                    popularity_rank,
                    source,
                    dataset_id
                LIMIT ?
                """,
                params,
            ).fetchall()

    def get_dataset(self, row_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM gridstatus_dataset_metadata
                WHERE id = ?
                """,
                (row_id,),
            ).fetchone()


class GridStatusMetadataApp:
    def __init__(
        self,
        root,
        repository: GridStatusMetadataRepository,
        *,
        configure_window: bool = True,
    ) -> None:
        self.root = root
        self.ui = UiLifecycle(root)
        self.repository = repository
        self.current_row: sqlite3.Row | None = None
        self.result_rows: dict[str, sqlite3.Row] = {}
        self.download_dialog: Toplevel | None = None
        self.download_message_var = StringVar()
        self.download_detail_var = StringVar()
        self.download_progress_var = DoubleVar(value=0.0)
        self.download_pause_button: ttk.Button | None = None
        self.download_control: DownloadControl | None = None
        self.download_start_time_var = StringVar()
        self.download_end_time_var = StringVar()
        self.download_filter_column_var = StringVar()
        self.download_filter_value_var = StringVar()
        self.download_page_size_var = StringVar(value=str(GRIDSTATUS_DEFAULT_PAGE_SIZE))
        self.download_format_var = StringVar(value="csv")
        self.download_range_mode_var = StringVar(value="sample")
        self.catalog_refresh_button: ttk.Button | None = None

        self.keyword_var = StringVar()
        self.source_var = StringVar()
        self.frequency_var = StringVar()
        self.status_var = StringVar(value="active")
        self.summary_var = StringVar(value="Ready")

        if configure_window:
            self.root.title("GridStatus 数据集目录")
            self.root.geometry("1220x760")
            self.root.minsize(980, 640)

        self.setup_styles()
        self.build_layout()
        self.load_filters()
        self.refresh_results()

    def setup_styles(self) -> None:
        style = ttk.Style()
        style.configure("Treeview", rowheight=28)
        style.configure("TButton", padding=(10, 5))
        style.configure("Toolbar.TFrame", padding=8)

    def build_layout(self) -> None:
        filter_panel = ttk.Frame(self.root, style="Toolbar.TFrame")
        filter_panel.pack(fill=X, padx=8, pady=(8, 0))
        filter_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        filter_bar.pack(fill=X)

        ttk.Label(filter_bar, text="关键词").pack(side=LEFT, padx=(0, 6))
        keyword_entry = ttk.Entry(filter_bar, textvariable=self.keyword_var, width=24)
        keyword_entry.pack(side=LEFT, padx=(0, 12))
        keyword_entry.bind("<Return>", lambda _event: self.refresh_results())

        ttk.Label(filter_bar, text="来源").pack(side=LEFT, padx=(0, 6))
        self.source_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.source_var,
            width=10,
            state="readonly",
        )
        self.source_combo.pack(side=LEFT, padx=(0, 12))

        ttk.Label(filter_bar, text="频率").pack(side=LEFT, padx=(0, 6))
        self.frequency_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.frequency_var,
            width=12,
            state="readonly",
        )
        self.frequency_combo.pack(side=LEFT, padx=(0, 12))

        ttk.Label(filter_bar, text="状态").pack(side=LEFT, padx=(0, 6))
        self.status_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.status_var,
            width=10,
            state="readonly",
        )
        self.status_combo.pack(side=LEFT, padx=(0, 12))

        filter_actions = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        filter_actions.pack(fill=X, pady=(5, 0))
        ttk.Button(filter_actions, text="搜索", command=self.refresh_results).pack(side=LEFT)
        ttk.Button(filter_actions, text="重置筛选", command=self.reset_filters).pack(
            side=LEFT,
            padx=(6, 0),
        )

        catalog_actions = ttk.Frame(self.root, padding=(8, 6, 8, 4))
        catalog_actions.pack(fill=X)
        self.catalog_refresh_button = ttk.Button(
            catalog_actions,
            text="更新数据集目录",
            command=self.refresh_dataset_catalog,
        )
        self.catalog_refresh_button.pack(side=LEFT)
        ttk.Button(
            catalog_actions,
            text="更换 API key",
            command=self.change_gridstatus_api_key,
        ).pack(
            side=LEFT,
            padx=(8, 0),
        )
        ttk.Label(
            self.root,
            text="选择数据集后可在右侧查看字段、API 地址并下载数据。",
            style="Muted.TLabel",
        ).pack(fill=X, padx=8, pady=(0, 5))

        status_bar = ttk.Frame(self.root)
        status_bar.pack(fill=X, side="bottom")
        ttk.Label(status_bar, textvariable=self.summary_var, anchor=W).pack(fill=X, padx=8, pady=4)

        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=3)
        main.add(right, weight=2)

        columns = ("name", "source", "frequency", "status", "time_range")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="数据集")
        self.tree.heading("source", text="来源")
        self.tree.heading("frequency", text="频率")
        self.tree.heading("status", text="状态")
        self.tree.heading("time_range", text="时间范围")
        self.tree.column("name", width=280, minwidth=180)
        self.tree.column("source", width=58, minwidth=46, anchor="center", stretch=False)
        self.tree.column("frequency", width=92, minwidth=78, anchor="center", stretch=False)
        self.tree.column("status", width=64, minwidth=56, anchor="center", stretch=False)
        self.tree.column("time_range", width=360, minwidth=260)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        tree_scroll = ttk.Scrollbar(left, orient=VERTICAL, command=self.tree.yview)
        tree_scroll_x = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(
            yscrollcommand=tree_scroll.set,
            xscrollcommand=tree_scroll_x.set,
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self.on_select_dataset)

        self.build_detail_panel(right)

    def build_detail_panel(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill=X, pady=(0, 8))
        self.title_label = ttk.Label(header, text="请选择一个数据集", font=("", 14, "bold"))
        self.title_label.pack(anchor=W)
        self.dataset_id_label = ttk.Label(header, text="")
        self.dataset_id_label.pack(anchor=W, pady=(3, 0))

        action_bar = ttk.Frame(parent)
        action_bar.pack(fill=X, pady=(0, 8))
        action_bar.columnconfigure(0, weight=1, uniform="dataset-action")
        action_bar.columnconfigure(1, weight=1, uniform="dataset-action")
        ttk.Button(action_bar, text="复制 ID", command=self.copy_dataset_id).grid(
            row=0,
            column=0,
            sticky="ew",
        )
        ttk.Button(action_bar, text="复制 API 地址", command=self.copy_api_url).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(8, 0),
        )
        ttk.Button(
            action_bar,
            text="下载 CSV",
            style="Primary.TButton",
            command=self.download_csv,
        ).grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(action_bar, text="打开来源", command=self.open_source_url).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(8, 0),
            pady=(6, 0),
        )

        notebook = ttk.Notebook(parent)
        notebook.pack(fill=BOTH, expand=True)

        self.description_text = self.create_text_tab(notebook, "说明")
        self.columns_text = self.create_text_tab(notebook, "字段")
        self.technical_text = self.create_text_tab(notebook, "技术")

    def create_text_tab(self, notebook: ttk.Notebook, label: str):
        frame = ttk.Frame(notebook)
        text = __import__("tkinter").Text(frame, wrap="word", height=10, padx=10, pady=10)
        scroll = ttk.Scrollbar(frame, orient=VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)
        text.configure(state="disabled")
        notebook.add(frame, text=label)
        return text

    def load_filters(self) -> None:
        self.source_combo["values"] = [""] + self.repository.filter_values("source")
        self.frequency_combo["values"] = [""] + self.repository.filter_values("data_frequency")
        self.status_combo["values"] = ["active", ""] + [
            value for value in self.repository.filter_values("status") if value != "active"
        ]

    def reset_filters(self) -> None:
        self.keyword_var.set("")
        self.source_var.set("")
        self.frequency_var.set("")
        self.status_var.set("active")
        self.refresh_results()

    def refresh_results(self) -> None:
        rows = self.repository.search(
            keyword=self.keyword_var.get(),
            source=self.source_var.get(),
            data_frequency=self.frequency_var.get(),
            status=self.status_var.get(),
        )
        self.result_rows = {}
        self.tree.delete(*self.tree.get_children())

        for row in rows:
            item_id = str(row["id"])
            self.result_rows[item_id] = row
            self.tree.insert(
                "",
                END,
                iid=item_id,
                values=(
                    row["name"] or row["dataset_id"],
                    row["source"] or "",
                    row["data_frequency"] or "",
                    row["status"] or "",
                    self.format_time_range(row),
                ),
            )

        self.summary_var.set(
            f"找到 {len(rows)} 个数据集。时间范围来自本地目录快照，"
            "可点击“更新数据集目录”刷新。"
        )
        if rows:
            first_id = str(rows[0]["id"])
            self.tree.selection_set(first_id)
            self.tree.focus(first_id)
            self.show_dataset(int(first_id))
        else:
            self.clear_detail()

    def refresh_dataset_catalog(self) -> None:
        if not self.ensure_gridstatus_api_key():
            return
        if self.catalog_refresh_button is not None:
            self.catalog_refresh_button.configure(state="disabled")
        self.summary_var.set("正在更新 GridStatus 数据集目录...")

        def worker() -> None:
            spider = None
            try:
                spider = get_spider("gridstatus_datasets")
                records = list(spider.crawl())
                written = upsert_gridstatus_dataset_metadata_records(records)
            except Exception as exc:
                message = str(exc)
                self.ui.post(self.on_catalog_refresh_error, message)
            else:
                self.ui.post(self.on_catalog_refresh_success, written)
            finally:
                if spider is not None:
                    spider.close()

        threading.Thread(target=worker, daemon=True).start()

    def on_catalog_refresh_success(self, written: int) -> None:
        if self.catalog_refresh_button is not None:
            self.catalog_refresh_button.configure(state="normal")
        self.load_filters()
        self.refresh_results()
        self.summary_var.set(f"GridStatus 数据集目录已更新：写入 {written} 条。")

    def on_catalog_refresh_error(self, message: str) -> None:
        if self.catalog_refresh_button is not None:
            self.catalog_refresh_button.configure(state="normal")
        self.summary_var.set("GridStatus 数据集目录更新失败。")
        messagebox.showerror("更新目录失败", message)

    def on_select_dataset(self, _event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self.show_dataset(int(selection[0]))

    def show_dataset(self, row_id: int) -> None:
        row = self.repository.get_dataset(row_id)
        if row is None:
            return
        self.current_row = row

        self.title_label.configure(text=row["name"] or row["dataset_id"])
        self.dataset_id_label.configure(text=f"{row['dataset_id']}  |  {row['source'] or '-'}")

        self.set_text(self.description_text, self.build_description(row))
        self.set_text(self.columns_text, self.build_columns(row))
        self.set_text(self.technical_text, self.build_technical(row))

    def clear_detail(self) -> None:
        self.current_row = None
        self.title_label.configure(text="没有匹配的数据集")
        self.dataset_id_label.configure(text="")
        self.set_text(self.description_text, "")
        self.set_text(self.columns_text, "")
        self.set_text(self.technical_text, "")

    def build_description(self, row: sqlite3.Row) -> str:
        parts = [
            "中文说明",
            row["description_chinese"] or "暂无中文说明",
            "",
            "英文原文",
            row["description"] or "No description.",
            "",
            "常用信息",
            f"dataset_id: {row['dataset_id']}",
            f"API 地址: {self.build_api_url(row)}",
            f"CSV 下载地址: {self.build_csv_url(row)}",
            f"可用时间: {self.format_time_range(row)}",
        ]
        return "\n".join(parts)

    def build_columns(self, row: sqlite3.Row) -> str:
        columns = self.parse_json(row["all_columns_json"], default=[])
        primary_keys = self.parse_json(row["primary_key_columns_json"], default=[])

        lines = ["主键字段", ", ".join(primary_keys) if primary_keys else "暂无", "", "返回字段"]
        if not columns:
            lines.append("暂无字段说明")
            return "\n".join(lines)

        for column in columns:
            if isinstance(column, dict):
                name = column.get("name") or column.get("column") or "-"
                data_type = column.get("type") or column.get("data_type") or "-"
                description = column.get("description") or column.get("comment") or ""
                lines.append(f"- {name}  ({data_type})")
                if description:
                    lines.append(f"  {description}")
            else:
                lines.append(f"- {column}")
        return "\n".join(lines)

    def build_technical(self, row: sqlite3.Row) -> str:
        pairs = [
            ("source", row["source"]),
            ("status", row["status"]),
            ("data_frequency", row["data_frequency"]),
            ("publication_frequency", row["publication_frequency"]),
            ("time_index_column", row["time_index_column"]),
            ("publish_time_column", row["publish_time_column"]),
            ("subseries_index_column", row["subseries_index_column"]),
            ("number_of_rows_approximate", row["number_of_rows_approximate"]),
            ("source_url", row["source_url"]),
            ("earliest_available_time_utc", row["earliest_available_time_utc"]),
            ("latest_available_time_utc", row["latest_available_time_utc"]),
            ("last_checked_time_utc", row["last_checked_time_utc"]),
            ("created_at_utc", row["created_at_utc"]),
            ("collected_at", row["collected_at"]),
        ]
        return "\n".join(f"{key}: {value if value not in (None, '') else '-'}" for key, value in pairs)

    def set_text(self, text_widget, content: str) -> None:
        text_widget.configure(state="normal")
        text_widget.delete("1.0", END)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    def copy_dataset_id(self) -> None:
        if not self.current_row:
            return
        self.copy_to_clipboard(self.current_row["dataset_id"], "dataset_id 已复制")

    def copy_api_url(self) -> None:
        if not self.current_row:
            return
        if not self.ensure_gridstatus_api_key():
            return
        self.copy_to_clipboard(self.build_api_url(self.current_row), "API 地址已复制")

    def download_csv(self) -> None:
        if not self.current_row:
            return
        if not self.ensure_gridstatus_api_key():
            return
        if not get_credential("gridstatus_api_key"):
            messagebox.showerror(
                "缺少 API key",
                "请先在 .auth/credentials.json 中设置 GridStatus API key。",
            )
            return

        self.show_download_options(dict(self.current_row))

    def change_gridstatus_api_key(self) -> None:
        api_key = self.ask_gridstatus_api_key()
        if not api_key:
            self.summary_var.set("未更改 GridStatus API key。")
            return

        save_gridstatus_api_key(api_key.strip())
        self.summary_var.set("GridStatus API key 已更换。")
        messagebox.showinfo(
            "API key 已更换",
            "新的 GridStatus API key 已保存到 .auth/credentials.json。",
        )

    def ensure_gridstatus_api_key(self) -> bool:
        if has_usable_gridstatus_api_key():
            return True

        api_key = self.ask_gridstatus_api_key()
        if not api_key:
            messagebox.showinfo("未设置 API key", "已取消下载。下载真实数据需要 GridStatus API key。")
            return False

        save_gridstatus_api_key(api_key.strip())
        self.summary_var.set("GridStatus API key 已保存。")
        return True

    def ask_gridstatus_api_key(self) -> str | None:
        dialog = Toplevel(self.root)
        dialog.title("设置 GridStatus API key")
        dialog.minsize(560, 250)
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()

        value = StringVar()
        result: dict[str, str | None] = {"api_key": None}

        ttk.Label(dialog, text="请输入 GridStatus API key", font=("", 11, "bold")).pack(
            anchor=W,
            padx=18,
            pady=(18, 6),
        )
        ttk.Label(
            dialog,
            text="每个用户的 API key 可在 GridStatus 设置页面获取：",
        ).pack(anchor=W, padx=18)

        url_row = ttk.Frame(dialog)
        url_row.pack(fill=X, padx=18, pady=(6, 12))
        url_entry = ttk.Entry(url_row)
        url_entry.insert(0, GRIDSTATUS_API_KEY_SETTINGS_URL)
        url_entry.configure(state="readonly")
        url_entry.pack(side=LEFT, fill=X, expand=True)

        def copy_settings_url() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(GRIDSTATUS_API_KEY_SETTINGS_URL)

        ttk.Button(url_row, text="复制网址", command=copy_settings_url).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            url_row,
            text="打开网页",
            command=lambda: webbrowser.open(GRIDSTATUS_API_KEY_SETTINGS_URL),
        ).pack(side=LEFT, padx=(8, 0))

        ttk.Label(dialog, text="API key").pack(anchor=W, padx=18)
        entry = ttk.Entry(dialog, textvariable=value, show="*", width=64)
        entry.pack(fill=X, padx=18, pady=(6, 14))
        entry.focus_set()

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=X, padx=18)

        def submit() -> None:
            result["api_key"] = value.get().strip()
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        ttk.Button(buttons, text="保存并继续", command=submit).pack(side=LEFT)
        ttk.Button(buttons, text="取消", command=cancel).pack(side=LEFT, padx=(8, 0))
        dialog.bind("<Return>", lambda _event: submit())
        dialog.bind("<Escape>", lambda _event: cancel())

        self.root.wait_window(dialog)
        return result["api_key"]

    def show_download_options(self, metadata: dict[str, object]) -> None:
        dialog = Toplevel(self.root)
        dialog.title("下载数据")
        dialog.minsize(650, 430)
        dialog.resizable(True, True)

        dataset_id = str(metadata["dataset_id"])
        start, end = suggested_download_range(metadata)
        self.download_start_time_var.set(start)
        self.download_end_time_var.set(end)
        filter_column, filter_value = suggested_gridstatus_filter(metadata)
        self.download_filter_column_var.set(filter_column)
        self.download_filter_value_var.set(filter_value)
        self.download_page_size_var.set(str(GRIDSTATUS_DEFAULT_PAGE_SIZE))
        self.download_range_mode_var.set("sample")

        ttk.Label(dialog, text=f"数据集：{dataset_id}", font=("", 11, "bold")).pack(
            anchor=W,
            padx=18,
            pady=(16, 8),
        )
        ttk.Label(dialog, text="选择时间和等值筛选条件；程序会使用 cursor 自动下载所有分页。").pack(
            anchor=W,
            padx=18,
            pady=(0, 12),
        )

        form = ttk.Frame(dialog)
        form.pack(fill=X, padx=18)
        mode_frame = ttk.Frame(form)
        mode_frame.grid(row=0, column=0, columnspan=2, sticky=W, pady=(0, 10))

        def use_sample_range() -> None:
            sample_start, sample_end = suggested_download_range(metadata)
            self.download_start_time_var.set(sample_start)
            self.download_end_time_var.set(sample_end)

        def use_full_range() -> None:
            full_start, full_end = full_download_range(metadata)
            self.download_start_time_var.set(full_start)
            self.download_end_time_var.set(full_end)

        def use_current_time() -> None:
            self.download_range_mode_var.set("custom")
            self.download_end_time_var.set(
                format_gridstatus_time(current_gridstatus_time_utc())
            )

        def on_mode_change() -> None:
            if self.download_range_mode_var.get() == "sample":
                use_sample_range()
            elif self.download_range_mode_var.get() == "full":
                use_full_range()

        ttk.Radiobutton(
            mode_frame,
            text="安全试下载",
            variable=self.download_range_mode_var,
            value="sample",
            command=on_mode_change,
        ).pack(side=LEFT)
        ttk.Radiobutton(
            mode_frame,
            text="完整数据集",
            variable=self.download_range_mode_var,
            value="full",
            command=on_mode_change,
        ).pack(side=LEFT, padx=(12, 0))
        ttk.Radiobutton(
            mode_frame,
            text="自定义时间",
            variable=self.download_range_mode_var,
            value="custom",
        ).pack(side=LEFT, padx=(12, 0))
        ttk.Label(form, text="开始时间").grid(row=1, column=0, sticky=W, pady=(0, 8))
        ttk.Entry(form, textvariable=self.download_start_time_var, width=46).grid(
            row=1,
            column=1,
            sticky=W,
            padx=(10, 0),
            pady=(0, 8),
        )
        ttk.Label(form, text="结束时间（UTC）").grid(row=2, column=0, sticky=W)
        ttk.Entry(form, textvariable=self.download_end_time_var, width=46).grid(
            row=2,
            column=1,
            sticky=W,
            padx=(10, 0),
        )
        ttk.Button(form, text="当前 UTC", command=use_current_time).grid(
            row=2,
            column=2,
            sticky=W,
            padx=(8, 0),
        )
        ttk.Label(form, text="筛选字段").grid(row=3, column=0, sticky=W, pady=(8, 0))
        ttk.Combobox(
            form,
            textvariable=self.download_filter_column_var,
            values=["", *gridstatus_dataset_columns(metadata)],
            width=43,
        ).grid(row=3, column=1, sticky=W, padx=(10, 0), pady=(8, 0))
        ttk.Label(form, text="筛选值").grid(row=4, column=0, sticky=W, pady=(8, 0))
        ttk.Entry(form, textvariable=self.download_filter_value_var, width=46).grid(
            row=4,
            column=1,
            sticky=W,
            padx=(10, 0),
            pady=(8, 0),
        )
        ttk.Label(form, text="每页行数").grid(row=5, column=0, sticky=W, pady=(8, 0))
        ttk.Spinbox(
            form,
            textvariable=self.download_page_size_var,
            from_=1_000,
            to=GRIDSTATUS_MAX_PAGE_SIZE,
            increment=1_000,
            width=14,
        ).grid(row=5, column=1, sticky=W, padx=(10, 0), pady=(8, 0))
        ttk.Label(form, text="保存格式").grid(row=6, column=0, sticky=W, pady=(8, 0))
        ttk.Combobox(
            form,
            textvariable=self.download_format_var,
            values=["csv", "sqlite"],
            state="readonly",
            width=14,
        ).grid(row=6, column=1, sticky=W, padx=(10, 0), pady=(8, 0))

        ttk.Label(
            dialog,
            text=(
                "等值筛选示例：location_type = Load Zone。每页最多 50000 行；"
                "时间格式示例：2023-04-21T00:00Z。"
            ),
            wraplength=610,
        ).pack(anchor=W, padx=18, pady=(12, 12))

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=X, padx=18)
        ttk.Button(
            buttons,
            text="开始下载",
            command=lambda: self.start_download_with_options(dialog, metadata),
        ).pack(side=LEFT)
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=LEFT, padx=(8, 0))

    def start_download_with_options(self, dialog: Toplevel, metadata: dict[str, object]) -> None:
        start_time = self.download_start_time_var.get().strip()
        end_time = self.download_end_time_var.get().strip()
        start = parse_gridstatus_time(start_time)
        end = parse_gridstatus_time(end_time)
        if start is None or end is None or start >= end:
            messagebox.showerror("时间范围无效", "请填写有效的开始和结束时间，且开始时间早于结束时间。")
            return

        filter_column = self.download_filter_column_var.get().strip()
        filter_value = self.download_filter_value_var.get().strip()
        if bool(filter_column) != bool(filter_value):
            messagebox.showerror("筛选条件不完整", "筛选字段和筛选值必须同时填写，或同时留空。")
            return
        available_columns = gridstatus_dataset_columns(metadata)
        if filter_column and available_columns and filter_column not in available_columns:
            messagebox.showerror("筛选字段无效", f"数据集不存在字段：{filter_column}")
            return
        try:
            page_size = int(self.download_page_size_var.get().strip())
        except ValueError:
            messagebox.showerror("每页行数无效", "每页行数必须是整数。")
            return
        if not 1 <= page_size <= GRIDSTATUS_MAX_PAGE_SIZE:
            messagebox.showerror(
                "每页行数无效",
                f"当前 GridStatus API 允许的范围是 1 到 {GRIDSTATUS_MAX_PAGE_SIZE}。",
            )
            return

        metadata["download_start_time_utc"] = format_gridstatus_time(start)
        metadata["download_end_time_utc"] = format_gridstatus_time(end)
        metadata["download_page_size"] = page_size
        if filter_column:
            metadata["download_filter_column"] = filter_column
            metadata["download_filter_value"] = filter_value
        else:
            metadata.pop("download_filter_column", None)
            metadata.pop("download_filter_value", None)

        dataset_id = str(metadata["dataset_id"])
        output_format = self.download_format_var.get() or "csv"
        if output_format == "sqlite":
            initialfile = f"{dataset_id}.db"
            defaultextension = ".db"
            filetypes = [("SQLite database", "*.db"), ("All files", "*.*")]
        else:
            initialfile = f"{dataset_id}.csv"
            defaultextension = ".csv"
            filetypes = [("CSV files", "*.csv"), ("All files", "*.*")]
        output_path = asksaveasfilename(
            title="保存 GridStatus 数据",
            initialfile=initialfile,
            defaultextension=defaultextension,
            filetypes=filetypes,
        )
        if not output_path:
            return
        output = Path(output_path)

        if self.download_range_mode_var.get() == "full":
            filter_summary = (
                f"筛选：{filter_column} = {filter_value}\n" if filter_column else "筛选：无\n"
            )
            if not messagebox.askyesno(
                "确认完整下载",
                (
                    f"时间：{format_gridstatus_time(start)} 到 {format_gridstatus_time(end)}\n"
                    f"{filter_summary}每页：{page_size} 行，使用 cursor 自动翻页。\n\n"
                    "完整数据集可能包含大量历史数据，并消耗较多 GridStatus API usage。\n\n"
                    "确认继续完整下载吗？"
                ),
            ):
                return

        if not self.confirm_existing_download_state(output):
            return

        dialog.destroy()
        self.summary_var.set(f"正在下载 {dataset_id} ...")
        self.show_download_dialog(dataset_id)
        self.download_control = DownloadControl(
            cancel_event=threading.Event(),
            pause_event=threading.Event(),
        )
        thread = threading.Thread(
            target=self.download_csv_worker,
            args=(metadata, output, dataset_id, output_format, self.download_control),
            daemon=True,
        )
        thread.start()

    def confirm_existing_download_state(self, output_path: Path) -> bool:
        state_path = checkpoint_path(output_path)
        if not output_path.exists() and not state_path.exists():
            return True

        choice = messagebox.askyesnocancel(
            "发现已有下载文件",
            (
                "已发现同名输出文件或断点文件。\n\n"
                "是：继续上次断点下载。\n"
                "否：删除旧文件并重新开始。\n"
                "取消：不开始下载。"
            ),
        )
        if choice is None:
            return False
        if choice:
            return True

        for path in (output_path, state_path):
            if path.exists():
                path.unlink()
        return True

    def show_download_dialog(self, dataset_id: str) -> None:
        if self.download_dialog is not None and self.download_dialog.winfo_exists():
            self.download_dialog.destroy()

        dialog = Toplevel(self.root)
        dialog.title("正在下载")
        dialog.minsize(480, 210)
        dialog.resizable(True, True)
        dialog.attributes("-toolwindow", False)
        dialog.protocol("WM_DELETE_WINDOW", self.cancel_download)

        self.download_message_var.set(f"正在下载 {dataset_id} 的 CSV 文件，请稍候...")
        self.download_detail_var.set("准备请求 GridStatus API")
        self.download_progress_var.set(0.0)
        ttk.Label(dialog, textvariable=self.download_message_var, wraplength=360).pack(
            side=TOP,
            fill=X,
            padx=18,
            pady=(18, 6),
        )
        ttk.Label(dialog, textvariable=self.download_detail_var, wraplength=420).pack(
            side=TOP,
            fill=X,
            padx=18,
            pady=(0, 8),
        )
        progress = ttk.Progressbar(
            dialog,
            mode="determinate",
            maximum=100,
            variable=self.download_progress_var,
        )
        progress.pack(fill=X, padx=18, pady=(0, 14))

        button_bar = ttk.Frame(dialog)
        button_bar.pack(fill=X, padx=18, pady=(0, 14))
        self.download_pause_button = ttk.Button(
            button_bar,
            text="暂停",
            command=self.toggle_download_pause,
        )
        self.download_pause_button.pack(side=LEFT)
        ttk.Button(button_bar, text="取消", command=self.cancel_download).pack(side=LEFT, padx=(8, 0))
        ttk.Label(button_bar, text="可最小化窗口，下载会继续。").pack(side=RIGHT)

        self.download_dialog = dialog

    def toggle_download_pause(self) -> None:
        if self.download_control is None or self.download_control.pause_event is None:
            return
        if self.download_control.pause_event.is_set():
            self.download_control.pause_event.clear()
            if self.download_pause_button is not None:
                self.download_pause_button.configure(text="暂停")
            self.update_download_message("继续下载")
        else:
            self.download_control.pause_event.set()
            if self.download_pause_button is not None:
                self.download_pause_button.configure(text="继续")
            self.update_download_message("下载已暂停")

    def cancel_download(self) -> None:
        if self.download_control is not None and self.download_control.cancel_event is not None:
            self.download_control.cancel_event.set()
        self.update_download_message("正在取消下载，会保留已完成的 CSV 和断点文件...")

    def download_csv_worker(
        self,
        metadata: dict[str, object],
        output_path: Path,
        dataset_id: str,
        output_format: str,
        control: DownloadControl,
    ) -> None:
        try:
            result = download_dataset_csv_adaptive(
                metadata=metadata,
                output_path=output_path,
                progress=lambda message: self.ui.post(self.update_download_message, message),
                control=control,
                output_format=output_format,
            )
        except DownloadCancelled as exc:
            message = str(exc)
            self.ui.post(self.on_download_cancelled, dataset_id, message)
        except Exception as exc:
            message = str(exc)
            self.ui.post(self.on_download_error, dataset_id, message)
        else:
            self.ui.post(self.on_download_success, dataset_id, result.output_path, result)

    def update_download_message(self, message) -> None:
        if isinstance(message, dict):
            text = str(message.get("message", "正在下载"))
            percent = float(message.get("percent", self.download_progress_var.get()) or 0)
            rows_written = message.get("rows_written", 0)
            requests_made = message.get("requests_made", 0)
            intervals_pending = message.get("intervals_pending", 0)
            self.download_message_var.set(text)
            self.download_detail_var.set(
                f"进度约 {percent:.1f}% | 已写入 {rows_written} 行 | "
                f"请求 {requests_made} 次 | 待处理区间 {intervals_pending} 个"
            )
            self.download_progress_var.set(percent)
            self.summary_var.set(text)
            return

        self.download_message_var.set(str(message))
        self.summary_var.set(str(message))

    def clear_download_state(self) -> None:
        self.download_control = None
        self.download_pause_button = None

    def on_download_success(self, dataset_id: str, output_path: Path, result) -> None:
        self.summary_var.set(f"{dataset_id} 文件已保存到 {output_path}")
        self.download_progress_var.set(100.0)
        if self.download_dialog is not None and self.download_dialog.winfo_exists():
            self.download_dialog.destroy()
            self.download_dialog = None
        self.clear_download_state()
        messagebox.showinfo(
            "下载完成",
            (
                f"文件已保存到：\n{output_path}\n\n"
                f"写入行数：{result.rows_written}\n"
                f"API 请求次数：{result.requests_made}\n"
                f"完成时间段：{result.intervals_completed}"
            ),
        )

    def on_download_cancelled(self, dataset_id: str, message: str) -> None:
        self.summary_var.set(f"{dataset_id} 下载已取消")
        if self.download_dialog is not None and self.download_dialog.winfo_exists():
            self.download_dialog.destroy()
            self.download_dialog = None
        self.clear_download_state()
        messagebox.showinfo(
            "下载已取消",
            f"{message}\n\n下次选择同一个保存路径时，会从断点继续。",
        )

    def on_download_error(self, dataset_id: str, message: str) -> None:
        self.summary_var.set(f"{dataset_id} CSV 下载失败")
        if self.download_dialog is not None and self.download_dialog.winfo_exists():
            self.download_dialog.destroy()
            self.download_dialog = None
        self.clear_download_state()
        messagebox.showerror("下载失败", message)

    def open_source_url(self) -> None:
        if not self.current_row:
            return
        source_url = self.current_row["source_url"]
        if not source_url:
            messagebox.showinfo("没有来源链接", "这个数据集没有 source_url。")
            return
        webbrowser.open(source_url)

    def copy_to_clipboard(self, value: str, message: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.summary_var.set(message)

    @staticmethod
    def build_api_url(row: sqlite3.Row) -> str:
        params = {"limit": 1000}
        api_key = (get_credential("gridstatus_api_key") or "").strip()
        if api_key and api_key != GRIDSTATUS_API_KEY_PLACEHOLDER:
            params["api_key"] = api_key
        return f"{GRIDSTATUS_QUERY_BASE_URL.format(dataset_id=row['dataset_id'])}?{urlencode(params)}"

    @staticmethod
    def build_csv_url(row: sqlite3.Row) -> str:
        params = {
            "return_format": "csv",
            "download": "true",
            "limit": 1000,
        }
        api_key = (get_credential("gridstatus_api_key") or "").strip()
        if api_key and api_key != GRIDSTATUS_API_KEY_PLACEHOLDER:
            params["api_key"] = api_key
        return f"{GRIDSTATUS_QUERY_BASE_URL.format(dataset_id=row['dataset_id'])}?{urlencode(params)}"

    @staticmethod
    def parse_json(value: str | None, default):
        if not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    @staticmethod
    def format_time_range(row: sqlite3.Row) -> str:
        start = row["earliest_available_time_utc"] or "?"
        end = row["latest_available_time_utc"] or "?"
        return f"{start} -> {end}"


class ElecheckDataRepository:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or resolve_sqlite_path()

    def connect(self) -> AbstractContextManager[sqlite3.Connection]:
        return sqlite_row_connection(self.db_path)

    def table_exists(self, table_name: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        return row is not None

    def distinct_values(self, table_name: str, column: str) -> list[str]:
        if not self.table_exists(table_name):
            return []
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {column}, COUNT(*) AS row_count
                FROM {table_name}
                WHERE {column} IS NOT NULL AND TRIM(CAST({column} AS TEXT)) != ''
                GROUP BY {column}
                ORDER BY row_count DESC, {column}
                """
            ).fetchall()
        return [str(row[column]) for row in rows]

    def area_options(self) -> list[str]:
        if not self.table_exists("elecheck_area_records"):
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT area_name, area_code
                FROM elecheck_area_records
                ORDER BY area_name
                """
            ).fetchall()
        return [f"{row['area_name']} ({row['area_code']})" for row in rows]

    def clear_price_area_targets(self) -> list[dict[str, str | None]]:
        if not self.table_exists("elecheck_area_records"):
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT area_name, area_code, earliest_clear_price_date
                FROM elecheck_area_records
                ORDER BY id
                """
            ).fetchall()
        return [
            {
                "area_name": str(row["area_name"]),
                "area_code": str(row["area_code"]),
                "earliest_clear_price_date": str(row["earliest_clear_price_date"])
                if row["earliest_clear_price_date"]
                else None,
            }
            for row in rows
        ]

    def clear_price_latest_daily_dates_by_area(self) -> dict[str, str]:
        if not self.table_exists("elecheck_clear_price_records"):
            return {}
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT area_code, MAX(start_date) AS latest_date
                FROM elecheck_clear_price_records
                WHERE start_date = end_date
                GROUP BY area_code
                """
            ).fetchall()
        return {
            str(row["area_code"]): str(row["latest_date"])
            for row in rows
            if row["area_code"] and row["latest_date"]
        }

    def resolve_area_code(self, area_selection: str) -> str:
        value = area_selection.strip()
        if not value:
            raise ValueError("请选择或输入地区。")
        if value.endswith(")") and "(" in value:
            return value.rsplit("(", 1)[1].rstrip(")").strip()
        if value.isdigit():
            return value
        if not self.table_exists("elecheck_area_records"):
            raise ValueError("地区映射表不存在，请先运行 powertrade init-db。")
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT area_code
                FROM elecheck_area_records
                WHERE area_name = ?
                """,
                (value,),
            ).fetchone()
        if row is None:
            raise ValueError(f"找不到地区：{value}")
        return str(row["area_code"])

    def earliest_clear_price_date(self, area_selection: str) -> str | None:
        area_code = self.resolve_area_code(area_selection)
        if not self.table_exists("elecheck_area_records"):
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT earliest_clear_price_date
                FROM elecheck_area_records
                WHERE area_code = ?
                """,
                (area_code,),
            ).fetchone()
        if row is None:
            return None
        value = row["earliest_clear_price_date"]
        return str(value) if value else None

    def search_clear_price(
        self,
        *,
        area_code: str = "",
        endpoint: str = "",
        metric: str = "",
        time96: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        where = []
        params: list[object] = []
        if area_code:
            where.append("area_code = ?")
            params.append(area_code)
        if endpoint:
            where.append("endpoint = ?")
            params.append(endpoint)
        if metric:
            where.append("metric = ?")
            params.append(metric)
        if time96:
            where.append("time96 = ?")
            params.append(time96)
        if start_date:
            where.append("start_date >= ?")
            params.append(start_date)
        if end_date:
            where.append("start_date <= ?")
            params.append(end_date)
        return self.query_table(
            table_name="elecheck_clear_price_records",
            columns=[
                "id",
                "endpoint",
                "area_code",
                "start_date",
                "end_date",
                "time96",
                "metric",
                "value",
                "unit",
                "currency",
                "collected_at",
                "raw_json",
            ],
            where=where,
            params=params,
            order_by="start_date DESC, end_date DESC, endpoint, time96, metric",
            limit=limit,
        )

    def delete_clear_price_records(self) -> int:
        if not self.table_exists("elecheck_clear_price_records"):
            return 0
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM elecheck_clear_price_records")
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            connection.commit()
        return deleted

    def delete_purchasing_records(self) -> int:
        if not self.table_exists("elecheck_purchasing_records"):
            return 0
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM elecheck_purchasing_records")
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            connection.commit()
        return deleted

    def delete_mechanism_records(self) -> int:
        if not self.table_exists("elecheck_mechanism_electricity_price_records"):
            return 0
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM elecheck_mechanism_electricity_price_records")
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            connection.commit()
        return deleted

    def search_purchasing(
        self,
        *,
        data_month: str = "",
        province_name: str = "",
        data_kind: str = "",
        metric: str = "",
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        where = []
        params: list[object] = []
        if data_month:
            where.append("data_month = ?")
            params.append(data_month)
        if province_name:
            where.append("province_name = ?")
            params.append(province_name)
        if data_kind:
            where.append("data_kind = ?")
            params.append(data_kind)
        if metric:
            where.append("metric = ?")
            params.append(metric)
        return self.query_table(
            table_name="elecheck_purchasing_records",
            columns=[
                "id",
                "endpoint",
                "data_kind",
                "data_month",
                "province_name",
                "metric",
                "value",
                "unit",
                "diff_value",
                "statistic",
                "related_province_name",
                "collected_at",
                "raw_json",
            ],
            where=where,
            params=params,
            order_by="data_month DESC, province_name, data_kind, metric, statistic",
            limit=limit,
        )

    def search_mechanism(
        self,
        *,
        region_name: str = "",
        category: str = "",
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        where = []
        params: list[object] = []
        if region_name:
            where.append("region_name = ?")
            params.append(region_name)
        if category:
            where.append("category = ?")
            params.append(category)
        return self.query_table(
            table_name="elecheck_mechanism_electricity_price_records",
            columns=[
                "id",
                "region_name",
                "region",
                "category",
                "price",
                "clear_price",
                "unit",
                "collected_at",
                "raw_json",
            ],
            where=where,
            params=params,
            order_by="region_name, category",
            limit=limit,
        )

    def query_table(
        self,
        *,
        table_name: str,
        columns: list[str],
        where: list[str],
        params: list[object],
        order_by: str,
        limit: int,
    ) -> list[sqlite3.Row]:
        if not self.table_exists(table_name):
            return []
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params = [*params, limit]
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM {table_name}
                {where_sql}
                ORDER BY {order_by}
                LIMIT ?
                """,
                params,
            ).fetchall()

    def export_query_to_csv(
        self,
        *,
        output_path: Path,
        table_name: str,
        columns: list[str],
        where: list[str],
        params: list[object],
        order_by: str,
        progress,
    ) -> int:
        if not self.table_exists(table_name):
            return 0
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        row_count = 0
        with self.connect() as connection, output_path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            cursor = connection.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM {table_name}
                {where_sql}
                ORDER BY {order_by}
                """,
                params,
            )
            for row in cursor:
                writer.writerow(dict(row))
                row_count += 1
                if row_count % 10000 == 0:
                    progress(row_count)
        progress(row_count)
        return row_count


class EntsoeDataRepository:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or resolve_sqlite_path()

    def connect(self) -> AbstractContextManager[sqlite3.Connection]:
        return sqlite_row_connection(self.db_path)

    def table_exists(self, table_name: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        return row is not None

    def search(
        self,
        *,
        dataset: str = "",
        area: str = "",
        in_domain: str = "",
        out_domain: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 1000,
    ) -> list[dict[str, object]]:
        if dataset == ENTSOE_DAY_AHEAD_DATASET:
            return self.search_day_ahead_prices(
                area=area,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
        return self.search_entsoe_records(
            dataset=dataset,
            area=area,
            in_domain=in_domain,
            out_domain=out_domain,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

    def search_entsoe_records(
        self,
        *,
        dataset: str = "",
        area: str = "",
        in_domain: str = "",
        out_domain: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 1000,
    ) -> list[dict[str, object]]:
        if not self.table_exists("entsoe_records"):
            return []

        where = []
        params: list[object] = []
        if dataset:
            where.append("dataset = ?")
            params.append(dataset)
        if area:
            where.append("(area = ? OR in_domain = ? OR out_domain = ?)")
            params.extend([area, area, area])
        if in_domain:
            where.append("in_domain = ?")
            params.append(in_domain)
        if out_domain:
            where.append("out_domain = ?")
            params.append(out_domain)
        if start_date:
            where.append("interval_start_utc >= ?")
            params.append(f"{start_date}T00:00Z")
        if end_date:
            where.append("interval_start_utc < ?")
            params.append(f"{end_date}T00:00Z")

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, dataset, category, title_zh, title_en, area, in_domain, out_domain,
                       interval_start_utc, interval_end_utc, psr_type, value, value_field,
                       unit, currency, resolution, document_type, process_type,
                       business_type, raw_json, collected_at
                FROM entsoe_records
                {where_sql}
                ORDER BY interval_start_utc DESC, dataset, area
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def search_day_ahead_prices(
        self,
        *,
        area: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 1000,
    ) -> list[dict[str, object]]:
        if not self.table_exists("market_records"):
            return []

        where = [
            "source = ?",
            "market = ?",
        ]
        params: list[object] = [
            "ENTSO-E Transparency Platform",
            "day_ahead",
        ]
        if area:
            where.append("region = ?")
            params.append(area)
        if start_date:
            where.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            where.append("trade_date < ?")
            params.append(end_date)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, region, trade_date, metric, value, unit, currency,
                       raw_json, collected_at
                FROM market_records
                WHERE {' AND '.join(where)}
                ORDER BY trade_date DESC, metric
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        normalized_rows: list[dict[str, object]] = []
        for row in rows:
            raw = self.parse_json(row["raw_json"], {})
            normalized_rows.append(
                {
                    "id": row["id"],
                    "dataset": ENTSOE_DAY_AHEAD_DATASET,
                    "category": "market",
                    "title_zh": "日前电价",
                    "title_en": "Day-ahead Energy Prices",
                    "area": row["region"],
                    "in_domain": raw.get("in_Domain"),
                    "out_domain": raw.get("out_Domain"),
                    "interval_start_utc": raw.get("interval_start_utc") or str(row["trade_date"]),
                    "interval_end_utc": raw.get("interval_end_utc"),
                    "psr_type": None,
                    "value": row["value"],
                    "value_field": "price",
                    "unit": row["unit"],
                    "currency": row["currency"],
                    "resolution": raw.get("resolution"),
                    "document_type": raw.get("documentType"),
                    "process_type": raw.get("processType"),
                    "business_type": raw.get("businessType"),
                    "raw_json": row["raw_json"],
                    "collected_at": row["collected_at"],
                }
            )
        return normalized_rows

    def delete_records(
        self,
        *,
        dataset: str,
        area: str = "",
        in_domain: str = "",
        out_domain: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if dataset == ENTSOE_DAY_AHEAD_DATASET:
            return self.delete_day_ahead_prices(
                area=area,
                start_date=start_date,
                end_date=end_date,
            )
        return self.delete_entsoe_records(
            dataset=dataset,
            area=area,
            in_domain=in_domain,
            out_domain=out_domain,
            start_date=start_date,
            end_date=end_date,
        )

    def delete_entsoe_records(
        self,
        *,
        dataset: str,
        area: str = "",
        in_domain: str = "",
        out_domain: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if not self.table_exists("entsoe_records"):
            return 0
        where, params = self.build_entsoe_where(
            dataset=dataset,
            area=area,
            in_domain=in_domain,
            out_domain=out_domain,
            start_date=start_date,
            end_date=end_date,
        )
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM entsoe_records WHERE {' AND '.join(where)}",
                params,
            )
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            connection.commit()
        return deleted

    def delete_day_ahead_prices(
        self,
        *,
        area: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if not self.table_exists("market_records"):
            return 0
        where, params = self.build_day_ahead_where(
            area=area,
            start_date=start_date,
            end_date=end_date,
        )
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM market_records WHERE {' AND '.join(where)}",
                params,
            )
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            connection.commit()
        return deleted

    def export_records(
        self,
        *,
        output_path: Path,
        dataset: str,
        area: str = "",
        in_domain: str = "",
        out_domain: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if dataset == ENTSOE_DAY_AHEAD_DATASET:
            return self.export_day_ahead_prices(
                output_path=output_path,
                area=area,
                start_date=start_date,
                end_date=end_date,
            )
        return self.export_entsoe_records(
            output_path=output_path,
            dataset=dataset,
            area=area,
            in_domain=in_domain,
            out_domain=out_domain,
            start_date=start_date,
            end_date=end_date,
        )

    def export_entsoe_records(
        self,
        *,
        output_path: Path,
        dataset: str,
        area: str = "",
        in_domain: str = "",
        out_domain: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if not self.table_exists("entsoe_records"):
            return 0
        columns = [
            "id",
            "dataset",
            "category",
            "title_zh",
            "title_en",
            "area",
            "in_domain",
            "out_domain",
            "interval_start_utc",
            "interval_end_utc",
            "psr_type",
            "value",
            "value_field",
            "unit",
            "currency",
            "resolution",
            "document_type",
            "process_type",
            "business_type",
            "raw_json",
            "collected_at",
        ]
        where, params = self.build_entsoe_where(
            dataset=dataset,
            area=area,
            in_domain=in_domain,
            out_domain=out_domain,
            start_date=start_date,
            end_date=end_date,
        )
        return self.export_query(
            output_path=output_path,
            table_name="entsoe_records",
            columns=columns,
            where=where,
            params=params,
            order_by="COALESCE(interval_start_utc, '') DESC, dataset, area",
        )

    def export_day_ahead_prices(
        self,
        *,
        output_path: Path,
        area: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if not self.table_exists("market_records"):
            return 0
        columns = [
            "id",
            "source",
            "market",
            "region",
            "trade_date",
            "metric",
            "value",
            "unit",
            "currency",
            "raw_json",
            "collected_at",
        ]
        where, params = self.build_day_ahead_where(
            area=area,
            start_date=start_date,
            end_date=end_date,
        )
        return self.export_query(
            output_path=output_path,
            table_name="market_records",
            columns=columns,
            where=where,
            params=params,
            order_by="trade_date DESC, metric",
        )

    def export_query(
        self,
        *,
        output_path: Path,
        table_name: str,
        columns: list[str],
        where: list[str],
        params: list[object],
        order_by: str,
    ) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        row_count = 0
        with self.connect() as connection, output_path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            cursor = connection.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM {table_name}
                WHERE {' AND '.join(where)}
                ORDER BY {order_by}
                """,
                params,
            )
            for row in cursor:
                writer.writerow(dict(row))
                row_count += 1
        return row_count

    def build_entsoe_where(
        self,
        *,
        dataset: str,
        area: str = "",
        in_domain: str = "",
        out_domain: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> tuple[list[str], list[object]]:
        where = ["dataset = ?"]
        params: list[object] = [dataset]
        if area:
            where.append("(area = ? OR in_domain = ? OR out_domain = ?)")
            params.extend([area, area, area])
        if in_domain:
            where.append("in_domain = ?")
            params.append(in_domain)
        if out_domain:
            where.append("out_domain = ?")
            params.append(out_domain)
        if start_date:
            where.append("COALESCE(interval_start_utc, '') >= ?")
            params.append(f"{start_date}T00:00Z")
        if end_date:
            where.append("COALESCE(interval_start_utc, '') < ?")
            params.append(f"{end_date}T00:00Z")
        return where, params

    def build_day_ahead_where(
        self,
        *,
        area: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> tuple[list[str], list[object]]:
        where = ["source = ?", "market = ?"]
        params: list[object] = ["ENTSO-E Transparency Platform", "day_ahead"]
        if area:
            where.append("region = ?")
            params.append(area)
        if start_date:
            where.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            where.append("trade_date < ?")
            params.append(end_date)
        return where, params

    @staticmethod
    def parse_json(value: str | None, default):
        if not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default


class DatePickerDialog:
    def __init__(self, root, initial_value: str = "") -> None:
        self.root = root
        self.selected_date: date | None = None
        self.current_month = self.parse_initial_date(initial_value)

        self.dialog = Toplevel(root)
        self.dialog.title("选择日期")
        self.dialog.minsize(460, 430)
        self.dialog.resizable(True, True)
        self.dialog.transient(root.winfo_toplevel())
        self.dialog.grab_set()

        self.year_var = StringVar(value=str(self.current_month.year))
        self.month_var = StringVar(value=str(self.current_month.month))
        self.build_layout()
        self.render_calendar()

    def build_layout(self) -> None:
        header = ttk.Frame(self.dialog, padding=(14, 14, 14, 8))
        header.pack(fill=X)
        ttk.Button(header, text="上月", width=8, command=lambda: self.shift_month(-1)).pack(side=LEFT)

        selectors = ttk.Frame(header)
        selectors.pack(side=LEFT, fill=X, expand=True, padx=12)
        ttk.Label(selectors, text="年份").pack(side=LEFT, padx=(0, 6))
        self.year_combo = ttk.Combobox(
            selectors,
            textvariable=self.year_var,
            values=[str(year) for year in range(2020, date.today().year + 2)],
            width=8,
            state="readonly",
        )
        self.year_combo.pack(side=LEFT, padx=(0, 12))
        ttk.Label(selectors, text="月份").pack(side=LEFT, padx=(0, 6))
        self.month_combo = ttk.Combobox(
            selectors,
            textvariable=self.month_var,
            values=[str(month) for month in range(1, 13)],
            width=6,
            state="readonly",
        )
        self.month_combo.pack(side=LEFT)
        self.year_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_year_month())
        self.month_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_year_month())

        ttk.Button(header, text="下月", width=8, command=lambda: self.shift_month(1)).pack(side=RIGHT)

        self.days_frame = ttk.Frame(self.dialog, padding=(14, 4, 14, 10))
        self.days_frame.pack(fill=BOTH, expand=True)

        footer = ttk.Frame(self.dialog, padding=(14, 4, 14, 14))
        footer.pack(fill=X)
        ttk.Button(footer, text="选择今天", width=12, command=self.select_today).pack(side=LEFT)
        ttk.Button(footer, text="取消", width=12, command=self.dialog.destroy).pack(side=RIGHT)

    def render_calendar(self) -> None:
        for child in self.days_frame.winfo_children():
            child.destroy()

        self.year_var.set(str(self.current_month.year))
        self.month_var.set(str(self.current_month.month))
        weekday_labels = ["一", "二", "三", "四", "五", "六", "日"]
        for column, label in enumerate(weekday_labels):
            ttk.Label(self.days_frame, text=label, anchor="center", font=("", 10, "bold")).grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=3,
                pady=4,
            )

        month_days = calendar.monthcalendar(self.current_month.year, self.current_month.month)
        for row_index, week in enumerate(month_days, start=1):
            for column, day in enumerate(week):
                if day == 0:
                    ttk.Label(self.days_frame, text="").grid(
                        row=row_index,
                        column=column,
                        sticky="nsew",
                        padx=3,
                        pady=3,
                    )
                    continue
                ttk.Button(
                    self.days_frame,
                    text=str(day),
                    width=7,
                    command=lambda day=day: self.select_day(day),
                ).grid(row=row_index, column=column, sticky="nsew", padx=3, pady=3, ipady=5)

        for column in range(7):
            self.days_frame.columnconfigure(column, weight=1)
        for row in range(7):
            self.days_frame.rowconfigure(row, weight=1)

    def apply_year_month(self) -> None:
        self.current_month = date(int(self.year_var.get()), int(self.month_var.get()), 1)
        self.render_calendar()

    def shift_month(self, offset: int) -> None:
        month_index = self.current_month.month - 1 + offset
        year = self.current_month.year + month_index // 12
        month = month_index % 12 + 1
        self.current_month = date(year, month, 1)
        self.render_calendar()

    def select_day(self, day: int) -> None:
        self.selected_date = date(self.current_month.year, self.current_month.month, day)
        self.dialog.destroy()

    def select_today(self) -> None:
        self.selected_date = date.today()
        self.dialog.destroy()

    def show(self) -> str | None:
        self.root.wait_window(self.dialog)
        if self.selected_date is None:
            return None
        return self.selected_date.isoformat()

    @staticmethod
    def parse_initial_date(value: str) -> date:
        try:
            parsed = date.fromisoformat(value.strip())
            return date(parsed.year, parsed.month, 1)
        except ValueError:
            today = date.today()
            return date(today.year, today.month, 1)


class ElecheckDataApp:
    all_areas_label = "全部"

    def __init__(
        self,
        root,
        repository: ElecheckDataRepository,
        on_navigate=None,
    ) -> None:
        self.root = root
        self.ui = UiLifecycle(root)
        self.repository = repository
        self.on_navigate = on_navigate
        self.summary_var = StringVar(value="Ready")

        self.clear_area_var = StringVar()
        today = date.today()
        self.clear_start_date_var = StringVar(value=(today - timedelta(days=7)).isoformat())
        self.clear_end_date_var = StringVar(value=today.isoformat())
        self.clear_filter_area_var = StringVar()
        self.clear_filter_start_date_var = StringVar()
        self.clear_filter_end_date_var = StringVar()
        self.clear_filter_endpoint_var = StringVar()
        self.clear_filter_metric_var = StringVar()
        self.clear_filter_time96_var = StringVar()
        self.clear_earliest_date_hint_var = StringVar(value="请选择地区以查看最早可用日期")
        self.clear_authorization_var = StringVar()
        self.clear_crawl_button: ttk.Button | None = None
        self.clear_full_crawl_button: ttk.Button | None = None
        self.clear_crawl_dialog: Toplevel | None = None
        self.clear_crawl_message_var = StringVar()
        self.clear_crawl_detail_var = StringVar()
        self.clear_crawl_progress_var = DoubleVar(value=0.0)
        self.clear_crawl_pause_button: ttk.Button | None = None
        self.clear_crawl_pause_event: threading.Event | None = None
        self.clear_crawl_stop_event: threading.Event | None = None
        self.clear_crawl_abandon_event: threading.Event | None = None
        self.clear_price_dashboard: ElecheckPriceDashboardApp | None = None
        self.purchasing_dashboard: ElecheckPurchasingDashboardApp | None = None
        self.mechanism_dashboard: ElecheckMechanismDashboardApp | None = None
        self.agent_app = None

        self.purchasing_month_var = StringVar()
        self.purchasing_province_var = StringVar()
        self.purchasing_kind_var = StringVar()
        self.purchasing_metric_var = StringVar()
        self.purchasing_authorization_var = StringVar()
        self.purchasing_collect_button: ttk.Button | None = None
        self.purchasing_collect_dialog: Toplevel | None = None
        self.purchasing_collect_message_var = StringVar()
        self.purchasing_collect_detail_var = StringVar()
        self.purchasing_collect_progress_var = DoubleVar(value=0.0)

        self.mechanism_region_var = StringVar()
        self.mechanism_category_var = StringVar()
        self.mechanism_collect_button: ttk.Button | None = None
        self.mechanism_collect_dialog: Toplevel | None = None
        self.mechanism_collect_message_var = StringVar()
        self.mechanism_collect_detail_var = StringVar()
        self.mechanism_collect_progress_var = DoubleVar(value=0.0)

        self.result_rows: dict[str, list[sqlite3.Row]] = {
            "clear": [],
            "purchasing": [],
            "mechanism": [],
        }

        self.build_layout()
        self.load_filter_values()
        self.refresh_clear_price()
        self.refresh_purchasing()
        self.refresh_mechanism()

    def pick_date(self, target_var: StringVar) -> None:
        selected = DatePickerDialog(self.root, target_var.get()).show()
        if selected:
            target_var.set(selected)
            if target_var is self.clear_start_date_var:
                self.update_clear_price_earliest_hint(adjust_start_date=False)

    def build_layout(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=BOTH, expand=True, padx=8, pady=8)
        self.notebook = notebook

        from powertrade_crawler.agent.gui import ElecheckAgentApp

        dashboard_frame = ttk.Frame(notebook)
        notebook.add(dashboard_frame, text="现货价格分析")
        self.clear_price_dashboard = ElecheckPriceDashboardApp(
            dashboard_frame,
            self.repository.db_path,
        )
        self.clear_tree, self.clear_raw_text = self.build_clear_price_tab(notebook)

        purchasing_dashboard_frame = ttk.Frame(notebook)
        notebook.add(purchasing_dashboard_frame, text="代理购电分析")
        self.purchasing_dashboard = ElecheckPurchasingDashboardApp(
            purchasing_dashboard_frame,
            self.repository.db_path,
        )
        self.purchasing_tree, self.purchasing_raw_text = self.build_purchasing_tab(notebook)

        mechanism_dashboard_frame = ttk.Frame(notebook)
        notebook.add(mechanism_dashboard_frame, text="增量机制分析")
        self.mechanism_dashboard = ElecheckMechanismDashboardApp(
            mechanism_dashboard_frame,
            self.repository.db_path,
        )
        self.mechanism_tree, self.mechanism_raw_text = self.build_mechanism_tab(notebook)

        agent_frame = ttk.Frame(notebook)
        notebook.add(agent_frame, text="智能 Agent")
        self.agent_app = ElecheckAgentApp(agent_frame, on_navigate=self.on_navigate)

        status_bar = ttk.Frame(self.root)
        status_bar.pack(fill=X, side="bottom")
        ttk.Label(status_bar, textvariable=self.summary_var, anchor=W).pack(fill=X, padx=8, pady=4)

    def build_clear_price_tab(self, notebook: ttk.Notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="现货价格数据")

        crawl_panel = ttk.LabelFrame(frame, text="在线采集", padding=8)
        crawl_panel.pack(fill=X, padx=8, pady=(6, 3))
        crawl_scope_bar = ttk.Frame(crawl_panel, style="ToolbarRow.TFrame")
        crawl_scope_bar.pack(fill=X)
        ttk.Label(crawl_scope_bar, text="地区").pack(side=LEFT, padx=(0, 6))
        self.clear_area_combo = ttk.Combobox(
            crawl_scope_bar,
            textvariable=self.clear_area_var,
            width=16,
        )
        self.clear_area_combo.pack(side=LEFT, padx=(0, 8))
        self.clear_area_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.update_clear_price_earliest_hint(),
        )
        self.clear_area_combo.bind(
            "<FocusOut>",
            lambda _event: self.update_clear_price_earliest_hint(adjust_start_date=False),
        )
        ttk.Label(crawl_scope_bar, text="开始日期").pack(side=LEFT, padx=(0, 6))
        ttk.Entry(
            crawl_scope_bar,
            textvariable=self.clear_start_date_var,
            width=12,
            state="readonly",
        ).pack(
            side=LEFT,
            padx=(0, 4),
        )
        ttk.Button(
            crawl_scope_bar,
            text="选择",
            width=5,
            command=lambda: self.pick_date(self.clear_start_date_var),
        ).pack(
            side=LEFT,
            padx=(0, 8),
        )
        ttk.Label(crawl_scope_bar, text="结束日期").pack(side=LEFT, padx=(0, 6))
        ttk.Entry(
            crawl_scope_bar,
            textvariable=self.clear_end_date_var,
            width=12,
            state="readonly",
        ).pack(
            side=LEFT,
            padx=(0, 4),
        )
        ttk.Button(
            crawl_scope_bar,
            text="选择",
            width=5,
            command=lambda: self.pick_date(self.clear_end_date_var),
        ).pack(
            side=LEFT,
            padx=(0, 8),
        )

        ttk.Label(
            crawl_panel,
            textvariable=self.clear_earliest_date_hint_var,
            foreground="#666666",
        ).pack(fill=X, pady=(5, 3))

        crawl_action_bar = ttk.Frame(crawl_panel, style="ToolbarRow.TFrame")
        crawl_action_bar.pack(fill=X)
        crawl_actions = ttk.Frame(crawl_action_bar, style="ToolbarRow.TFrame")
        crawl_actions.pack(side=RIGHT)
        ttk.Label(crawl_action_bar, text="采集凭据（可留空）").pack(side=LEFT, padx=(0, 6))
        ttk.Entry(
            crawl_action_bar,
            textvariable=self.clear_authorization_var,
            width=16,
            show="*",
        ).pack(
            side=LEFT,
            padx=(0, 8),
        )
        self.clear_crawl_button = ttk.Button(
            crawl_actions,
            text="采集指定范围",
            width=12,
            command=self.start_clear_price_crawl,
        )
        self.clear_crawl_button.pack(side=LEFT)
        self.clear_full_crawl_button = ttk.Button(
            crawl_actions,
            text="全地区采集/更新",
            width=16,
            style="Primary.TButton",
            command=self.start_clear_price_full_coverage_crawl,
        )
        self.clear_full_crawl_button.pack(side=LEFT, padx=(6, 0))

        filter_panel = ttk.LabelFrame(frame, text="本地数据筛选", padding=8)
        filter_panel.pack(fill=X, padx=8, pady=3)
        primary_filter_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        primary_filter_bar.pack(fill=X)

        ttk.Label(primary_filter_bar, text="筛选地区").pack(side=LEFT, padx=(0, 3))
        self.clear_filter_area_combo = ttk.Combobox(
            primary_filter_bar,
            textvariable=self.clear_filter_area_var,
            width=12,
        )
        self.clear_filter_area_combo.pack(side=LEFT, padx=(0, 5))
        ttk.Label(primary_filter_bar, text="日期起").pack(side=LEFT, padx=(0, 3))
        ttk.Entry(
            primary_filter_bar,
            textvariable=self.clear_filter_start_date_var,
            width=10,
            state="readonly",
        ).pack(side=LEFT, padx=(0, 2))
        ttk.Button(
            primary_filter_bar,
            text="选择",
            width=4,
            command=lambda: self.pick_date(self.clear_filter_start_date_var),
        ).pack(side=LEFT, padx=(0, 5))
        ttk.Label(primary_filter_bar, text="日期止").pack(side=LEFT, padx=(0, 3))
        ttk.Entry(
            primary_filter_bar,
            textvariable=self.clear_filter_end_date_var,
            width=10,
            state="readonly",
        ).pack(side=LEFT, padx=(0, 2))
        ttk.Button(
            primary_filter_bar,
            text="选择",
            width=4,
            command=lambda: self.pick_date(self.clear_filter_end_date_var),
        ).pack(side=LEFT, padx=(0, 5))

        secondary_filter_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        secondary_filter_bar.pack(fill=X, pady=(5, 0))
        ttk.Label(secondary_filter_bar, text="结果接口").pack(side=LEFT, padx=(0, 3))
        self.clear_endpoint_combo = ttk.Combobox(
            secondary_filter_bar,
            textvariable=self.clear_filter_endpoint_var,
            values=["", "detail", "statistics"],
            width=8,
            state="readonly",
        )
        self.clear_endpoint_combo.pack(side=LEFT, padx=(0, 5))
        ttk.Label(secondary_filter_bar, text="结果指标").pack(side=LEFT, padx=(0, 3))
        self.clear_metric_combo = ttk.Combobox(
            secondary_filter_bar,
            textvariable=self.clear_filter_metric_var,
            width=13,
        )
        self.clear_metric_combo.pack(side=LEFT, padx=(0, 5))
        ttk.Label(secondary_filter_bar, text="时点").pack(side=LEFT, padx=(0, 3))
        self.clear_time96_combo = ttk.Combobox(
            secondary_filter_bar,
            textvariable=self.clear_filter_time96_var,
            width=6,
        )
        self.clear_time96_combo.pack(side=LEFT, padx=(0, 5))

        filter_action_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        filter_action_bar.pack(fill=X, pady=(5, 0))
        ttk.Button(
            filter_action_bar,
            text="刷新结果",
            command=self.refresh_clear_price,
        ).pack(side=LEFT)
        ttk.Button(filter_action_bar, text="重置筛选", command=self.reset_clear_price).pack(
            side=LEFT,
            padx=(6, 0),
        )
        ttk.Button(
            filter_action_bar,
            text="导出 CSV",
            command=lambda: self.export_rows("clear"),
        ).pack(
            side=LEFT,
            padx=(6, 0),
        )
        ttk.Button(
            filter_action_bar,
            text="清空数据",
            command=self.clear_all_clear_price_records,
        ).pack(side=RIGHT)

        columns = ("date", "endpoint", "area_code", "time96", "metric", "value", "unit")
        tree, raw_text = self.build_result_panel(frame, columns, self.on_select_clear_price)
        headings = {
            "date": ("日期", 110),
            "endpoint": ("接口", 80),
            "area_code": ("地区编码", 120),
            "time96": ("时点", 80),
            "metric": ("指标", 190),
            "value": ("数值", 110),
            "unit": ("单位", 90),
        }
        self.configure_tree_columns(tree, headings)
        return tree, raw_text

    def build_purchasing_tab(self, notebook: ttk.Notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="代理购电数据")

        collect_toolbar = ttk.LabelFrame(frame, text="在线采集", padding=8)
        collect_toolbar.pack(fill=X, padx=8, pady=(6, 3))
        ttk.Label(collect_toolbar, text="采集凭据（可留空）").pack(side=LEFT, padx=(0, 4))
        ttk.Entry(
            collect_toolbar,
            textvariable=self.purchasing_authorization_var,
            width=16,
            show="*",
        ).pack(side=LEFT, padx=(0, 6))
        self.purchasing_collect_button = ttk.Button(
            collect_toolbar,
            text="采集全部",
            width=10,
            style="Primary.TButton",
            command=self.start_purchasing_collect_all,
        )
        self.purchasing_collect_button.pack(side=LEFT)
        ttk.Button(
            collect_toolbar,
            text="清空数据",
            width=10,
            command=self.clear_all_purchasing_records,
        ).pack(side=RIGHT)

        filter_panel = ttk.LabelFrame(frame, text="本地数据筛选", padding=8)
        filter_panel.pack(fill=X, padx=8, pady=3)
        primary_filter_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        primary_filter_bar.pack(fill=X)

        ttk.Label(primary_filter_bar, text="月份").pack(side=LEFT, padx=(0, 6))
        self.purchasing_month_combo = ttk.Combobox(
            primary_filter_bar,
            textvariable=self.purchasing_month_var,
            width=12,
        )
        self.purchasing_month_combo.pack(side=LEFT, padx=(0, 10))
        ttk.Label(primary_filter_bar, text="省份").pack(side=LEFT, padx=(0, 6))
        self.purchasing_province_combo = ttk.Combobox(
            primary_filter_bar,
            textvariable=self.purchasing_province_var,
            width=14,
        )
        self.purchasing_province_combo.pack(side=LEFT, padx=(0, 10))

        secondary_filter_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        secondary_filter_bar.pack(fill=X, pady=(5, 0))
        ttk.Label(secondary_filter_bar, text="类型").pack(side=LEFT, padx=(0, 6))
        self.purchasing_kind_combo = ttk.Combobox(
            secondary_filter_bar,
            textvariable=self.purchasing_kind_var,
            width=18,
        )
        self.purchasing_kind_combo.pack(side=LEFT, padx=(0, 10))
        ttk.Label(secondary_filter_bar, text="指标").pack(side=LEFT, padx=(0, 6))
        self.purchasing_metric_combo = ttk.Combobox(
            secondary_filter_bar,
            textvariable=self.purchasing_metric_var,
            width=24,
        )
        self.purchasing_metric_combo.pack(side=LEFT, padx=(0, 10))

        filter_action_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        filter_action_bar.pack(fill=X, pady=(5, 0))
        ttk.Button(filter_action_bar, text="搜索", command=self.refresh_purchasing).pack(side=LEFT)
        ttk.Button(filter_action_bar, text="重置筛选", command=self.reset_purchasing).pack(
            side=LEFT,
            padx=(6, 0),
        )
        ttk.Button(
            filter_action_bar,
            text="导出 CSV",
            command=lambda: self.export_rows("purchasing"),
        ).pack(
            side=LEFT,
            padx=(6, 0),
        )

        columns = ("month", "province", "kind", "metric", "value", "statistic", "related")
        tree, raw_text = self.build_result_panel(frame, columns, self.on_select_purchasing)
        headings = {
            "month": ("月份", 90),
            "province": ("省份", 100),
            "kind": ("数据类型", 150),
            "metric": ("指标", 190),
            "value": ("数值", 100),
            "statistic": ("统计", 100),
            "related": ("关联省份", 100),
        }
        self.configure_tree_columns(tree, headings)
        return tree, raw_text

    def build_mechanism_tab(self, notebook: ttk.Notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="增量机制数据")
        filter_panel = ttk.LabelFrame(frame, text="筛选与在线采集", padding=8)
        filter_panel.pack(fill=X, padx=8, pady=(6, 3))
        filter_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        filter_bar.pack(fill=X)

        ttk.Label(filter_bar, text="地区").pack(side=LEFT, padx=(0, 6))
        self.mechanism_region_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.mechanism_region_var,
            width=16,
        )
        self.mechanism_region_combo.pack(side=LEFT, padx=(0, 10))
        ttk.Label(filter_bar, text="电源类型").pack(side=LEFT, padx=(0, 6))
        self.mechanism_category_combo = ttk.Combobox(
            filter_bar,
            textvariable=self.mechanism_category_var,
            width=16,
        )
        self.mechanism_category_combo.pack(side=LEFT, padx=(0, 10))

        action_bar = ttk.Frame(filter_panel, style="ToolbarRow.TFrame")
        action_bar.pack(fill=X, pady=(5, 0))
        ttk.Button(action_bar, text="搜索", command=self.refresh_mechanism).pack(side=LEFT)
        ttk.Button(action_bar, text="重置筛选", command=self.reset_mechanism).pack(
            side=LEFT,
            padx=(6, 0),
        )
        ttk.Button(
            action_bar,
            text="导出 CSV",
            command=lambda: self.export_rows("mechanism"),
        ).pack(
            side=LEFT,
            padx=(6, 0),
        )
        self.mechanism_collect_button = ttk.Button(
            action_bar,
            text="采集全部",
            width=10,
            style="Primary.TButton",
            command=self.start_mechanism_collect_all,
        )
        self.mechanism_collect_button.pack(side=LEFT, padx=(6, 0))
        ttk.Button(
            action_bar,
            text="清空数据",
            width=8,
            command=self.clear_all_mechanism_records,
        ).pack(side=RIGHT)

        columns = ("region_name", "region", "category", "price", "clear_price", "unit")
        tree, raw_text = self.build_result_panel(frame, columns, self.on_select_mechanism)
        headings = {
            "region_name": ("地区", 120),
            "region": ("地区标识", 110),
            "category": ("电源类型", 140),
            "price": ("燃煤基准价", 110),
            "clear_price": ("26年增量机制电价", 140),
            "unit": ("单位", 90),
        }
        self.configure_tree_columns(tree, headings)
        return tree, raw_text

    def build_result_panel(self, parent: ttk.Frame, columns: tuple[str, ...], select_callback):
        main = ttk.PanedWindow(parent, orient="vertical")
        main.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))

        table_frame = ttk.Frame(main)
        detail_frame = ttk.Frame(main)
        main.add(table_frame, weight=4)
        main.add(detail_frame, weight=1)

        tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        scroll = ttk.Scrollbar(table_frame, orient=VERTICAL, command=tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=scroll_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        tree.bind("<<TreeviewSelect>>", select_callback)

        raw_text = __import__("tkinter").Text(detail_frame, wrap="word", height=6, padx=10, pady=8)
        raw_scroll = ttk.Scrollbar(detail_frame, orient=VERTICAL, command=raw_text.yview)
        raw_text.configure(yscrollcommand=raw_scroll.set)
        raw_text.pack(side=LEFT, fill=BOTH, expand=True)
        raw_scroll.pack(side=RIGHT, fill=Y)
        raw_text.configure(state="disabled")
        return tree, raw_text

    def configure_tree_columns(self, tree: ttk.Treeview, headings: dict[str, tuple[str, int]]) -> None:
        for column, (label, width) in headings.items():
            tree.heading(column, text=label)
            tree.column(column, width=width, minwidth=max(70, width // 2), anchor="center")

    def load_filter_values(self) -> None:
        area_options = self.repository.area_options()
        if area_options:
            self.clear_area_combo["values"] = ["", self.all_areas_label] + area_options
            self.clear_filter_area_combo["values"] = ["", self.all_areas_label] + area_options
        else:
            fallback_area_values = [""] + self.repository.distinct_values(
                "elecheck_clear_price_records",
                "area_code",
            )
            self.clear_area_combo["values"] = fallback_area_values
            self.clear_filter_area_combo["values"] = fallback_area_values
        self.clear_metric_combo["values"] = [""] + self.repository.distinct_values(
            "elecheck_clear_price_records",
            "metric",
        )
        self.clear_time96_combo["values"] = [""] + self.repository.distinct_values(
            "elecheck_clear_price_records",
            "time96",
        )
        self.purchasing_month_combo["values"] = [""] + self.repository.distinct_values(
            "elecheck_purchasing_records",
            "data_month",
        )
        self.purchasing_province_combo["values"] = [""] + self.repository.distinct_values(
            "elecheck_purchasing_records",
            "province_name",
        )
        self.purchasing_kind_combo["values"] = [""] + self.repository.distinct_values(
            "elecheck_purchasing_records",
            "data_kind",
        )
        self.purchasing_metric_combo["values"] = [""] + self.repository.distinct_values(
            "elecheck_purchasing_records",
            "metric",
        )
        self.mechanism_region_combo["values"] = [""] + self.repository.distinct_values(
            "elecheck_mechanism_electricity_price_records",
            "region_name",
        )
        self.mechanism_category_combo["values"] = [""] + self.repository.distinct_values(
            "elecheck_mechanism_electricity_price_records",
            "category",
        )
        self.update_clear_full_crawl_button_text()
        self.update_clear_price_earliest_hint(adjust_start_date=False)

    def update_clear_full_crawl_button_text(self) -> None:
        if self.clear_full_crawl_button is None:
            return
        self.clear_full_crawl_button.configure(text="全地区采集/更新")

    def update_clear_price_earliest_hint(self, adjust_start_date: bool = True) -> None:
        area_selection = self.clear_area_var.get().strip()
        if not area_selection:
            self.clear_earliest_date_hint_var.set("请选择地区以查看最早可用日期")
            return
        if area_selection == self.all_areas_label:
            self.clear_earliest_date_hint_var.set("全部地区：开始采集使用所选日期；全量/更新请点右侧按钮")
            return

        try:
            earliest_date = self.repository.earliest_clear_price_date(area_selection)
        except ValueError:
            self.clear_earliest_date_hint_var.set("未找到该地区的最早可用日期")
            return

        if not earliest_date:
            self.clear_earliest_date_hint_var.set("该地区尚未探测最早可用日期")
            return

        self.clear_earliest_date_hint_var.set(f"最早可选：{earliest_date}")
        if adjust_start_date and self.clear_start_date_var.get() < earliest_date:
            self.clear_start_date_var.set(earliest_date)

    def refresh_clear_price(self) -> None:
        area_code = ""
        area_selection = self.clear_filter_area_var.get().strip()
        if area_selection and area_selection != self.all_areas_label:
            try:
                area_code = self.repository.resolve_area_code(area_selection)
            except ValueError:
                area_code = area_selection
        rows = self.repository.search_clear_price(
            area_code=area_code,
            endpoint=self.clear_filter_endpoint_var.get().strip(),
            metric=self.clear_filter_metric_var.get().strip(),
            time96=self.clear_filter_time96_var.get().strip(),
            start_date=self.clear_filter_start_date_var.get().strip(),
            end_date=self.clear_filter_end_date_var.get().strip(),
        )
        self.result_rows["clear"] = rows
        self.clear_tree.delete(*self.clear_tree.get_children())
        for index, row in enumerate(rows):
            self.clear_tree.insert(
                "",
                END,
                iid=str(index),
                values=(
                    self.format_date_range(row["start_date"], row["end_date"]),
                    row["endpoint"] or "",
                    row["area_code"] or "",
                    row["time96"] or "",
                    row["metric"] or "",
                    self.format_value(row["value"]),
                    row["unit"] or "",
                ),
            )
        self.update_summary("现货价格", rows)
        self.show_first_row(self.clear_tree, rows, self.clear_raw_text)

    def start_clear_price_crawl(self) -> None:
        area_selection = self.clear_area_var.get().strip()
        start_date = self.clear_start_date_var.get().strip()
        end_date = self.clear_end_date_var.get().strip()
        authorization = self.clear_authorization_var.get().strip()

        if not area_selection:
            messagebox.showerror("缺少地区", "请选择地区，或直接输入 area_code。")
            return
        if not start_date or not end_date:
            messagebox.showerror("缺少日期", "请填写开始日期和结束日期，格式为 YYYY-MM-DD。")
            return

        try:
            targets = self.build_clear_price_crawl_targets(area_selection, start_date, end_date)
        except ValueError as exc:
            messagebox.showerror("地区错误", str(exc))
            return

        self.begin_clear_price_crawl(targets, start_date, end_date, authorization)

    def start_clear_price_full_coverage_crawl(self) -> None:
        end_date = self.clear_end_date_var.get().strip()
        authorization = self.clear_authorization_var.get().strip()
        if not end_date:
            messagebox.showerror("缺少日期", "请填写结束日期，格式为 YYYY-MM-DD。")
            return
        latest_dates = self.repository.clear_price_latest_daily_dates_by_area()
        is_update = bool(latest_dates)
        action_text = "更新全部数据" if is_update else "爬取全部数据"
        confirm_message = (
            "将从各地区已采集到的最新日期往前一天开始，逐日更新到当前选择的结束日期。\n\n"
            if is_update
            else "将按照每个地区的最早可用日期，逐日采集到当前选择的结束日期。\n\n"
        )
        if not messagebox.askyesno(
            f"确认{action_text}",
            f"{confirm_message}结束日期：{end_date}\n\n确认继续吗？",
        ):
            return

        try:
            targets = self.build_clear_price_full_coverage_targets(
                end_date,
                latest_dates=latest_dates,
            )
        except ValueError as exc:
            messagebox.showerror("全量采集错误", str(exc))
            return

        detail_override = (
            f"更新全部数据：从各地区已采最新日期往前一天采集至 {end_date}"
            if is_update
            else f"爬取全部数据：按各地区最早可用日期采集至 {end_date}"
        )
        self.begin_clear_price_crawl(targets, "", end_date, authorization, detail_override)

    def begin_clear_price_crawl(
        self,
        targets: list[dict[str, str]],
        start_date: str,
        end_date: str,
        authorization: str,
        detail_override: str | None = None,
    ) -> None:
        if self.clear_crawl_button is not None:
            self.clear_crawl_button.configure(state="disabled")
        if self.clear_full_crawl_button is not None:
            self.clear_full_crawl_button.configure(state="disabled")
        self.summary_var.set("正在采集现货价格数据...")
        self.show_clear_price_crawl_dialog(targets, start_date, end_date, detail_override)
        self.clear_crawl_pause_event = threading.Event()
        self.clear_crawl_stop_event = threading.Event()
        self.clear_crawl_abandon_event = threading.Event()

        thread = threading.Thread(
            target=self.clear_price_crawl_worker,
            args=(targets, authorization),
            daemon=True,
        )
        thread.start()

    def build_clear_price_crawl_targets(
        self,
        area_selection: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, str]]:
        if date.fromisoformat(start_date) > date.fromisoformat(end_date):
            raise ValueError("开始日期不能晚于结束日期。")

        if area_selection != self.all_areas_label:
            area_code = self.repository.resolve_area_code(area_selection)
            return [
                {
                    "area_name": area_selection,
                    "area_code": area_code,
                    "start_date": start_date,
                    "end_date": end_date,
                }
            ]

        return self.build_clear_price_all_area_range_targets(start_date, end_date)

    def build_clear_price_all_area_range_targets(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, str]]:
        areas = self.repository.clear_price_area_targets()
        if not areas:
            raise ValueError("没有可采集的地区。请先运行 powertrade init-db。")

        targets = []
        for area in areas:
            area_name = str(area["area_name"])
            area_code = str(area["area_code"])
            targets.append(
                {
                    "area_name": area_name,
                    "area_code": area_code,
                    "start_date": start_date,
                    "end_date": end_date,
                }
            )
        return targets

    def build_clear_price_full_coverage_targets(
        self,
        end_date: str,
        *,
        latest_dates: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        return build_shared_clear_price_targets(
            self.repository.db_path,
            end_date,
            latest_dates=latest_dates,
        )

    def show_clear_price_crawl_dialog(
        self,
        targets: list[dict[str, str]],
        start_date: str,
        end_date: str,
        detail_override: str | None = None,
    ) -> None:
        if self.clear_crawl_dialog is not None and self.clear_crawl_dialog.winfo_exists():
            self.clear_crawl_dialog.destroy()

        dialog = Toplevel(self.root)
        dialog.title("正在采集现货价格")
        dialog.minsize(520, 230)
        dialog.resizable(True, True)
        dialog.protocol("WM_DELETE_WINDOW", self.abandon_clear_price_crawl)

        if len(targets) == 1:
            label = f"{targets[0]['area_name']} {targets[0]['area_code']}"
        else:
            label = f"全部地区（{len(targets)} 个）"
        self.clear_crawl_message_var.set(f"正在采集 {label} 的现货价格数据...")
        if detail_override is not None:
            detail = detail_override
        elif len(targets) == 1:
            detail = f"日期范围：{targets[0]['start_date']} 至 {targets[0]['end_date']}"
        elif len({target["start_date"] for target in targets}) == 1:
            detail = f"全部地区日期范围：{start_date} 至 {end_date}"
        else:
            detail = f"全部地区全覆盖：按各地区最早可用日期采集至 {end_date}"
        self.clear_crawl_detail_var.set(detail)
        self.clear_crawl_progress_var.set(0.0)
        ttk.Label(dialog, textvariable=self.clear_crawl_message_var, wraplength=460).pack(
            side=TOP,
            fill=X,
            padx=18,
            pady=(18, 6),
        )
        ttk.Label(dialog, textvariable=self.clear_crawl_detail_var, wraplength=480).pack(
            side=TOP,
            fill=X,
            padx=18,
            pady=(0, 8),
        )
        ttk.Progressbar(
            dialog,
            mode="determinate",
            maximum=100,
            variable=self.clear_crawl_progress_var,
        ).pack(fill=X, padx=18, pady=(0, 14))

        button_bar = ttk.Frame(dialog)
        button_bar.pack(fill=X, padx=18, pady=(0, 14))
        self.clear_crawl_pause_button = ttk.Button(
            button_bar,
            text="暂停",
            command=self.toggle_clear_price_crawl_pause,
        )
        self.clear_crawl_pause_button.pack(side=LEFT)
        ttk.Button(button_bar, text="结束并保存", command=self.stop_clear_price_crawl).pack(
            side=LEFT,
            padx=(8, 0),
        )
        ttk.Button(button_bar, text="放弃采集", command=self.abandon_clear_price_crawl).pack(
            side=LEFT,
            padx=(8, 0),
        )
        ttk.Label(button_bar, text="可最小化窗口，采集会继续。").pack(side=RIGHT)

        self.clear_crawl_dialog = dialog

    def toggle_clear_price_crawl_pause(self) -> None:
        if self.clear_crawl_pause_event is None:
            return
        if self.clear_crawl_pause_event.is_set():
            self.clear_crawl_pause_event.clear()
            if self.clear_crawl_pause_button is not None:
                self.clear_crawl_pause_button.configure(text="暂停")
            self.update_clear_price_crawl_progress(message="继续采集")
        else:
            self.clear_crawl_pause_event.set()
            if self.clear_crawl_pause_button is not None:
                self.clear_crawl_pause_button.configure(text="继续")
            self.update_clear_price_crawl_progress(message="采集已暂停")

    def stop_clear_price_crawl(self) -> None:
        if self.clear_crawl_stop_event is not None:
            self.clear_crawl_stop_event.set()
        self.update_clear_price_crawl_progress(message="正在结束采集，将保存已采集数据...")

    def abandon_clear_price_crawl(self) -> None:
        if self.clear_crawl_abandon_event is not None:
            self.clear_crawl_abandon_event.set()
        if self.clear_crawl_stop_event is not None:
            self.clear_crawl_stop_event.set()
        self.update_clear_price_crawl_progress(message="正在放弃后续采集，已写入数据会保留...")

    def update_clear_price_crawl_progress(
        self,
        *,
        message: str,
        detail: str | None = None,
        percent: float | None = None,
    ) -> None:
        self.clear_crawl_message_var.set(message)
        if detail is not None:
            self.clear_crawl_detail_var.set(detail)
        if percent is not None:
            self.clear_crawl_progress_var.set(percent)
        self.summary_var.set(message)

    def clear_price_crawl_worker(
        self,
        targets: list[dict[str, str]],
        authorization: str,
    ) -> None:
        record_count = 0
        written = 0
        try:
            pause_event = self.clear_crawl_pause_event
            stop_event = self.clear_crawl_stop_event
            abandon_event = self.clear_crawl_abandon_event
            resolved_authorization = resolve_elecheck_authorization(authorization)
            if resolved_authorization:
                if authorization:
                    cache_elecheck_authorization(resolved_authorization)
                set_elecheck_authorization_for_current_process(resolved_authorization)
            client = ElecheckClient(authorization=resolved_authorization)
            area_jobs = []
            for target in targets:
                spider = ElecheckClearPriceSpider(
                    client=client,
                    area_code=target["area_code"],
                    start_date=target["start_date"],
                    end_date=target["end_date"],
                    daily=True,
                )
                area_jobs.append((target, spider, list(spider.iter_request_date_ranges())))

            total_ranges = sum(len(request_ranges) for _target, _spider, request_ranges in area_jobs)
            completed_ranges = 0
            stopped_early = False

            for area_index, (target, spider, request_ranges) in enumerate(area_jobs, start=1):
                area_label = f"{target['area_name']} {target['area_code']}"
                if stop_event is not None and stop_event.is_set():
                    stopped_early = True
                    break

                for request_start_date, request_end_date in request_ranges:
                    while pause_event is not None and pause_event.is_set():
                        if abandon_event is not None and abandon_event.is_set():
                            break
                        sleep(0.2)
                    if abandon_event is not None and abandon_event.is_set():
                        stopped_early = True
                        break

                    date_label = self.format_date_range(
                        request_start_date.isoformat(),
                        request_end_date.isoformat(),
                    )
                    self.ui.post(
                        self.update_clear_price_crawl_progress,
                        message=f"正在采集 {area_label}",
                        detail=(
                            f"地区 {area_index}/{len(area_jobs)} | 日期 {date_label} | "
                            f"进度 {completed_ranges}/{total_ranges} | "
                            f"已抓取 {record_count} 条 | 已写入 {written} 条"
                        ),
                        percent=(completed_ranges / total_ranges * 100) if total_ranges else 0,
                    )

                    records = list(
                        spider.crawl_date_range(
                            request_start_date=request_start_date,
                            request_end_date=request_end_date,
                        )
                    )
                    record_count += len(records)
                    if abandon_event is not None and abandon_event.is_set():
                        stopped_early = True
                        break

                    written += upsert_elecheck_clear_price_records(records)
                    completed_ranges += 1
                    self.ui.post(
                        self.update_clear_price_crawl_progress,
                        message="现货价格采集中...",
                        detail=(
                            f"地区 {area_index}/{len(area_jobs)} | "
                            f"进度 {completed_ranges}/{total_ranges} | "
                            f"已抓取 {record_count} 条 | 已写入 {written} 条"
                        ),
                        percent=(completed_ranges / total_ranges * 100) if total_ranges else 100,
                    )

                    if stop_event is not None and stop_event.is_set():
                        stopped_early = True
                        break

                if abandon_event is not None and abandon_event.is_set():
                    break
                if stop_event is not None and stop_event.is_set():
                    break

            client.close()
            if abandon_event is not None and abandon_event.is_set():
                self.ui.post(self.on_clear_price_crawl_abandoned, record_count, written)
                return

        except ElecheckUnauthorizedError as exc:
            message = str(exc)
            self.ui.post(self.on_clear_price_unauthorized, record_count, written, message)
        except Exception as exc:
            message = str(exc)
            self.ui.post(self.on_clear_price_crawl_error, message, record_count, written)
        else:
            self.ui.post(
                self.on_clear_price_crawl_success,
                record_count,
                written,
                stopped_early,
            )

    def on_clear_price_unauthorized(
        self,
        record_count: int = 0,
        written: int = 0,
        message: str | None = None,
    ) -> None:
        self.finish_clear_price_crawl()
        detail = message or (
            "Elecheck 凭据无效或已过期。请在 API 配置向导中重新配置并验证凭据。"
        )
        self.on_clear_price_crawl_error(detail, record_count, written, finish=False)

    def on_clear_price_crawl_error(
        self,
        message: str,
        record_count: int = 0,
        written: int = 0,
        *,
        finish: bool = True,
    ) -> None:
        if finish:
            self.finish_clear_price_crawl()
        data_note = (
            f"失败前已抓取 {record_count} 条、写入/更新 {written} 条；请刷新页面核对已保存数据。"
            if written
            else "本次没有修改业务数据。"
        )
        messagebox.showerror("现货价格采集失败", f"{message}\n\n{data_note}")
        self.summary_var.set(f"现货价格采集失败：{data_note}")

    def on_clear_price_crawl_success(
        self,
        record_count: int,
        written: int,
        stopped_early: bool = False,
    ) -> None:
        self.finish_clear_price_crawl()
        self.load_filter_values()
        self.refresh_clear_price()
        if self.clear_price_dashboard is not None:
            self.clear_price_dashboard.refresh_after_crawl(select_latest=True)
        if record_count == 0:
            self.summary_var.set("现货价格采集请求已完成：所选范围无新数据，数据库未修改。")
            messagebox.showinfo(
                "无新数据",
                "所选地区和日期范围没有返回记录。请求执行成功，数据库未修改。",
            )
            return
        status = "已结束并保存" if stopped_early else "采集完成"
        self.summary_var.set(f"现货价格{status}：抓取 {record_count} 条，写入/更新 {written} 条。")
        messagebox.showinfo(
            status,
            f"现货价格{status}。\n\n抓取记录：{record_count}\n写入/更新：{written}",
        )

    def on_clear_price_crawl_abandoned(self, record_count: int, written: int) -> None:
        self.finish_clear_price_crawl()
        self.load_filter_values()
        self.refresh_clear_price()
        if self.clear_price_dashboard is not None:
            self.clear_price_dashboard.refresh_after_crawl(select_latest=True)
        self.summary_var.set(
            f"已放弃后续采集：本次抓取 {record_count} 条，已写入/更新 {written} 条。"
        )
        messagebox.showinfo(
            "已放弃采集",
            (
                "已停止后续现货价格采集。\n\n"
                f"本次抓取记录：{record_count}\n"
                f"已写入/更新：{written}"
            ),
        )

    def finish_clear_price_crawl(self) -> None:
        if self.clear_crawl_button is not None:
            self.clear_crawl_button.configure(state="normal")
        if self.clear_full_crawl_button is not None:
            self.clear_full_crawl_button.configure(state="normal")
        if self.clear_crawl_dialog is not None and self.clear_crawl_dialog.winfo_exists():
            self.clear_crawl_dialog.destroy()
        self.clear_crawl_dialog = None
        self.clear_crawl_pause_button = None
        self.clear_crawl_pause_event = None
        self.clear_crawl_stop_event = None
        self.clear_crawl_abandon_event = None

    def refresh_purchasing(self) -> None:
        rows = self.repository.search_purchasing(
            data_month=self.purchasing_month_var.get().strip(),
            province_name=self.purchasing_province_var.get().strip(),
            data_kind=self.purchasing_kind_var.get().strip(),
            metric=self.purchasing_metric_var.get().strip(),
        )
        self.result_rows["purchasing"] = rows
        self.purchasing_tree.delete(*self.purchasing_tree.get_children())
        for index, row in enumerate(rows):
            self.purchasing_tree.insert(
                "",
                END,
                iid=str(index),
                values=(
                    row["data_month"] or "",
                    row["province_name"] or "",
                    row["data_kind"] or "",
                    row["metric"] or "",
                    self.format_value(row["value"]),
                    row["statistic"] or "",
                    row["related_province_name"] or "",
                ),
            )
        self.update_summary("代理购电价格", rows)
        self.show_first_row(self.purchasing_tree, rows, self.purchasing_raw_text)

    def refresh_mechanism(self) -> None:
        rows = self.repository.search_mechanism(
            region_name=self.mechanism_region_var.get().strip(),
            category=self.mechanism_category_var.get().strip(),
        )
        self.result_rows["mechanism"] = rows
        self.mechanism_tree.delete(*self.mechanism_tree.get_children())
        for index, row in enumerate(rows):
            self.mechanism_tree.insert(
                "",
                END,
                iid=str(index),
                values=(
                    row["region_name"] or "",
                    row["region"] or "",
                    row["category"] or "",
                    self.format_value(row["price"]),
                    self.format_value(row["clear_price"]),
                    row["unit"] or "",
                ),
            )
        self.update_summary("增量机制电价", rows)
        self.show_first_row(self.mechanism_tree, rows, self.mechanism_raw_text)

    def reset_clear_price(self) -> None:
        self.clear_filter_area_var.set("")
        self.clear_filter_endpoint_var.set("")
        self.clear_filter_metric_var.set("")
        self.clear_filter_time96_var.set("")
        self.clear_filter_start_date_var.set("")
        self.clear_filter_end_date_var.set("")
        self.refresh_clear_price()

    def clear_all_clear_price_records(self) -> None:
        if not messagebox.askyesno(
            "确认清空",
            "将删除 elecheck_clear_price_records 中的所有现货价格数据。\n\n确认继续吗？",
        ):
            return

        try:
            deleted = self.repository.delete_clear_price_records()
        except Exception as exc:
            messagebox.showerror("清空失败", str(exc))
            self.summary_var.set("清空现货价格数据失败。")
            return

        self.load_filter_values()
        self.refresh_clear_price()
        if self.clear_price_dashboard is not None:
            self.clear_price_dashboard.refresh_after_crawl(select_latest=True)
        self.summary_var.set(f"已清空现货价格数据，删除 {deleted} 条记录。")
        messagebox.showinfo("清空完成", f"已删除 {deleted} 条现货价格记录。")

    def start_purchasing_collect_all(self) -> None:
        if not messagebox.askyesno(
            "确认采集",
            (
                "将从 2024-02 起采集全国代理购电价格月度大表，直到当前最新可用月份。\n\n"
                "重复采集到相同数据时会更新原记录，不会插入重复记录。\n\n确认继续吗？"
            ),
        ):
            return

        if self.purchasing_collect_button is not None:
            self.purchasing_collect_button.configure(state="disabled")
        self.show_purchasing_collect_dialog()
        self.summary_var.set("正在采集代理购电价格数据...")
        thread = threading.Thread(
            target=self.purchasing_collect_all_worker,
            args=(self.purchasing_authorization_var.get().strip(),),
            daemon=True,
        )
        thread.start()

    def show_purchasing_collect_dialog(self) -> None:
        if (
            self.purchasing_collect_dialog is not None
            and self.purchasing_collect_dialog.winfo_exists()
        ):
            self.purchasing_collect_dialog.destroy()

        dialog = Toplevel(self.root)
        dialog.title("正在采集代理购电价格")
        dialog.minsize(520, 190)
        dialog.resizable(True, True)
        dialog.protocol("WM_DELETE_WINDOW", dialog.iconify)
        self.purchasing_collect_message_var.set("正在准备采集代理购电价格数据...")
        self.purchasing_collect_detail_var.set("范围：2024-02 至最新可用月份")
        self.purchasing_collect_progress_var.set(0.0)
        ttk.Label(dialog, textvariable=self.purchasing_collect_message_var, wraplength=460).pack(
            fill=X,
            padx=18,
            pady=(18, 8),
        )
        ttk.Label(dialog, textvariable=self.purchasing_collect_detail_var, wraplength=480).pack(
            fill=X,
            padx=18,
            pady=(0, 12),
        )
        ttk.Progressbar(
            dialog,
            mode="determinate",
            maximum=100,
            variable=self.purchasing_collect_progress_var,
        ).pack(fill=X, padx=18, pady=(0, 14))
        ttk.Label(dialog, text="可最小化窗口，采集会继续。").pack(anchor=W, padx=18)
        self.purchasing_collect_dialog = dialog

    def update_purchasing_collect_progress(
        self,
        *,
        message: str,
        detail: str | None = None,
        percent: float | None = None,
    ) -> None:
        self.purchasing_collect_message_var.set(message)
        if detail is not None:
            self.purchasing_collect_detail_var.set(detail)
        if percent is not None:
            self.purchasing_collect_progress_var.set(percent)
        self.summary_var.set(message)

    def purchasing_collect_all_worker(self, authorization: str) -> None:
        client = None
        record_count = 0
        written = 0
        try:
            resolved_authorization = resolve_elecheck_authorization(authorization)
            if resolved_authorization:
                if authorization:
                    cache_elecheck_authorization(resolved_authorization)
                set_elecheck_authorization_for_current_process(resolved_authorization)
            client = ElecheckClient(authorization=resolved_authorization)
            spider = ElecheckPurchasingNationalRangeSpider(client=client)
            months = list(spider.iter_months(spider.start_month, spider.end_month))
            total_months = len(months)
            for month_index, data_month in enumerate(months, start=1):
                self.ui.post(
                    self.update_purchasing_collect_progress,
                    message=f"正在采集代理购电价格 {data_month}",
                    detail=(
                        f"月份 {month_index}/{total_months} | 已抓取 {record_count} 条 | "
                        f"已写入/更新 {written} 条"
                    ),
                    percent=((month_index - 1) / total_months * 100) if total_months else 0,
                )
                data = client.fetch_purchasing_list(
                    province="",
                    latest_data_month=data_month,
                )
                latest_data_month = str(data.get("latestDataMonth") or data_month)
                records = []
                for row in data.get("tableDataList") or []:
                    records.extend(spider.table_row_to_records(row, latest_data_month))
                for row in data.get("topDataList") or []:
                    records.extend(spider.top_row_to_records(row, latest_data_month))

                record_count += len(records)
                written += upsert_elecheck_purchasing_records(records)
                self.ui.post(
                    self.update_purchasing_collect_progress,
                    message=f"代理购电价格采集中：已完成 {data_month}",
                    detail=(
                        f"月份 {month_index}/{total_months} | 已抓取 {record_count} 条 | "
                        f"已写入/更新 {written} 条"
                    ),
                    percent=(month_index / total_months * 100) if total_months else 100,
                )
        except ElecheckUnauthorizedError as exc:
            message = str(exc)
            self.ui.post(self.on_purchasing_collect_error, message, record_count, written)
        except Exception as exc:
            message = str(exc)
            self.ui.post(self.on_purchasing_collect_error, message, record_count, written)
        else:
            self.ui.post(self.on_purchasing_collect_success, record_count, written)
        finally:
            if client is not None:
                client.close()

    def on_purchasing_collect_error(
        self,
        message: str,
        record_count: int = 0,
        written: int = 0,
    ) -> None:
        self.finish_purchasing_collect()
        data_note = (
            f"失败前已抓取 {record_count} 条、写入/更新 {written} 条；请刷新页面核对已保存数据。"
            if written
            else "本次没有修改业务数据。"
        )
        messagebox.showerror("代理购电价格采集失败", f"{message}\n\n{data_note}")
        self.summary_var.set(f"代理购电价格采集失败：{data_note}")

    def on_purchasing_collect_success(self, record_count: int, written: int) -> None:
        self.finish_purchasing_collect()
        self.load_filter_values()
        self.refresh_purchasing()
        if self.purchasing_dashboard is not None:
            self.purchasing_dashboard.refresh_after_crawl(select_latest=True)
        if record_count == 0:
            self.summary_var.set("代理购电采集请求已完成：所选月份无新数据，数据库未修改。")
            messagebox.showinfo(
                "无新数据",
                "所选月份没有返回代理购电记录。请求执行成功，数据库未修改。",
            )
            return
        self.summary_var.set(
            f"代理购电价格采集完成：抓取 {record_count} 条，写入/更新 {written} 条。"
        )
        messagebox.showinfo(
            "采集完成",
            f"代理购电价格采集完成。\n\n抓取记录：{record_count}\n写入/更新：{written}",
        )

    def finish_purchasing_collect(self) -> None:
        if self.purchasing_collect_button is not None:
            self.purchasing_collect_button.configure(state="normal")
        if (
            self.purchasing_collect_dialog is not None
            and self.purchasing_collect_dialog.winfo_exists()
        ):
            self.purchasing_collect_dialog.destroy()
        self.purchasing_collect_dialog = None

    def clear_all_purchasing_records(self) -> None:
        if not messagebox.askyesno(
            "确认清空",
            "将删除 elecheck_purchasing_records 中的所有代理购电价格数据。\n\n确认继续吗？",
        ):
            return

        try:
            deleted = self.repository.delete_purchasing_records()
        except Exception as exc:
            messagebox.showerror("清空失败", str(exc))
            self.summary_var.set("清空代理购电价格数据失败。")
            return

        self.load_filter_values()
        self.refresh_purchasing()
        if self.purchasing_dashboard is not None:
            self.purchasing_dashboard.refresh_after_crawl(select_latest=True)
        self.summary_var.set(f"已清空代理购电价格数据，删除 {deleted} 条记录。")
        messagebox.showinfo("清空完成", f"已删除 {deleted} 条代理购电价格记录。")

    def start_mechanism_collect_all(self) -> None:
        if not messagebox.askyesno(
            "确认采集",
            (
                "将采集全部增量机制电价数据，并写入本地数据库。\n\n"
                "重复采集到相同地区、相同电源类型的数据时会更新原记录。\n\n确认继续吗？"
            ),
        ):
            return

        if self.mechanism_collect_button is not None:
            self.mechanism_collect_button.configure(state="disabled")
        self.show_mechanism_collect_dialog()
        self.summary_var.set("正在采集增量机制电价数据...")
        thread = threading.Thread(target=self.mechanism_collect_all_worker, daemon=True)
        thread.start()

    def show_mechanism_collect_dialog(self) -> None:
        if (
            self.mechanism_collect_dialog is not None
            and self.mechanism_collect_dialog.winfo_exists()
        ):
            self.mechanism_collect_dialog.destroy()

        dialog = Toplevel(self.root)
        dialog.title("正在采集增量机制电价")
        dialog.minsize(520, 190)
        dialog.resizable(True, True)
        dialog.protocol("WM_DELETE_WINDOW", dialog.iconify)
        self.mechanism_collect_message_var.set("正在准备采集增量机制电价数据...")
        self.mechanism_collect_detail_var.set("范围：全部地区、全部电源类型")
        self.mechanism_collect_progress_var.set(0.0)
        ttk.Label(dialog, textvariable=self.mechanism_collect_message_var, wraplength=460).pack(
            fill=X,
            padx=18,
            pady=(18, 8),
        )
        ttk.Label(dialog, textvariable=self.mechanism_collect_detail_var, wraplength=480).pack(
            fill=X,
            padx=18,
            pady=(0, 12),
        )
        ttk.Progressbar(
            dialog,
            mode="determinate",
            maximum=100,
            variable=self.mechanism_collect_progress_var,
        ).pack(fill=X, padx=18, pady=(0, 14))
        ttk.Label(dialog, text="可最小化窗口，采集会继续。").pack(anchor=W, padx=18)
        self.mechanism_collect_dialog = dialog

    def update_mechanism_collect_progress(
        self,
        *,
        message: str,
        detail: str | None = None,
        percent: float | None = None,
    ) -> None:
        self.mechanism_collect_message_var.set(message)
        if detail is not None:
            self.mechanism_collect_detail_var.set(detail)
        if percent is not None:
            self.mechanism_collect_progress_var.set(percent)
        self.summary_var.set(message)

    def mechanism_collect_all_worker(self) -> None:
        client = None
        try:
            resolved_authorization = resolve_elecheck_authorization("")
            if resolved_authorization:
                set_elecheck_authorization_for_current_process(resolved_authorization)
            client = ElecheckClient(authorization=resolved_authorization)
            spider = ElecheckMechanismElectricityPriceSpider(client=client)
            self.ui.post(
                self.update_mechanism_collect_progress,
                message="正在请求增量机制电价接口...",
                detail="正在从 Elecheck 获取全部列表",
                percent=20,
            )
            records = list(spider.crawl())
            self.ui.post(
                self.update_mechanism_collect_progress,
                message="正在写入增量机制电价数据...",
                detail=f"已抓取 {len(records)} 条，正在写入/更新数据库",
                percent=70,
            )
            written = upsert_elecheck_mechanism_electricity_price_records(records)
        except ElecheckUnauthorizedError as exc:
            message = str(exc)
            self.ui.post(self.on_mechanism_collect_error, message)
        except Exception as exc:
            message = str(exc)
            self.ui.post(self.on_mechanism_collect_error, message)
        else:
            self.ui.post(self.on_mechanism_collect_success, len(records), written)
        finally:
            if client is not None:
                client.close()

    def on_mechanism_collect_error(self, message: str) -> None:
        self.finish_mechanism_collect()
        messagebox.showerror(
            "增量机制电价采集失败",
            f"{message}\n\n本次没有修改业务数据。",
        )
        self.summary_var.set("增量机制电价采集失败：本次没有修改业务数据。")

    def on_mechanism_collect_success(self, record_count: int, written: int) -> None:
        self.finish_mechanism_collect()
        self.load_filter_values()
        self.refresh_mechanism()
        if self.mechanism_dashboard is not None:
            self.mechanism_dashboard.refresh_after_crawl()
        if record_count == 0:
            self.summary_var.set("增量机制电价采集请求已完成：当前无新数据，数据库未修改。")
            messagebox.showinfo(
                "无新数据",
                "接口当前没有返回增量机制电价记录。请求执行成功，数据库未修改。",
            )
            return
        self.summary_var.set(
            f"增量机制电价采集完成：抓取 {record_count} 条，写入/更新 {written} 条。"
        )
        messagebox.showinfo(
            "采集完成",
            f"增量机制电价采集完成。\n\n抓取记录：{record_count}\n写入/更新：{written}",
        )

    def finish_mechanism_collect(self) -> None:
        if self.mechanism_collect_button is not None:
            self.mechanism_collect_button.configure(state="normal")
        if (
            self.mechanism_collect_dialog is not None
            and self.mechanism_collect_dialog.winfo_exists()
        ):
            self.mechanism_collect_dialog.destroy()
        self.mechanism_collect_dialog = None

    def clear_all_mechanism_records(self) -> None:
        if not messagebox.askyesno(
            "确认清空",
            (
                "将删除 elecheck_mechanism_electricity_price_records "
                "中的所有增量机制电价数据。\n\n确认继续吗？"
            ),
        ):
            return

        try:
            deleted = self.repository.delete_mechanism_records()
        except Exception as exc:
            messagebox.showerror("清空失败", str(exc))
            self.summary_var.set("清空增量机制电价数据失败。")
            return

        self.load_filter_values()
        self.refresh_mechanism()
        if self.mechanism_dashboard is not None:
            self.mechanism_dashboard.refresh_after_crawl()
        self.summary_var.set(f"已清空增量机制电价数据，删除 {deleted} 条记录。")
        messagebox.showinfo("清空完成", f"已删除 {deleted} 条增量机制电价记录。")

    def reset_purchasing(self) -> None:
        self.purchasing_month_var.set("")
        self.purchasing_province_var.set("")
        self.purchasing_kind_var.set("")
        self.purchasing_metric_var.set("")
        self.refresh_purchasing()

    def reset_mechanism(self) -> None:
        self.mechanism_region_var.set("")
        self.mechanism_category_var.set("")
        self.refresh_mechanism()

    def on_select_clear_price(self, _event) -> None:
        self.show_selected_raw(self.clear_tree, self.result_rows["clear"], self.clear_raw_text)

    def on_select_purchasing(self, _event) -> None:
        self.show_selected_raw(self.purchasing_tree, self.result_rows["purchasing"], self.purchasing_raw_text)

    def on_select_mechanism(self, _event) -> None:
        self.show_selected_raw(self.mechanism_tree, self.result_rows["mechanism"], self.mechanism_raw_text)

    def show_first_row(self, tree: ttk.Treeview, rows: list[sqlite3.Row], raw_text) -> None:
        if not rows:
            self.set_text(raw_text, "没有匹配数据。")
            return
        tree.selection_set("0")
        tree.focus("0")
        self.set_text(raw_text, self.describe_row(rows[0]))

    def show_selected_raw(self, tree: ttk.Treeview, rows: list[sqlite3.Row], raw_text) -> None:
        selection = tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(rows):
            self.set_text(raw_text, self.describe_row(rows[index]))

    def describe_row(self, row: sqlite3.Row) -> str:
        data = dict(row)
        raw_json = data.pop("raw_json", None)
        lines = [f"{key}: {value if value not in (None, '') else '-'}" for key, value in data.items()]
        lines.extend(["", "raw_json", self.format_json(raw_json)])
        return "\n".join(lines)

    def export_rows(self, key: str) -> None:
        output_path = asksaveasfilename(
            title="导出 Elecheck CSV",
            initialfile=f"elecheck_{key}.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not output_path:
            return

        try:
            export_spec = self.build_export_spec(key)
        except ValueError as exc:
            messagebox.showerror("导出失败", str(exc))
            return

        self.show_elecheck_export_dialog(output_path)
        thread = threading.Thread(
            target=self.export_rows_worker,
            args=(Path(output_path), export_spec),
            daemon=True,
        )
        thread.start()

    def build_export_spec(self, key: str) -> dict[str, object]:
        if key == "clear":
            area_code = ""
            area_selection = self.clear_filter_area_var.get().strip()
            if area_selection and area_selection != self.all_areas_label:
                area_code = self.repository.resolve_area_code(area_selection)
            where = []
            params: list[object] = []
            if area_code:
                where.append("area_code = ?")
                params.append(area_code)
            if self.clear_filter_endpoint_var.get().strip():
                where.append("endpoint = ?")
                params.append(self.clear_filter_endpoint_var.get().strip())
            if self.clear_filter_metric_var.get().strip():
                where.append("metric = ?")
                params.append(self.clear_filter_metric_var.get().strip())
            if self.clear_filter_time96_var.get().strip():
                where.append("time96 = ?")
                params.append(self.clear_filter_time96_var.get().strip())
            if self.clear_filter_start_date_var.get().strip():
                where.append("start_date >= ?")
                params.append(self.clear_filter_start_date_var.get().strip())
            if self.clear_filter_end_date_var.get().strip():
                where.append("start_date <= ?")
                params.append(self.clear_filter_end_date_var.get().strip())
            return {
                "table_name": "elecheck_clear_price_records",
                "columns": [
                    "id",
                    "endpoint",
                    "area_code",
                    "start_date",
                    "end_date",
                    "time96",
                    "metric",
                    "value",
                    "unit",
                    "currency",
                    "collected_at",
                    "raw_json",
                ],
                "where": where,
                "params": params,
                "order_by": "start_date DESC, end_date DESC, endpoint, time96, metric",
            }
        if key == "purchasing":
            where = []
            params = []
            if self.purchasing_month_var.get().strip():
                where.append("data_month = ?")
                params.append(self.purchasing_month_var.get().strip())
            if self.purchasing_province_var.get().strip():
                where.append("province_name = ?")
                params.append(self.purchasing_province_var.get().strip())
            if self.purchasing_kind_var.get().strip():
                where.append("data_kind = ?")
                params.append(self.purchasing_kind_var.get().strip())
            if self.purchasing_metric_var.get().strip():
                where.append("metric = ?")
                params.append(self.purchasing_metric_var.get().strip())
            return {
                "table_name": "elecheck_purchasing_records",
                "columns": [
                    "id",
                    "endpoint",
                    "data_kind",
                    "data_month",
                    "province_name",
                    "metric",
                    "value",
                    "unit",
                    "diff_value",
                    "statistic",
                    "related_province_name",
                    "collected_at",
                    "raw_json",
                ],
                "where": where,
                "params": params,
                "order_by": "data_month DESC, province_name, data_kind, metric, statistic",
            }
        if key == "mechanism":
            where = []
            params = []
            if self.mechanism_region_var.get().strip():
                where.append("region_name = ?")
                params.append(self.mechanism_region_var.get().strip())
            if self.mechanism_category_var.get().strip():
                where.append("category = ?")
                params.append(self.mechanism_category_var.get().strip())
            return {
                "table_name": "elecheck_mechanism_electricity_price_records",
                "columns": [
                    "id",
                    "region_name",
                    "region",
                    "category",
                    "price",
                    "clear_price",
                    "unit",
                    "collected_at",
                    "raw_json",
                ],
                "where": where,
                "params": params,
                "order_by": "region_name, category",
            }
        raise ValueError(f"Unsupported export key: {key}")

    def show_elecheck_export_dialog(self, output_path: str) -> None:
        self.export_dialog = Toplevel(self.root)
        self.export_dialog.title("正在导出 CSV")
        self.export_dialog.minsize(500, 180)
        self.export_dialog.resizable(True, True)
        self.export_message_var = StringVar(value="正在导出全量 CSV...")
        self.export_detail_var = StringVar(value=f"保存到：{output_path}")
        ttk.Label(self.export_dialog, textvariable=self.export_message_var, wraplength=440).pack(
            fill=X,
            padx=18,
            pady=(18, 8),
        )
        ttk.Label(self.export_dialog, textvariable=self.export_detail_var, wraplength=460).pack(
            fill=X,
            padx=18,
            pady=(0, 12),
        )
        ttk.Progressbar(self.export_dialog, mode="indeterminate").pack(fill=X, padx=18)
        ttk.Label(self.export_dialog, text="可最小化窗口，导出会继续。").pack(
            anchor=W,
            padx=18,
            pady=(12, 0),
        )

    def export_rows_worker(self, output_path: Path, export_spec: dict[str, object]) -> None:
        try:
            written = self.repository.export_query_to_csv(
                output_path=output_path,
                table_name=str(export_spec["table_name"]),
                columns=list(export_spec["columns"]),
                where=list(export_spec["where"]),
                params=list(export_spec["params"]),
                order_by=str(export_spec["order_by"]),
                progress=lambda row_count: self.ui.post(
                    self.update_elecheck_export_progress,
                    row_count,
                ),
            )
        except PermissionError:
            self.ui.post(
                self.on_elecheck_export_error,
                (
                    "没有权限写入这个 CSV 文件。\n\n"
                    "请确认目标文件没有被 Excel/WPS 打开，或换一个可写的保存位置后重试。\n\n"
                    f"路径：{output_path}"
                ),
            )
        except OSError as exc:
            self.ui.post(self.on_elecheck_export_error, f"写入 CSV 时出错：\n\n{exc}")
        else:
            self.ui.post(self.on_elecheck_export_success, written, output_path)

    def update_elecheck_export_progress(self, row_count: int) -> None:
        if hasattr(self, "export_message_var"):
            self.export_message_var.set(f"正在导出全量 CSV，已写入 {row_count} 行...")
        self.summary_var.set(f"CSV 导出中：已写入 {row_count} 行。")

    def on_elecheck_export_success(self, row_count: int, output_path: Path) -> None:
        if hasattr(self, "export_dialog") and self.export_dialog.winfo_exists():
            self.export_dialog.destroy()
        self.summary_var.set(f"已导出 {row_count} 条记录到 {output_path}")
        messagebox.showinfo("导出完成", f"已导出 {row_count} 条记录。\n\n{output_path}")

    def on_elecheck_export_error(self, message: str) -> None:
        if hasattr(self, "export_dialog") and self.export_dialog.winfo_exists():
            self.export_dialog.destroy()
        messagebox.showerror("导出失败", message)
        self.summary_var.set("CSV 导出失败。")

    def update_summary(self, section: str, rows: list[sqlite3.Row]) -> None:
        self.summary_var.set(f"{section}：显示 {len(rows)} 条记录。默认最多显示 1000 条。")

    def set_text(self, text_widget, content: str) -> None:
        text_widget.configure(state="normal")
        text_widget.delete("1.0", END)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    @staticmethod
    def format_date_range(start: str | None, end: str | None) -> str:
        if start == end:
            return start or ""
        return f"{start or '?'} -> {end or '?'}"

    @staticmethod
    def format_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)

    @staticmethod
    def format_json(value: str | None) -> str:
        if not value:
            return "{}"
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return value


class EntsoeDataApp:
    parameter_meanings = {
        "documentType": "数据文档/主题，例如 A44 价格、A65 负荷、A75 分类型实际发电。",
        "processType": "数据阶段，例如 A16 实际、A01 日前、A31 周前、A32 月前、A33 年前。",
        "businessType": "时间序列的具体业务含义，部分平衡和备用容量接口需要。",
        "contract_MarketAgreement.Type": "市场合约周期类型，例如 A01 日前。",
        "auction.Type": "拍卖周期类型，例如 A01 日前。",
        "Type_MarketAgreement.Type": "备用容量等接口使用的市场合约周期类型。",
        "in_Domain": "输入或来源区域 EIC。",
        "out_Domain": "输出或目的区域 EIC。",
        "outBiddingZone_Domain": "负荷等单区域接口使用的报价区 EIC。",
        "controlArea_Domain": "平衡市场接口使用的控制区 EIC。",
        "BiddingZone_Domain": "停运和不可用接口使用的报价区 EIC。",
        "psrType": "电源/资源类型，例如 B16 光伏、B18 海上风电、B19 陆上风电。",
        "periodStart": "查询开始时间，按 UTC 发送，格式 yyyyMMddHHmm。",
        "periodEnd": "查询结束时间，按 UTC 发送，结束点不包含在区间内。",
    }

    output_meaning = (
        "返回数据写入 entsoe_records；兼容数据集 entsoe_day_ahead_prices 写入 market_records。\n"
        "通用结果中的 value 是自动识别的主数值，value_field 标明它来自 XML 的哪个字段；"
        "raw_json 保留 document、series、period、point 四层原始上下文。"
    )

    def __init__(self, root, repository: EntsoeDataRepository) -> None:
        self.root = root
        self.ui = UiLifecycle(root)
        self.repository = repository
        self.configs = load_entsoe_request_configs()
        self.config_by_name = {config["name"]: config for config in self.configs}
        self.border_availability = self.load_border_availability()
        self.dataset_options = [
            f"{config['name']} | {config['title_zh']} / {config['title_en']}"
            for config in self.configs
        ]
        self.area_options = ["", ENTSOE_ALL_AREAS_LABEL] + [
            self.format_area_option(alias) for alias in sorted(ENTSOE_BIDDING_ZONES)
        ]
        today = date.today()

        self.dataset_var = StringVar(value=self.dataset_options[0] if self.dataset_options else "")
        self.dataset_count_var = StringVar(value=f"已接入 {len(self.configs)} 个数据集")
        self.token_status_var = StringVar()
        self.mode_hint_var = StringVar()
        self.border_availability_var = StringVar()
        self.area_var = StringVar(value=self.format_area_option("DE-LU"))
        self.in_area_var = StringVar(value=self.format_area_option("FR"))
        self.out_area_var = StringVar(value=self.format_area_option("DE-LU"))
        self.start_date_var = StringVar(value=(today - timedelta(days=1)).isoformat())
        self.end_date_var = StringVar(value=today.isoformat())
        self.psr_type_var = StringVar()
        self.extra_params_var = StringVar()
        self.summary_var = StringVar(value="Ready")
        self.result_rows: list[dict[str, object]] = []
        self.collect_button: ttk.Button | None = None
        self.area_combo: ttk.Combobox | None = None
        self.in_area_combo: ttk.Combobox | None = None
        self.out_area_combo: ttk.Combobox | None = None
        self.collect_dialog: Toplevel | None = None
        self.collect_message_var = StringVar()
        self.collect_detail_var = StringVar()
        self.last_gb_warning_key = ""

        self.build_layout()
        self.refresh_token_status()
        self.update_dataset_description()
        self.refresh_results()

    def build_layout(self) -> None:
        outer = ttk.Frame(
            self.root,
            style="AppSurface.TFrame",
            padding=(12, 10, 12, 8),
        )
        outer.pack(fill=BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        command_card = ttk.Frame(outer, style="Card.TFrame", padding=(14, 10))
        command_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        command_card.columnconfigure(1, weight=1)
        ttk.Label(command_card, text="数据集", style="SectionTitle.TLabel").grid(
            row=0,
            column=0,
            sticky=W,
            padx=(0, 8),
        )
        self.dataset_combo = ttk.Combobox(
            command_card,
            textvariable=self.dataset_var,
            values=self.dataset_options,
            state="readonly",
        )
        self.dataset_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.dataset_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.update_dataset_description(),
        )
        ttk.Label(
            command_card,
            textvariable=self.dataset_count_var,
            style="CardMuted.TLabel",
        ).grid(row=0, column=2, sticky="e", padx=(0, 12))
        ttk.Button(
            command_card,
            text="预览请求",
            command=lambda: (self.content_notebook.select(0), self.preview_request()),
        ).grid(
            row=0,
            column=3,
            padx=(0, 7),
        )
        self.collect_button = ttk.Button(
            command_card,
            text="执行爬取",
            style="Primary.TButton",
            command=self.start_collect,
        )
        self.collect_button.grid(row=0, column=4)

        token_row = ttk.Frame(command_card, style="CardBody.TFrame")
        token_row.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        ttk.Label(token_row, text="ENTSO-E 访问凭据", style="CardMuted.TLabel").pack(side=LEFT)
        ttk.Label(token_row, textvariable=self.token_status_var, style="Success.TLabel").pack(
            side=LEFT,
            padx=(7, 8),
        )
        ttk.Button(
            token_row,
            text="刷新凭据状态",
            style="Link.TButton",
            command=self.refresh_token_status,
        ).pack(side=LEFT)
        ttk.Label(
            token_row,
            text="选择条件 → 预览请求 → 执行爬取",
            style="CardMuted.TLabel",
        ).pack(side=RIGHT)

        workspace = ttk.PanedWindow(outer, orient="horizontal")
        workspace.grid(row=1, column=0, sticky="nsew")
        settings = ttk.LabelFrame(workspace, text="采集条件", width=380, padding=(12, 10))
        settings.grid_propagate(False)
        settings.columnconfigure(0, weight=1)
        settings.rowconfigure(0, weight=1)
        content = ttk.Frame(workspace, style="AppSurface.TFrame")
        workspace.add(settings, weight=0)
        workspace.add(content, weight=1)

        condition_tabs = ttk.Notebook(settings)
        condition_tabs.grid(row=0, column=0, sticky="nsew")
        basic_tab = ttk.Frame(condition_tabs, padding=(10, 10))
        advanced_tab = ttk.Frame(condition_tabs, padding=(10, 10))
        condition_tabs.add(basic_tab, text="区域与时间")
        condition_tabs.add(advanced_tab, text="高级参数")
        basic_tab.columnconfigure(1, weight=1)

        ttk.Label(basic_tab, text="区域模式", style="CardMuted.TLabel").grid(
            row=0,
            column=0,
            sticky=W,
            pady=(0, 6),
        )
        ttk.Label(basic_tab, textvariable=self.mode_hint_var, style="SectionTitle.TLabel").grid(
            row=0,
            column=1,
            sticky=W,
            pady=(0, 6),
        )

        def add_area_field(row: int, label: str, widget: ttk.Combobox) -> ttk.Label:
            label_widget = ttk.Label(basic_tab, text=label)
            label_widget.grid(row=row, column=0, sticky=W, pady=4, padx=(0, 8))
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            return label_widget

        self.area_combo = ttk.Combobox(
            basic_tab,
            textvariable=self.area_var,
            values=self.area_options,
            style="Compact.TCombobox",
        )
        self.area_field_label = add_area_field(1, "区域", self.area_combo)
        self.area_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.maybe_warn_gb_after_publication_stop(),
        )
        self.area_combo.bind(
            "<FocusOut>",
            lambda _event: self.maybe_warn_gb_after_publication_stop(),
        )

        self.in_area_combo = ttk.Combobox(
            basic_tab,
            textvariable=self.in_area_var,
            values=self.area_options,
            style="Compact.TCombobox",
        )
        self.in_area_field_label = add_area_field(2, "来源区域", self.in_area_combo)
        self.in_area_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.on_border_area_changed(changed="in"),
        )
        self.in_area_combo.bind(
            "<FocusOut>",
            lambda _event: self.on_border_area_changed(changed="in"),
        )

        self.out_area_combo = ttk.Combobox(
            basic_tab,
            textvariable=self.out_area_var,
            values=self.area_options,
            style="Compact.TCombobox",
        )
        self.out_area_field_label = add_area_field(3, "目标区域", self.out_area_combo)
        self.out_area_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.on_border_area_changed(changed="out"),
        )
        self.out_area_combo.bind(
            "<FocusOut>",
            lambda _event: self.on_border_area_changed(changed="out"),
        )
        self.border_status_label = ttk.Label(
            basic_tab,
            textvariable=self.border_availability_var,
            style="CardMuted.TLabel",
            wraplength=260,
        )
        self.border_status_label.grid(
            row=4,
            column=0,
            columnspan=2,
            sticky=W,
            pady=(2, 10),
        )

        ttk.Separator(basic_tab).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 9))
        ttk.Label(basic_tab, text="查询时间", style="SectionTitle.TLabel").grid(
            row=6,
            column=0,
            columnspan=2,
            sticky=W,
            pady=(0, 4),
        )

        def add_date_field(row: int, label: str, variable: StringVar) -> None:
            ttk.Label(basic_tab, text=label).grid(row=row, column=0, sticky=W, pady=4, padx=(0, 8))
            date_row = ttk.Frame(basic_tab)
            date_row.grid(row=row, column=1, sticky="ew", pady=4)
            date_row.columnconfigure(0, weight=1)
            ttk.Entry(
                date_row,
                textvariable=variable,
                state="readonly",
                width=10,
                style="Compact.TEntry",
            ).grid(
                row=0,
                column=0,
                sticky="ew",
                padx=(0, 5),
            )
            ttk.Button(
                date_row,
                text="选择",
                width=5,
                style="Compact.TButton",
                command=lambda: self.pick_date(variable),
            ).grid(
                row=0,
                column=1,
            )

        add_date_field(7, "开始", self.start_date_var)
        add_date_field(8, "结束", self.end_date_var)

        advanced_tab.columnconfigure(0, weight=1)
        ttk.Label(advanced_tab, text="电源类型 psrType", style="SectionTitle.TLabel").grid(
            row=0,
            column=0,
            sticky=W,
        )
        ttk.Entry(advanced_tab, textvariable=self.psr_type_var).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(5, 14),
        )
        ttk.Label(advanced_tab, text="额外请求参数", style="SectionTitle.TLabel").grid(
            row=2,
            column=0,
            sticky=W,
        )
        ttk.Entry(advanced_tab, textvariable=self.extra_params_var).grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(5, 6),
        )
        ttk.Label(
            advanced_tab,
            text="格式：KEY=VALUE；多个参数使用分号分隔。通常可保持为空。",
            style="CardMuted.TLabel",
            wraplength=280,
        ).grid(row=4, column=0, sticky=W)
        ttk.Button(settings, text="重置全部条件", command=self.reset_filters).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(8, 0),
        )

        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.content_notebook = ttk.Notebook(content)
        self.content_notebook.grid(row=0, column=0, sticky="nsew")
        results_tab = ttk.Frame(self.content_notebook, padding=(8, 8))
        description_tab = ttk.Frame(self.content_notebook, padding=(8, 8))
        self.content_notebook.add(results_tab, text="数据结果")
        self.content_notebook.add(description_tab, text="数据集说明")

        result_actions = ttk.Frame(results_tab)
        result_actions.pack(fill=X, pady=(0, 7))
        ttk.Label(result_actions, text="本地结果", style="SectionTitle.TLabel").pack(side=LEFT)
        ttk.Button(
            result_actions,
            text="清除",
            style="Compact.Danger.TButton",
            width=5,
            command=self.clear_records,
        ).pack(side=RIGHT)
        ttk.Button(
            result_actions,
            text="导出",
            width=5,
            style="Compact.TButton",
            command=self.export_records,
        ).pack(
            side=RIGHT,
            padx=(0, 6),
        )
        ttk.Button(
            result_actions,
            text="刷新",
            width=5,
            style="Compact.TButton",
            command=self.refresh_results,
        ).pack(
            side=RIGHT,
            padx=(0, 6),
        )
        self.result_tree, self.raw_text = self.build_result_panel(results_tab)

        description_tab.columnconfigure(0, weight=1)
        description_tab.rowconfigure(0, weight=1)
        self.description_text = __import__("tkinter").Text(
            description_tab,
            wrap="word",
            padx=12,
            pady=10,
        )
        description_scroll = ttk.Scrollbar(
            description_tab,
            orient=VERTICAL,
            command=self.description_text.yview,
        )
        self.description_text.configure(yscrollcommand=description_scroll.set, state="disabled")
        self.description_text.grid(row=0, column=0, sticky="nsew")
        description_scroll.grid(row=0, column=1, sticky="ns")

        status_bar = ttk.Frame(outer, style="AppSurface.TFrame")
        status_bar.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(
            status_bar,
            textvariable=self.summary_var,
            style="PageSubtitle.TLabel",
            anchor=W,
        ).pack(fill=X)

    def build_result_panel(self, parent: ttk.Frame):
        main = ttk.PanedWindow(parent, orient="vertical")
        main.pack(fill=BOTH, expand=True)

        table_frame = ttk.Frame(main)
        detail_frame = ttk.Frame(main)
        main.add(table_frame, weight=4)
        main.add(detail_frame, weight=1)

        columns = (
            "dataset",
            "area",
            "start",
            "value",
            "field",
            "unit",
            "psr",
        )
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "dataset": ("数据集", 210),
            "area": ("区域", 120),
            "start": ("时段开始 UTC", 150),
            "value": ("数值", 100),
            "field": ("字段", 130),
            "unit": ("单位", 90),
            "psr": ("PSR", 70),
        }
        for column, (label, width) in headings.items():
            tree.heading(column, text=label)
            tree.column(column, width=width, minwidth=max(70, width // 2), anchor="center")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        scroll = ttk.Scrollbar(table_frame, orient=VERTICAL, command=tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=scroll_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        tree.bind("<<TreeviewSelect>>", self.on_select_result)

        raw_text = __import__("tkinter").Text(detail_frame, wrap="word", height=7, padx=10, pady=8)
        raw_scroll = ttk.Scrollbar(detail_frame, orient=VERTICAL, command=raw_text.yview)
        raw_text.configure(yscrollcommand=raw_scroll.set, state="disabled")
        raw_text.pack(side=LEFT, fill=BOTH, expand=True)
        raw_scroll.pack(side=RIGHT, fill=Y)
        return tree, raw_text

    def pick_date(self, target_var: StringVar) -> None:
        selected = DatePickerDialog(self.root, target_var.get()).show()
        if selected:
            target_var.set(selected)
            self.maybe_warn_gb_after_publication_stop()

    def refresh_token_status(self) -> None:
        token = (get_credential("entsoe_security_token") or "").strip()
        status = "configured" if token and token != ENTSOE_TOKEN_PLACEHOLDER else "missing"
        self.token_status_var.set(status)

    def selected_dataset_name(self) -> str:
        value = self.dataset_var.get().strip()
        return value.split("|", 1)[0].strip()

    def selected_config(self) -> dict[str, object]:
        dataset = self.selected_dataset_name()
        return self.config_by_name[dataset]

    @staticmethod
    def load_border_availability() -> dict[str, object]:
        if not ENTSOE_BORDER_AVAILABILITY_PATH.exists():
            return {}
        try:
            payload = json.loads(ENTSOE_BORDER_AVAILABILITY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def available_border_pairs(self, dataset: str | None = None) -> list[tuple[str, str]]:
        dataset_name = dataset or self.selected_dataset_name()
        datasets = self.border_availability.get("datasets")
        if not isinstance(datasets, dict):
            return []
        dataset_payload = datasets.get(dataset_name)
        if not isinstance(dataset_payload, dict):
            return []
        pairs = []
        for pair in dataset_payload.get("available_pairs", []):
            if not isinstance(pair, dict):
                continue
            in_area = str(pair.get("in_area") or "")
            out_area = str(pair.get("out_area") or "")
            if in_area and out_area:
                pairs.append((in_area, out_area))
        return sorted(set(pairs))

    def border_pair_probe_stats(self, dataset: str | None = None) -> dict[str, int]:
        dataset_name = dataset or self.selected_dataset_name()
        datasets = self.border_availability.get("datasets")
        if not isinstance(datasets, dict):
            return {}
        dataset_payload = datasets.get(dataset_name)
        if not isinstance(dataset_payload, dict):
            return {}
        return {
            "available": len(dataset_payload.get("available_pairs", [])),
            "unavailable": len(dataset_payload.get("unavailable_pairs", [])),
            "errors": len(dataset_payload.get("errors", [])),
        }

    def selected_area_alias(self, value: str) -> str | None:
        value = value.strip()
        if not value or value == ENTSOE_ALL_AREAS_LABEL:
            return None
        if value.endswith(")") and "(" in value:
            value = value.rsplit("(", 1)[1].rstrip(")").strip()
        normalized = value.upper()
        if normalized in ENTSOE_BIDDING_ZONES:
            return normalized
        if normalized.startswith("10Y"):
            return normalized
        available = "、".join(self.area_options[:12])
        raise ValueError(f"未知区域：{value}。请选择下拉中的中文区域；例如：{available}。")

    def selected_area_aliases(self, value: str) -> list[str]:
        alias = self.selected_area_alias(value)
        if alias:
            return [alias]
        return sorted(ENTSOE_BIDDING_ZONES)

    def selected_area_is_explicit_gb(self, value: str) -> bool:
        return self.selected_area_alias(value) == "GB"

    def selected_areas_include_gb(self) -> bool:
        domain_mode = str(self.selected_config()["domain_mode"])
        if domain_mode in {"single", "same_pair"}:
            return self.selected_area_is_explicit_gb(self.area_var.get())
        return (
            self.selected_area_is_explicit_gb(self.in_area_var.get())
            or self.selected_area_is_explicit_gb(self.out_area_var.get())
        )

    def selected_range_touches_gb_stopped_period(self) -> bool:
        try:
            end_date = date.fromisoformat(self.end_date_var.get().strip())
        except ValueError:
            return False
        return end_date > ENTSOE_GB_PUBLICATION_STOP_DATE

    def gb_warning_key(self) -> str:
        config = self.selected_config()
        if str(config["domain_mode"]) in {"single", "same_pair"}:
            area_part = self.area_var.get().strip()
        else:
            area_part = f"{self.in_area_var.get().strip()}->{self.out_area_var.get().strip()}"
        return "|".join(
            [
                self.selected_dataset_name(),
                area_part,
                self.start_date_var.get().strip(),
                self.end_date_var.get().strip(),
            ]
        )

    def maybe_warn_gb_after_publication_stop(self, *, block_action: bool = False) -> bool:
        try:
            includes_gb = self.selected_areas_include_gb()
        except ValueError:
            return True
        if not includes_gb:
            return True
        if not self.selected_range_touches_gb_stopped_period():
            return True
        warning_key = self.gb_warning_key()
        if block_action or warning_key != self.last_gb_warning_key:
            messagebox.showwarning(
                "英国 ENTSO-E 数据提示",
                (
                    "BZN|GB 的数据发布已在 2021-06-15 停止。\n\n"
                    "之后的数据请在 Elexon 处查询。"
                ),
            )
            self.last_gb_warning_key = warning_key
        return not block_action

    def selected_border_pairs(self) -> list[tuple[str, str]]:
        available_pairs = self.available_border_pairs()
        in_area = self.selected_area_alias(self.in_area_var.get())
        out_area = self.selected_area_alias(self.out_area_var.get())
        if not available_pairs:
            in_areas = [in_area] if in_area else sorted(ENTSOE_BIDDING_ZONES)
            out_areas = [out_area] if out_area else sorted(ENTSOE_BIDDING_ZONES)
            return [
                (source, target)
                for source in in_areas
                for target in out_areas
                if source != target
            ]
        return [
            (source, target)
            for source, target in available_pairs
            if (not in_area or source == in_area) and (not out_area or target == out_area)
        ]

    @staticmethod
    def format_area_option(alias: str) -> str:
        name = ENTSOE_AREA_NAMES.get(alias, alias)
        return f"{name} ({alias})"

    def update_border_area_options(self, changed: str | None = None) -> None:
        if str(self.selected_config()["domain_mode"]) != "border":
            self.border_availability_var.set("")
            return

        available_pairs = self.available_border_pairs()
        if not available_pairs:
            if self.in_area_combo is not None:
                self.in_area_combo["values"] = self.area_options
            if self.out_area_combo is not None:
                self.out_area_combo["values"] = self.area_options
            self.border_availability_var.set("未探测：显示全部区域")
            return

        selected_in = self.selected_area_alias(self.in_area_var.get())
        selected_out = self.selected_area_alias(self.out_area_var.get())
        source_aliases = sorted({source for source, target in available_pairs if not selected_out or target == selected_out})
        target_aliases = sorted({target for source, target in available_pairs if not selected_in or source == selected_in})

        in_values = ["", ENTSOE_ALL_AREAS_LABEL] + [self.format_area_option(alias) for alias in source_aliases]
        out_values = ["", ENTSOE_ALL_AREAS_LABEL] + [self.format_area_option(alias) for alias in target_aliases]
        if self.in_area_combo is not None:
            self.in_area_combo["values"] = in_values
        if self.out_area_combo is not None:
            self.out_area_combo["values"] = out_values

        if changed == "out" and self.in_area_var.get().strip() not in in_values:
            self.in_area_var.set("")
        if changed == "in" and self.out_area_var.get().strip() not in out_values:
            self.out_area_var.set("")

        stats = self.border_pair_probe_stats()
        self.border_availability_var.set(
            f"可用方向 {stats.get('available', 0)} | 无数据 {stats.get('unavailable', 0)} | 错误 {stats.get('errors', 0)}"
        )

    def on_border_area_changed(self, changed: str) -> None:
        self.update_border_area_options(changed=changed)
        self.maybe_warn_gb_after_publication_stop()

    def update_dataset_description(self) -> None:
        config = self.selected_config()
        domain_mode = str(config["domain_mode"])
        if domain_mode in {"single", "same_pair"}:
            self.mode_hint_var.set("单区域/同区")
            self.set_area_state(single_enabled=True, border_enabled=False)
            self.border_availability_var.set("")
        else:
            self.mode_hint_var.set("跨境方向")
            self.set_area_state(single_enabled=False, border_enabled=True)
            self.update_border_area_options()

        params = dict(config["params"])
        lines = [
            f"{config['title_zh']} / {config['title_en']}",
            f"采集项目标识：{config['name']}",
            f"Category: {config['category']}",
            f"Domain mode: {config['domain_mode']}",
            "",
            f"中文说明：{config['meaning_zh']}",
            f"English: {config['meaning_en']}",
            f"GUI 已接入数据集数量：{len(self.configs)} 个。",
            f"当前区域下拉：{ENTSOE_ALL_AREAS_LABEL} + {len(ENTSOE_BIDDING_ZONES)} 个内置常用区域。",
            "",
            "参数含义",
        ]
        for key, value in params.items():
            meaning = self.parameter_meanings.get(key, "固定官方参数。")
            lines.append(f"- {key}={value}: {meaning}")
        if domain_mode == "single":
            domain_parameter = str(config.get("domain_parameter") or "")
            lines.append(f"- {domain_parameter}=<area EIC>: {self.parameter_meanings.get(domain_parameter, '区域参数。')}")
        elif domain_mode == "same_pair":
            lines.append("- in_Domain/out_Domain=<area EIC>: 同一报价区的输入和输出区域。")
        else:
            lines.append("- in_Domain/out_Domain=<from/to EIC>: 跨境方向，反向需交换两个区域。")
            stats = self.border_pair_probe_stats()
            if stats:
                lines.append(
                    "- 已加载本地跨境可用性探测结果；来源/目标下拉会只显示探测到有数据的组合。"
                )
                lines.append(
                    f"- 探测摘要：可用 {stats.get('available', 0)}，"
                    f"无数据 {stats.get('unavailable', 0)}，错误 {stats.get('errors', 0)}。"
                )
            else:
                lines.append(
                    "- 当前数据集还没有本地可用性探测结果；下拉显示全部内置区域，真实可用性以 ENTSO-E 返回为准。"
                )
        lines.extend(
            [
                "- periodStart/periodEnd: UTC 查询窗口，结束点不包含。",
                f"- 选择空白或 {ENTSOE_ALL_AREAS_LABEL}: 执行时会展开为全部内置区域。",
                "",
                "返回数据意义",
                self.output_meaning,
            ]
        )
        self.set_text(self.description_text, "\n".join(lines))
        self.refresh_results()

    def set_area_state(self, *, single_enabled: bool, border_enabled: bool) -> None:
        if self.area_combo is not None:
            self.area_combo.configure(state="normal" if single_enabled else "disabled")
            if single_enabled:
                self.area_field_label.grid()
                self.area_combo.grid()
            else:
                self.area_field_label.grid_remove()
                self.area_combo.grid_remove()
        if self.in_area_combo is not None:
            self.in_area_combo.configure(state="normal" if border_enabled else "disabled")
            if border_enabled:
                self.in_area_field_label.grid()
                self.in_area_combo.grid()
            else:
                self.in_area_field_label.grid_remove()
                self.in_area_combo.grid_remove()
        if self.out_area_combo is not None:
            self.out_area_combo.configure(state="normal" if border_enabled else "disabled")
            if border_enabled:
                self.out_area_field_label.grid()
                self.out_area_combo.grid()
            else:
                self.out_area_field_label.grid_remove()
                self.out_area_combo.grid_remove()
        if border_enabled:
            self.border_status_label.grid()
        else:
            self.border_status_label.grid_remove()

    def preview_request(self) -> None:
        if not self.maybe_warn_gb_after_publication_stop(block_action=True):
            return
        try:
            request = self.build_request_preview()
        except ValueError as exc:
            messagebox.showerror("预览失败", str(exc))
            return
        self.set_text(self.raw_text, json.dumps(request, ensure_ascii=False, indent=2))
        self.summary_var.set("请求预览已生成；未访问 ENTSO-E，也未写入数据库。")

    def build_request_preview(self) -> dict[str, object]:
        config = self.selected_config()
        jobs = self.build_collect_jobs()
        previews = []
        for _label, kwargs in jobs[:20]:
            params = self.build_entsoe_params(
                config,
                area=str(kwargs.get("area") or ""),
                in_area=str(kwargs.get("in_area") or ""),
                out_area=str(kwargs.get("out_area") or ""),
            )
            params["periodStart"] = self.format_entsoe_period(self.start_date_var.get().strip())
            params["periodEnd"] = self.format_entsoe_period(self.end_date_var.get().strip())
            previews.append(params)
        return {
            "dataset": config["name"],
            "title_zh": config["title_zh"],
            "domain_mode": config["domain_mode"],
            "request_count": len(jobs),
            "preview_limit": 20,
            "params_without_token": previews,
            "dry_run": True,
        }

    def build_entsoe_params(
        self,
        config: dict[str, object],
        *,
        area: str = "",
        in_area: str = "",
        out_area: str = "",
    ) -> dict[str, str]:
        params = {str(key): str(value) for key, value in dict(config["params"]).items()}
        domain_mode = str(config["domain_mode"])
        if domain_mode == "single":
            if not area:
                raise ValueError("请选择 area。")
            params[str(config["domain_parameter"])] = self.resolve_area_code(area)
        elif domain_mode == "same_pair":
            if not area:
                raise ValueError("请选择 area。")
            area_code = self.resolve_area_code(area)
            params["in_Domain"] = area_code
            params["out_Domain"] = area_code
        elif domain_mode == "border":
            if not in_area or not out_area:
                raise ValueError("请选择 in-area 和 out-area。")
            params["in_Domain"] = self.resolve_area_code(in_area)
            params["out_Domain"] = self.resolve_area_code(out_area)
        else:
            raise ValueError(f"Unsupported ENTSO-E domain mode: {domain_mode}")

        psr_type = self.psr_type_var.get().strip()
        if psr_type:
            params["psrType"] = psr_type
        params.update(self.parse_extra_params(self.extra_params_var.get()))
        return params

    def build_collect_jobs(self) -> list[tuple[str, dict[str, object]]]:
        dataset = self.selected_dataset_name()
        config = self.selected_config()
        start_date = self.start_date_var.get().strip()
        end_date = self.end_date_var.get().strip()
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
        if date.fromisoformat(start_date) >= date.fromisoformat(end_date):
            raise ValueError("结束日期必须晚于开始日期。")

        base_kwargs: dict[str, object] = {"start_date": start_date, "end_date": end_date}
        domain_mode = str(config["domain_mode"])
        jobs: list[tuple[str, dict[str, object]]] = []
        if domain_mode in {"single", "same_pair"}:
            for area in self.selected_area_aliases(self.area_var.get()):
                jobs.append((self.format_area_option(area), {**base_kwargs, "area": area}))
        else:
            for in_area, out_area in self.selected_border_pairs():
                label = f"{self.format_area_option(in_area)} -> {self.format_area_option(out_area)}"
                jobs.append((label, {**base_kwargs, "in_area": in_area, "out_area": out_area}))

        if dataset != ENTSOE_DAY_AHEAD_DATASET:
            psr_type = self.psr_type_var.get().strip()
            extra_params = self.parse_extra_params(self.extra_params_var.get())
            for _label, kwargs in jobs:
                if psr_type:
                    kwargs["psr_type"] = psr_type
                kwargs["extra_params"] = extra_params
        elif self.psr_type_var.get().strip() or self.extra_params_var.get().strip():
            raise ValueError("entsoe_day_ahead_prices 兼容数据集不支持 psrType 或额外参数。")
        if not jobs:
            raise ValueError("没有可执行的区域组合。")
        return jobs

    def start_collect(self) -> None:
        if not self.maybe_warn_gb_after_publication_stop(block_action=True):
            return
        try:
            jobs = self.build_collect_jobs()
            self.build_request_preview()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        if self.token_status_var.get() != "configured":
            messagebox.showerror("缺少 token", "请先通过 powertrade set-credential entsoe 配置 token。")
            return
        dataset = self.selected_dataset_name()
        if len(jobs) > 20:
            if not messagebox.askyesno(
                "确认批量采集",
                (
                    f"本次会对 {len(jobs)} 个区域/方向组合执行 ENTSO-E 请求。\n\n"
                    "跨境数据如果两边都选“全部区域”，请求数量会非常大，"
                    "且很多区域组合可能没有数据。\n\n确认继续吗？"
                ),
            ):
                return
        if self.collect_button is not None:
            self.collect_button.configure(state="disabled")
        self.show_collect_dialog(dataset, len(jobs))
        thread = threading.Thread(target=self.collect_worker, args=(dataset, jobs), daemon=True)
        thread.start()

    def show_collect_dialog(self, dataset: str, job_count: int) -> None:
        if self.collect_dialog is not None and self.collect_dialog.winfo_exists():
            self.collect_dialog.destroy()
        dialog = Toplevel(self.root)
        dialog.title("正在采集 ENTSO-E")
        dialog.minsize(520, 190)
        dialog.resizable(True, True)
        self.collect_message_var.set(f"正在采集 {dataset} ...")
        self.collect_detail_var.set(f"待执行区域/方向组合：{job_count} 个")
        ttk.Label(dialog, textvariable=self.collect_message_var, wraplength=460).pack(
            fill=X,
            padx=18,
            pady=(18, 8),
        )
        ttk.Label(dialog, textvariable=self.collect_detail_var, wraplength=460).pack(
            fill=X,
            padx=18,
            pady=(0, 12),
        )
        ttk.Progressbar(dialog, mode="indeterminate").pack(fill=X, padx=18)
        ttk.Label(dialog, text="可最小化窗口，采集会继续。").pack(anchor=W, padx=18, pady=(12, 0))
        self.collect_dialog = dialog
        self.summary_var.set(f"正在采集 {dataset} ...")

    def collect_worker(self, dataset: str, jobs: list[tuple[str, dict[str, object]]]) -> None:
        record_count = 0
        written = 0
        try:
            for index, (label, kwargs) in enumerate(jobs, start=1):
                self.ui.post(
                    self.update_collect_progress,
                    index=index,
                    total=len(jobs),
                    label=label,
                )
                spider = get_spider(dataset, **kwargs)
                try:
                    records = list(spider.crawl())
                finally:
                    spider.close()
                record_count += len(records)
                if all(isinstance(record, MarketRecord) for record in records):
                    written += upsert_records(records)
                elif all(isinstance(record, EntsoeRecord) for record in records):
                    written += upsert_entsoe_records(records)
                else:
                    raise ValueError("ENTSO-E spider returned unsupported record types.")
        except Exception as exc:
            message = str(exc)
            self.ui.post(self.on_collect_error, message, record_count, written)
        else:
            self.ui.post(self.on_collect_success, record_count, written)

    def update_collect_progress(self, *, index: int, total: int, label: str) -> None:
        self.collect_message_var.set(f"正在采集 ENTSO-E：{index}/{total}")
        self.collect_detail_var.set(label)
        self.summary_var.set(f"ENTSO-E 采集中：{index}/{total} {label}")

    def on_collect_success(self, record_count: int, written: int) -> None:
        self.finish_collect()
        self.refresh_results()
        if record_count == 0:
            self.summary_var.set("ENTSO-E 请求已完成：所选范围无新数据，数据库未修改。")
            messagebox.showinfo(
                "无新数据",
                "ENTSO-E 在所选竞价区和日期范围没有返回记录。请求执行成功，数据库未修改。",
            )
            return
        self.summary_var.set(f"ENTSO-E 采集完成：抓取 {record_count} 条，写入/更新 {written} 条。")
        messagebox.showinfo(
            "采集完成",
            f"ENTSO-E 采集完成。\n\n抓取记录：{record_count}\n写入/更新：{written}",
        )

    def on_collect_error(
        self,
        message: str,
        record_count: int = 0,
        written: int = 0,
    ) -> None:
        self.finish_collect()
        data_note = (
            f"失败前已抓取 {record_count} 条、写入/更新 {written} 条；请刷新页面核对已保存数据。"
            if written
            else "本次没有修改业务数据。"
        )
        self.summary_var.set(f"ENTSO-E 采集失败：{data_note}")
        messagebox.showerror("ENTSO-E 采集失败", f"{message}\n\n{data_note}")

    def finish_collect(self) -> None:
        if self.collect_button is not None:
            self.collect_button.configure(state="normal")
        if self.collect_dialog is not None and self.collect_dialog.winfo_exists():
            self.collect_dialog.destroy()
        self.collect_dialog = None

    def export_records(self) -> None:
        output_path = asksaveasfilename(
            title="导出 ENTSO-E CSV",
            initialfile=f"{self.selected_dataset_name()}.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not output_path:
            return
        try:
            filters = self.current_query_filters()
            row_count = self.repository.export_records(output_path=Path(output_path), **filters)
        except (OSError, ValueError) as exc:
            messagebox.showerror("导出失败", str(exc))
            self.summary_var.set("ENTSO-E CSV 导出失败。")
            return
        self.summary_var.set(f"已导出 {row_count} 条 ENTSO-E 记录到 {output_path}")
        messagebox.showinfo("导出完成", f"已导出 {row_count} 条记录。\n\n{output_path}")

    def clear_records(self) -> None:
        try:
            filters = self.current_query_filters()
        except ValueError as exc:
            messagebox.showerror("清除失败", str(exc))
            return
        dataset = self.selected_dataset_name()
        area_label = self.current_area_label()
        if not messagebox.askyesno(
            "确认清除数据",
            (
                f"将清除当前筛选范围内的 ENTSO-E 数据。\n\n"
                f"数据集：{dataset}\n"
                f"区域：{area_label}\n"
                f"日期：{self.start_date_var.get()} 至 {self.end_date_var.get()}\n\n"
                "此操作不可撤销，确认继续吗？"
            ),
        ):
            return
        try:
            deleted = self.repository.delete_records(**filters)
        except ValueError as exc:
            messagebox.showerror("清除失败", str(exc))
            return
        self.refresh_results()
        self.summary_var.set(f"已清除 {deleted} 条 ENTSO-E 记录。")
        messagebox.showinfo("清除完成", f"已清除 {deleted} 条记录。")

    def refresh_results(self) -> None:
        try:
            filters = self.current_query_filters()
        except ValueError as exc:
            messagebox.showerror("刷新失败", str(exc))
            return
        rows = self.repository.search(
            **filters,
        )
        self.result_rows = rows
        self.result_tree.delete(*self.result_tree.get_children())
        for index, row in enumerate(rows):
            self.result_tree.insert(
                "",
                END,
                iid=str(index),
                values=(
                    row.get("dataset") or "",
                    self.display_area(row),
                    row.get("interval_start_utc") or "",
                    self.format_value(row.get("value")),
                    row.get("value_field") or "",
                    row.get("unit") or "",
                    row.get("psr_type") or "",
                ),
            )
        self.summary_var.set(f"ENTSO-E：显示 {len(rows)} 条记录。默认最多显示 1000 条。")
        self.show_first_result()

    def current_query_filters(self) -> dict[str, str]:
        dataset = self.selected_dataset_name()
        config = self.selected_config()
        filters = {
            "dataset": dataset,
            "area": "",
            "in_domain": "",
            "out_domain": "",
            "start_date": self.start_date_var.get().strip(),
            "end_date": self.end_date_var.get().strip(),
        }
        domain_mode = str(config["domain_mode"])
        if domain_mode in {"single", "same_pair"}:
            area = self.selected_area_alias(self.area_var.get())
            filters["area"] = area or ""
        else:
            in_area = self.selected_area_alias(self.in_area_var.get())
            out_area = self.selected_area_alias(self.out_area_var.get())
            filters["in_domain"] = self.resolve_area_code(in_area) if in_area else ""
            filters["out_domain"] = self.resolve_area_code(out_area) if out_area else ""
        return filters

    def current_area_label(self) -> str:
        config = self.selected_config()
        if str(config["domain_mode"]) in {"single", "same_pair"}:
            return self.area_var.get().strip() or ENTSOE_ALL_AREAS_LABEL
        return (
            f"{self.in_area_var.get().strip() or ENTSOE_ALL_AREAS_LABEL} -> "
            f"{self.out_area_var.get().strip() or ENTSOE_ALL_AREAS_LABEL}"
        )

    def reset_filters(self) -> None:
        self.area_var.set(self.format_area_option("DE-LU"))
        self.in_area_var.set(self.format_area_option("FR"))
        self.out_area_var.set(self.format_area_option("DE-LU"))
        today = date.today()
        self.start_date_var.set((today - timedelta(days=1)).isoformat())
        self.end_date_var.set(today.isoformat())
        self.psr_type_var.set("")
        self.extra_params_var.set("")
        self.refresh_results()

    def show_first_result(self) -> None:
        if not self.result_rows:
            self.set_text(self.raw_text, "没有匹配数据。")
            return
        self.result_tree.selection_set("0")
        self.result_tree.focus("0")
        self.set_text(self.raw_text, self.describe_row(self.result_rows[0]))

    def on_select_result(self, _event) -> None:
        selection = self.result_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self.result_rows):
            self.set_text(self.raw_text, self.describe_row(self.result_rows[index]))

    def describe_row(self, row: dict[str, object]) -> str:
        data = dict(row)
        raw_json = data.pop("raw_json", None)
        lines = [f"{key}: {value if value not in (None, '') else '-'}" for key, value in data.items()]
        lines.extend(["", "raw_json", self.format_json(str(raw_json) if raw_json else None)])
        return "\n".join(lines)

    @staticmethod
    def display_area(row: dict[str, object]) -> str:
        area = row.get("area")
        if area:
            return EntsoeDataApp.format_area_value(str(area))
        in_domain = EntsoeDataApp.format_area_value(str(row.get("in_domain") or "?"))
        out_domain = EntsoeDataApp.format_area_value(str(row.get("out_domain") or "?"))
        return f"{in_domain}->{out_domain}"

    @staticmethod
    def format_area_value(value: str) -> str:
        if value in ENTSOE_BIDDING_ZONES:
            return EntsoeDataApp.format_area_option(value)
        for alias, eic in ENTSOE_BIDDING_ZONES.items():
            if value == eic:
                return EntsoeDataApp.format_area_option(alias)
        return value

    @staticmethod
    def resolve_area_code(value: str) -> str:
        if value in ENTSOE_BIDDING_ZONES:
            return ENTSOE_BIDDING_ZONES[value]
        if value.startswith("10Y"):
            return value
        available = ", ".join(sorted(ENTSOE_BIDDING_ZONES))
        raise ValueError(f"未知 ENTSO-E area：{value}。可用别名：{available}")

    @staticmethod
    def format_entsoe_period(value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("日期格式应为 YYYY-MM-DD。") from exc
        return parsed.strftime("%Y%m%d0000")

    @staticmethod
    def parse_extra_params(value: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        if not value.strip():
            return parsed
        normalized = value.replace("\n", ";").replace(",", ";")
        for item in normalized.split(";"):
            item = item.strip()
            if not item:
                continue
            key, separator, raw_value = item.partition("=")
            if not separator or not key.strip() or not raw_value.strip():
                raise ValueError(f"额外参数格式错误：{item}。应使用 KEY=VALUE。")
            parsed[key.strip()] = raw_value.strip()
        return parsed

    def set_text(self, text_widget, content: str) -> None:
        text_widget.configure(state="normal")
        text_widget.delete("1.0", END)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    @staticmethod
    def format_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)

    @staticmethod
    def format_json(value: str | None) -> str:
        if not value:
            return "{}"
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return value


class ElexonDataRepository:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or resolve_sqlite_path()

    def connect(self) -> AbstractContextManager[sqlite3.Connection]:
        return sqlite_row_connection(self.db_path)

    def table_exists(self, table_name: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        return row is not None

    def search(
        self,
        *,
        dataset: str = "",
        metric: str = "",
        bm_unit: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 1000,
    ) -> list[dict[str, object]]:
        if not self.table_exists("elexon_records"):
            return []
        where, params = self.build_where(
            dataset=dataset,
            metric=metric,
            bm_unit=bm_unit,
            start_date=start_date,
            end_date=end_date,
        )
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, dataset, category, title_zh, title_en, endpoint, area,
                       settlement_date, settlement_period, publish_time_utc,
                       start_time_utc, end_time_utc, fuel_type, bm_unit,
                       national_grid_bm_unit, metric, value, value_field, unit,
                       currency, raw_json, collected_at
                FROM elexon_records
                WHERE {' AND '.join(where)}
                ORDER BY COALESCE(start_time_utc, settlement_date, publish_time_utc, '') DESC,
                         dataset, metric, fuel_type, bm_unit
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_records(
        self,
        *,
        dataset: str,
        metric: str = "",
        bm_unit: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if not self.table_exists("elexon_records"):
            return 0
        where, params = self.build_where(
            dataset=dataset,
            metric=metric,
            bm_unit=bm_unit,
            start_date=start_date,
            end_date=end_date,
        )
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM elexon_records WHERE {' AND '.join(where)}",
                params,
            )
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            connection.commit()
        return deleted

    def export_records(
        self,
        *,
        output_path: Path,
        dataset: str,
        metric: str = "",
        bm_unit: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if not self.table_exists("elexon_records"):
            return 0
        columns = [
            "id",
            "dataset",
            "category",
            "title_zh",
            "title_en",
            "endpoint",
            "area",
            "settlement_date",
            "settlement_period",
            "publish_time_utc",
            "start_time_utc",
            "end_time_utc",
            "fuel_type",
            "bm_unit",
            "national_grid_bm_unit",
            "metric",
            "value",
            "value_field",
            "unit",
            "currency",
            "raw_json",
            "collected_at",
        ]
        where, params = self.build_where(
            dataset=dataset,
            metric=metric,
            bm_unit=bm_unit,
            start_date=start_date,
            end_date=end_date,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        row_count = 0
        with self.connect() as connection, output_path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            cursor = connection.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM elexon_records
                WHERE {' AND '.join(where)}
                ORDER BY COALESCE(start_time_utc, settlement_date, publish_time_utc, '') DESC,
                         dataset, metric, fuel_type, bm_unit
                """,
                params,
            )
            for row in cursor:
                writer.writerow(dict(row))
                row_count += 1
        return row_count

    def build_where(
        self,
        *,
        dataset: str,
        metric: str = "",
        bm_unit: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> tuple[list[str], list[object]]:
        where = ["dataset = ?"]
        params: list[object] = [dataset]
        if metric:
            where.append("metric = ?")
            params.append(metric)
        if bm_unit:
            where.append("(bm_unit = ? OR national_grid_bm_unit = ?)")
            params.extend([bm_unit, bm_unit])
        if start_date:
            where.append("COALESCE(start_time_utc, settlement_date, publish_time_utc, '') >= ?")
            params.append(start_date)
        if end_date:
            where.append("COALESCE(start_time_utc, settlement_date, publish_time_utc, '') < ?")
            params.append(end_date)
        return where, params


class ElexonDataApp:
    output_meaning = (
        "返回数据写入 elexon_records。每个官方 JSON 行会按配置中的 value_fields 拆成一条或多条记录；"
        "raw_json 保留 Elexon 原始字段和本次请求参数。燃料类型、互联线名称等维度会尽量显示在结果表的类型列。"
    )

    def __init__(self, root, repository: ElexonDataRepository) -> None:
        self.root = root
        self.ui = UiLifecycle(root)
        self.repository = repository
        self.configs = load_elexon_request_configs()
        self.config_by_name = {config["name"]: config for config in self.configs}
        self.dataset_options = [
            f"{config['name']} | {config['title_zh']} / {config['title_en']}"
            for config in self.configs
        ]
        today = date.today()
        self.dataset_var = StringVar(value=self.dataset_options[0] if self.dataset_options else "")
        self.dataset_count_var = StringVar(value=f"已接入 {len(self.configs)} 个数据集")
        self.api_key_status_var = StringVar()
        self.start_date_var = StringVar(value=(today - timedelta(days=1)).isoformat())
        self.end_date_var = StringVar(value=today.isoformat())
        self.metric_var = StringVar()
        self.bm_unit_var = StringVar()
        self.extra_params_var = StringVar()
        self.summary_var = StringVar(value="Ready")
        self.result_rows: list[dict[str, object]] = []
        self.collect_button: ttk.Button | None = None
        self.collect_dialog: Toplevel | None = None
        self.collect_message_var = StringVar()
        self.collect_detail_var = StringVar()

        self.build_layout()
        self.refresh_api_key_status()
        self.update_dataset_description()
        self.refresh_results()

    def build_layout(self) -> None:
        outer = ttk.Frame(
            self.root,
            style="AppSurface.TFrame",
            padding=(12, 10, 12, 8),
        )
        outer.pack(fill=BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        command_card = ttk.Frame(outer, style="Card.TFrame", padding=(14, 10))
        command_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        command_card.columnconfigure(1, weight=1)
        ttk.Label(command_card, text="数据集", style="SectionTitle.TLabel").grid(
            row=0,
            column=0,
            sticky=W,
            padx=(0, 8),
        )
        self.dataset_combo = ttk.Combobox(
            command_card,
            textvariable=self.dataset_var,
            values=self.dataset_options,
            state="readonly",
        )
        self.dataset_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.dataset_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.update_dataset_description(),
        )
        ttk.Label(
            command_card,
            textvariable=self.dataset_count_var,
            style="CardMuted.TLabel",
        ).grid(row=0, column=2, sticky="e", padx=(0, 12))
        ttk.Button(
            command_card,
            text="预览请求",
            command=lambda: (self.content_notebook.select(0), self.preview_request()),
        ).grid(
            row=0,
            column=3,
            padx=(0, 7),
        )
        self.collect_button = ttk.Button(
            command_card,
            text="执行爬取",
            style="Primary.TButton",
            command=self.start_collect,
        )
        self.collect_button.grid(row=0, column=4)

        access_row = ttk.Frame(command_card, style="CardBody.TFrame")
        access_row.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        ttk.Label(access_row, text="Elexon Insights API", style="CardMuted.TLabel").pack(side=LEFT)
        ttk.Label(access_row, textvariable=self.api_key_status_var, style="Success.TLabel").pack(
            side=LEFT,
            padx=(7, 8),
        )
        ttk.Button(
            access_row,
            text="刷新访问状态",
            style="Link.TButton",
            command=self.refresh_api_key_status,
        ).pack(side=LEFT)
        ttk.Label(
            access_row,
            text="选择条件 → 预览请求 → 执行爬取",
            style="CardMuted.TLabel",
        ).pack(side=RIGHT)

        workspace = ttk.PanedWindow(outer, orient="horizontal")
        workspace.grid(row=1, column=0, sticky="nsew")
        settings = ttk.LabelFrame(workspace, text="采集条件", width=360, padding=(12, 10))
        settings.grid_propagate(False)
        settings.columnconfigure(0, weight=1)
        settings.rowconfigure(0, weight=1)
        content = ttk.Frame(workspace, style="AppSurface.TFrame")
        workspace.add(settings, weight=0)
        workspace.add(content, weight=1)

        condition_tabs = ttk.Notebook(settings)
        condition_tabs.grid(row=0, column=0, sticky="nsew")
        basic_tab = ttk.Frame(condition_tabs, padding=(10, 10))
        filter_tab = ttk.Frame(condition_tabs, padding=(10, 10))
        advanced_tab = ttk.Frame(condition_tabs, padding=(10, 10))
        condition_tabs.add(basic_tab, text="时间")
        condition_tabs.add(filter_tab, text="筛选")
        condition_tabs.add(advanced_tab, text="高级")
        basic_tab.columnconfigure(1, weight=1)
        filter_tab.columnconfigure(1, weight=1)

        ttk.Label(basic_tab, text="查询时间", style="SectionTitle.TLabel").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky=W,
            pady=(0, 5),
        )

        def add_date_field(row: int, label: str, variable: StringVar) -> None:
            ttk.Label(basic_tab, text=label).grid(row=row, column=0, sticky=W, pady=4, padx=(0, 8))
            date_row = ttk.Frame(basic_tab)
            date_row.grid(row=row, column=1, sticky="ew", pady=4)
            date_row.columnconfigure(0, weight=1)
            ttk.Entry(
                date_row,
                textvariable=variable,
                state="readonly",
                width=10,
                style="Compact.TEntry",
            ).grid(
                row=0,
                column=0,
                sticky="ew",
                padx=(0, 5),
            )
            ttk.Button(
                date_row,
                text="选择",
                width=5,
                style="Compact.TButton",
                command=lambda: self.pick_date(variable),
            ).grid(
                row=0,
                column=1,
            )

        add_date_field(1, "开始", self.start_date_var)
        add_date_field(2, "结束", self.end_date_var)
        ttk.Label(filter_tab, text="本地结果筛选", style="SectionTitle.TLabel").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky=W,
            pady=(0, 5),
        )
        ttk.Label(filter_tab, text="数据项 / 指标").grid(
            row=1,
            column=0,
            sticky=W,
            pady=2,
            padx=(0, 8),
        )
        ttk.Entry(filter_tab, textvariable=self.metric_var, style="Compact.TEntry").grid(
            row=1,
            column=1,
            sticky="ew",
            pady=2,
        )
        ttk.Label(filter_tab, text="BMU").grid(
            row=2,
            column=0,
            sticky=W,
            pady=2,
            padx=(0, 8),
        )
        ttk.Entry(filter_tab, textvariable=self.bm_unit_var, style="Compact.TEntry").grid(
            row=2,
            column=1,
            sticky="ew",
            pady=2,
        )
        ttk.Label(
            filter_tab,
            text="均可留空；只影响本地结果筛选。",
            style="CardMuted.TLabel",
            wraplength=260,
        ).grid(row=3, column=0, columnspan=2, sticky=W, pady=(5, 0))

        advanced_tab.columnconfigure(0, weight=1)
        ttk.Label(advanced_tab, text="额外请求参数", style="SectionTitle.TLabel").grid(
            row=0,
            column=0,
            sticky=W,
        )
        ttk.Entry(
            advanced_tab,
            textvariable=self.extra_params_var,
            style="Compact.TEntry",
        ).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(5, 6),
        )
        ttk.Label(
            advanced_tab,
            text="格式：KEY=VALUE；多个参数使用分号分隔。通常可保持为空。",
            style="CardMuted.TLabel",
            wraplength=270,
        ).grid(row=2, column=0, sticky=W)
        ttk.Button(settings, text="重置全部条件", command=self.reset_filters).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(8, 0),
        )

        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.content_notebook = ttk.Notebook(content)
        self.content_notebook.grid(row=0, column=0, sticky="nsew")
        results_tab = ttk.Frame(self.content_notebook, padding=(8, 8))
        description_tab = ttk.Frame(self.content_notebook, padding=(8, 8))
        self.content_notebook.add(results_tab, text="数据结果")
        self.content_notebook.add(description_tab, text="数据集说明")

        result_actions = ttk.Frame(results_tab)
        result_actions.pack(fill=X, pady=(0, 7))
        ttk.Label(result_actions, text="本地结果", style="SectionTitle.TLabel").pack(side=LEFT)
        ttk.Button(
            result_actions,
            text="清除",
            style="Compact.Danger.TButton",
            width=5,
            command=self.clear_records,
        ).pack(side=RIGHT)
        ttk.Button(
            result_actions,
            text="导出",
            width=5,
            style="Compact.TButton",
            command=self.export_records,
        ).pack(
            side=RIGHT,
            padx=(0, 6),
        )
        ttk.Button(
            result_actions,
            text="刷新",
            width=5,
            style="Compact.TButton",
            command=self.refresh_results,
        ).pack(
            side=RIGHT,
            padx=(0, 6),
        )
        self.result_tree, self.raw_text = self.build_result_panel(results_tab)

        description_tab.columnconfigure(0, weight=1)
        description_tab.rowconfigure(0, weight=1)
        self.description_text = __import__("tkinter").Text(
            description_tab,
            wrap="word",
            padx=12,
            pady=10,
        )
        description_scroll = ttk.Scrollbar(
            description_tab,
            orient=VERTICAL,
            command=self.description_text.yview,
        )
        self.description_text.configure(yscrollcommand=description_scroll.set, state="disabled")
        self.description_text.grid(row=0, column=0, sticky="nsew")
        description_scroll.grid(row=0, column=1, sticky="ns")

        status_bar = ttk.Frame(outer, style="AppSurface.TFrame")
        status_bar.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(
            status_bar,
            textvariable=self.summary_var,
            style="PageSubtitle.TLabel",
            anchor=W,
        ).pack(fill=X)

    def build_result_panel(self, parent: ttk.Frame):
        main = ttk.PanedWindow(parent, orient="vertical")
        main.pack(fill=BOTH, expand=True)

        table_frame = ttk.Frame(main)
        detail_frame = ttk.Frame(main)
        main.add(table_frame, weight=4)
        main.add(detail_frame, weight=1)

        columns = ("dataset", "category", "metric", "start", "period", "value", "unit", "fuel", "bmu")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "dataset": ("数据集", 220),
            "category": ("类别", 80),
            "metric": ("数据项/指标", 160),
            "start": ("时段开始 UTC", 150),
            "period": ("SP", 55),
            "value": ("数值", 100),
            "unit": ("单位", 90),
            "fuel": ("燃料/互联线/类型", 125),
            "bmu": ("BMU/平衡单元", 120),
        }
        for column, (label, width) in headings.items():
            tree.heading(column, text=label)
            tree.column(column, width=width, minwidth=max(55, width // 2), anchor="center")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        scroll = ttk.Scrollbar(table_frame, orient=VERTICAL, command=tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=scroll_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        tree.bind("<<TreeviewSelect>>", self.on_select_result)

        raw_text = __import__("tkinter").Text(detail_frame, wrap="word", height=7, padx=10, pady=8)
        raw_scroll = ttk.Scrollbar(detail_frame, orient=VERTICAL, command=raw_text.yview)
        raw_text.configure(yscrollcommand=raw_scroll.set, state="disabled")
        raw_text.pack(side=LEFT, fill=BOTH, expand=True)
        raw_scroll.pack(side=RIGHT, fill=Y)
        return tree, raw_text

    def pick_date(self, target_var: StringVar) -> None:
        selected = DatePickerDialog(self.root, target_var.get()).show()
        if selected:
            target_var.set(selected)

    def refresh_api_key_status(self) -> None:
        api_key = (get_credential("elexon_api_key") or "").strip()
        if api_key and api_key != ELEXON_API_KEY_PLACEHOLDER:
            self.api_key_status_var.set("optional configured")
        else:
            self.api_key_status_var.set("not required")

    def selected_dataset_name(self) -> str:
        value = self.dataset_var.get().strip()
        return value.split("|", 1)[0].strip()

    def selected_config(self) -> dict[str, object]:
        return self.config_by_name[self.selected_dataset_name()]

    def update_dataset_description(self) -> None:
        config = self.selected_config()
        lines = [
            f"{config['title_zh']} / {config['title_en']}",
            f"采集项目标识：{config['name']}",
            f"Category: {config['category']}",
            f"官方接口：{config['endpoint']}",
            f"时间口径：{config['time_mode']}",
            "",
            f"中文说明：{config['meaning_zh']}",
            f"English: {config['meaning_en']}",
            "Elexon Insights API 当前公开访问，不要求 API key；如以后配置 key，只显示状态不显示明文。",
        ]
        if config.get("reference_endpoint"):
            lines.append(f"Reference endpoint: {config['reference_endpoint']}")
        concept_notes = config.get("concept_notes") or []
        if concept_notes:
            lines.extend(["", "术语和使用说明"])
            for note in concept_notes:
                lines.append(f"- {note}")
        lines.extend(["", "主数值字段"])
        for value_config in config["value_fields"]:
            field_line = (
                f"- {value_config['field']} -> {value_config['metric']} "
                f"({value_config.get('unit') or '-'})"
            )
            if value_config.get("meaning_zh"):
                field_line = f"{field_line}: {value_config['meaning_zh']}"
            lines.append(field_line)
        default_params = config.get("default_params") or {}
        if default_params:
            lines.extend(["", f"默认参数：{default_params}"])
        parameter_notes = config.get("parameter_notes") or {}
        if parameter_notes:
            lines.append("")
            lines.append("参数含义")
            for key, meaning in parameter_notes.items():
                lines.append(f"- {key}: {meaning}")
        lines.extend(["", "返回数据意义", self.output_meaning])
        self.set_text(self.description_text, "\n".join(lines))
        self.refresh_results()

    def build_spider_kwargs(self) -> dict[str, object]:
        start_date = self.start_date_var.get().strip()
        end_date = self.end_date_var.get().strip()
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
        if date.fromisoformat(start_date) >= date.fromisoformat(end_date):
            raise ValueError("结束日期必须晚于开始日期。")
        return {
            "start_date": start_date,
            "end_date": end_date,
            "extra_params": self.parse_extra_params(self.extra_params_var.get()),
        }

    def preview_request(self) -> None:
        try:
            dataset = self.selected_dataset_name()
            spider = get_spider(dataset, **self.build_spider_kwargs())
            try:
                specs = spider.build_request_specs()
            finally:
                spider.close()
        except ValueError as exc:
            messagebox.showerror("预览失败", str(exc))
            return
        request = {
            "dataset": dataset,
            "title_zh": self.selected_config()["title_zh"],
            "request_count": len(specs),
            "preview_limit": 20,
            "requests": specs[:20],
            "dry_run": True,
        }
        self.set_text(self.raw_text, json.dumps(request, ensure_ascii=False, indent=2))
        self.summary_var.set("请求预览已生成；未访问 Elexon，也未写入数据库。")

    def start_collect(self) -> None:
        try:
            dataset = self.selected_dataset_name()
            kwargs = self.build_spider_kwargs()
            spider = get_spider(dataset, **kwargs)
            try:
                request_count = len(spider.build_request_specs())
            finally:
                spider.close()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        if request_count > 20:
            if not messagebox.askyesno(
                "确认批量采集",
                f"本次会执行 {request_count} 个 Elexon 请求。\n\n确认继续吗？",
            ):
                return
        if self.collect_button is not None:
            self.collect_button.configure(state="disabled")
        self.show_collect_dialog(dataset, request_count)
        thread = threading.Thread(target=self.collect_worker, args=(dataset, kwargs), daemon=True)
        thread.start()

    def show_collect_dialog(self, dataset: str, request_count: int) -> None:
        if self.collect_dialog is not None and self.collect_dialog.winfo_exists():
            self.collect_dialog.destroy()
        dialog = Toplevel(self.root)
        dialog.title("正在采集 Elexon")
        dialog.minsize(520, 190)
        dialog.resizable(True, True)
        self.collect_message_var.set(f"正在采集 {dataset} ...")
        self.collect_detail_var.set(f"待执行请求：{request_count} 个")
        ttk.Label(dialog, textvariable=self.collect_message_var, wraplength=460).pack(
            fill=X,
            padx=18,
            pady=(18, 8),
        )
        ttk.Label(dialog, textvariable=self.collect_detail_var, wraplength=460).pack(
            fill=X,
            padx=18,
            pady=(0, 12),
        )
        ttk.Progressbar(dialog, mode="indeterminate").pack(fill=X, padx=18)
        ttk.Label(dialog, text="可最小化窗口，采集会继续。").pack(anchor=W, padx=18, pady=(12, 0))
        self.collect_dialog = dialog
        self.summary_var.set(f"正在采集 {dataset} ...")

    def collect_worker(self, dataset: str, kwargs: dict[str, object]) -> None:
        try:
            spider = get_spider(dataset, **kwargs)
            try:
                records = list(spider.crawl())
            finally:
                spider.close()
            if not all(isinstance(record, ElexonRecord) for record in records):
                raise ValueError("Elexon spider returned unsupported record types.")
            written = upsert_elexon_records(records)
        except Exception as exc:
            message = str(exc)
            self.ui.post(self.on_collect_error, message)
        else:
            self.ui.post(self.on_collect_success, len(records), written)

    def on_collect_success(self, record_count: int, written: int) -> None:
        self.finish_collect()
        self.refresh_results()
        if record_count == 0:
            self.summary_var.set("Elexon 请求已完成：所选范围无新数据，数据库未修改。")
            messagebox.showinfo(
                "无新数据",
                "Elexon 在所选数据集和日期范围没有返回记录。请求执行成功，数据库未修改。",
            )
            return
        self.summary_var.set(f"Elexon 采集完成：抓取 {record_count} 条，写入/更新 {written} 条。")
        messagebox.showinfo(
            "采集完成",
            f"Elexon 采集完成。\n\n抓取记录：{record_count}\n写入/更新：{written}",
        )

    def on_collect_error(self, message: str) -> None:
        self.finish_collect()
        self.summary_var.set("Elexon 采集失败：本次没有修改业务数据。")
        messagebox.showerror(
            "Elexon 采集失败",
            f"{message}\n\n本次没有修改业务数据。",
        )

    def finish_collect(self) -> None:
        if self.collect_button is not None:
            self.collect_button.configure(state="normal")
        if self.collect_dialog is not None and self.collect_dialog.winfo_exists():
            self.collect_dialog.destroy()
        self.collect_dialog = None

    def refresh_results(self) -> None:
        try:
            filters = self.current_query_filters()
        except ValueError as exc:
            messagebox.showerror("刷新失败", str(exc))
            return
        rows = self.repository.search(**filters)
        self.result_rows = rows
        self.result_tree.delete(*self.result_tree.get_children())
        for index, row in enumerate(rows):
            self.result_tree.insert(
                "",
                END,
                iid=str(index),
                values=(
                    row.get("dataset") or "",
                    row.get("category") or "",
                    row.get("metric") or "",
                    row.get("start_time_utc") or row.get("publish_time_utc") or row.get("settlement_date") or "",
                    row.get("settlement_period") or "",
                    self.format_value(row.get("value")),
                    row.get("unit") or "",
                    row.get("fuel_type") or "",
                    row.get("bm_unit") or row.get("national_grid_bm_unit") or "",
                ),
            )
        self.summary_var.set(f"Elexon：显示 {len(rows)} 条记录。默认最多显示 1000 条。")
        self.show_first_result()

    def current_query_filters(self) -> dict[str, str]:
        return {
            "dataset": self.selected_dataset_name(),
            "metric": self.metric_var.get().strip(),
            "bm_unit": self.bm_unit_var.get().strip(),
            "start_date": self.start_date_var.get().strip(),
            "end_date": self.end_date_var.get().strip(),
        }

    def export_records(self) -> None:
        output_path = asksaveasfilename(
            title="导出 Elexon CSV",
            initialfile=f"{self.selected_dataset_name()}.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not output_path:
            return
        try:
            filters = self.current_query_filters()
            row_count = self.repository.export_records(output_path=Path(output_path), **filters)
        except (OSError, ValueError) as exc:
            messagebox.showerror("导出失败", str(exc))
            self.summary_var.set("Elexon CSV 导出失败。")
            return
        self.summary_var.set(f"已导出 {row_count} 条 Elexon 记录到 {output_path}")
        messagebox.showinfo("导出完成", f"已导出 {row_count} 条记录。\n\n{output_path}")

    def clear_records(self) -> None:
        try:
            filters = self.current_query_filters()
        except ValueError as exc:
            messagebox.showerror("清除失败", str(exc))
            return
        if not messagebox.askyesno(
            "确认清除数据",
            (
                "将清除当前筛选范围内的 Elexon 数据。\n\n"
                f"数据集：{filters['dataset']}\n"
                f"指标：{filters['metric'] or '全部'}\n"
                f"BMU（平衡单元）：{filters['bm_unit'] or '全部'}\n"
                f"日期：{filters['start_date']} 至 {filters['end_date']}\n\n"
                "此操作不可撤销，确认继续吗？"
            ),
        ):
            return
        try:
            deleted = self.repository.delete_records(**filters)
        except ValueError as exc:
            messagebox.showerror("清除失败", str(exc))
            return
        self.refresh_results()
        self.summary_var.set(f"已清除 {deleted} 条 Elexon 记录。")
        messagebox.showinfo("清除完成", f"已清除 {deleted} 条记录。")

    def reset_filters(self) -> None:
        today = date.today()
        self.start_date_var.set((today - timedelta(days=1)).isoformat())
        self.end_date_var.set(today.isoformat())
        self.metric_var.set("")
        self.bm_unit_var.set("")
        self.extra_params_var.set("")
        self.refresh_results()

    def show_first_result(self) -> None:
        if not self.result_rows:
            self.set_text(self.raw_text, "没有匹配数据。")
            return
        self.result_tree.selection_set("0")
        self.result_tree.focus("0")
        self.set_text(self.raw_text, self.describe_row(self.result_rows[0]))

    def on_select_result(self, _event) -> None:
        selection = self.result_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self.result_rows):
            self.set_text(self.raw_text, self.describe_row(self.result_rows[index]))

    def describe_row(self, row: dict[str, object]) -> str:
        data = dict(row)
        raw_json = data.pop("raw_json", None)
        lines = [f"{key}: {value if value not in (None, '') else '-'}" for key, value in data.items()]
        lines.extend(["", "raw_json", self.format_json(str(raw_json) if raw_json else None)])
        return "\n".join(lines)

    @staticmethod
    def parse_extra_params(value: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        if not value.strip():
            return parsed
        normalized = value.replace("\n", ";").replace(",", ";")
        for item in normalized.split(";"):
            item = item.strip()
            if not item:
                continue
            key, separator, raw_value = item.partition("=")
            if not separator or not key.strip() or not raw_value.strip():
                raise ValueError(f"额外参数格式错误：{item}。应使用 KEY=VALUE。")
            parsed[key.strip()] = raw_value.strip()
        return parsed

    def set_text(self, text_widget, content: str) -> None:
        text_widget.configure(state="normal")
        text_widget.delete("1.0", END)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

    @staticmethod
    def format_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)

    @staticmethod
    def format_json(value: str | None) -> str:
        if not value:
            return "{}"
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return value


def build_gui(root: Tk, db_path: Path) -> "PowertradeAppShell":
    from powertrade_crawler.app_shell import PowertradeAppShell
    from powertrade_crawler.credential_setup_gui import CredentialSetupApp
    from powertrade_crawler.market_agent.gui import MarketAgentApp
    from powertrade_crawler.overview_gui import OverviewApp
    from powertrade_crawler.ui_theme import (
        apply_semantic_widget_styles,
        configure_app_theme,
    )

    configure_app_theme(root)
    install_ui_dispatcher(root)
    shell = PowertradeAppShell(root, db_path)

    overview_app = OverviewApp(
        shell.page("overview"),
        on_navigate=shell.show_page,
        on_agent_question=shell.open_agent_with_prompt,
    )
    gridstatus_app = GridStatusMetadataApp(
        shell.page("gridstatus"),
        GridStatusMetadataRepository(db_path),
        configure_window=False,
    )
    elecheck_app = ElecheckDataApp(
        shell.page("elecheck"),
        ElecheckDataRepository(db_path),
        on_navigate=shell.show_page,
    )
    entsoe_app = EntsoeDataApp(
        shell.page("entsoe"),
        EntsoeDataRepository(db_path),
    )
    elexon_app = ElexonDataApp(
        shell.page("elexon"),
        ElexonDataRepository(db_path),
    )
    market_agent_app = MarketAgentApp(
        shell.page("agent"),
        on_navigate=shell.show_page,
    )
    schedule_app = ScheduleDataApp(shell.page("schedule"))
    credential_setup_app = CredentialSetupApp(shell.page("setup"))
    for key, controller in (
        ("overview", overview_app),
        ("gridstatus", gridstatus_app),
        ("elecheck", elecheck_app),
        ("entsoe", entsoe_app),
        ("elexon", elexon_app),
        ("agent", market_agent_app),
        ("schedule", schedule_app),
        ("setup", credential_setup_app),
    ):
        shell.register_controller(key, controller)
    shell.show_page("overview")
    root.after_idle(lambda: apply_semantic_widget_styles(root))
    return shell


def launch_gui() -> None:
    db_path = prepare_gui_database()
    root = Tk()
    root.title("Powertrade Crawler 数据浏览器")
    root.geometry("1440x900")
    root.minsize(1060, 680)
    build_gui(root, db_path)
    root.mainloop()
