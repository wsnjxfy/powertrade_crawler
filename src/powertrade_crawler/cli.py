import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger

from powertrade_crawler.clients.elecheck import ElecheckUnauthorizedError
from powertrade_crawler.clients.entsoe import EntsoeClient
from powertrade_crawler.credentials import (
    get_credential,
    get_credentials_path,
    save_credential,
)
from powertrade_crawler.elecheck_auth import (
    cache_elecheck_authorization,
    resolve_elecheck_authorization,
    set_elecheck_authorization_for_current_process,
)
from powertrade_crawler.models import (
    ElecheckClearPriceRecord,
    ElecheckMechanismElectricityPriceRecord,
    ElecheckPurchasingProvinceRecord,
    ElecheckPurchasingRecord,
    EntsoeRecord,
    GridStatusDatasetMetadataRecord,
    GridStatusRecord,
    GzpecNewsRecord,
    MarketRecord,
)
from powertrade_crawler.registry import get_spider, list_spiders
from powertrade_crawler.spiders.entsoe import (
    ENTSOE_BIDDING_ZONES,
    get_entsoe_request_config,
    load_entsoe_request_configs,
)
from powertrade_crawler.storage import (
    export_gridstatus_translation_tasks,
    export_table_to_csv,
    import_gridstatus_translations,
    init_db,
    list_tables,
    resolve_elecheck_area_code,
    upsert_entsoe_records,
    upsert_elecheck_clear_price_records,
    upsert_elecheck_mechanism_electricity_price_records,
    upsert_elecheck_purchasing_province_records,
    upsert_elecheck_purchasing_records,
    upsert_gridstatus_dataset_metadata_records,
    upsert_gridstatus_records,
    upsert_gzpec_news_records,
    upsert_records,
)

app = typer.Typer(help="Power trading data crawler.")

ELECHECK_SPIDERS = {
    "elecheck_clear_price",
    "elecheck_mechanism_electricity_price",
    "elecheck_purchasing_national_month",
    "elecheck_purchasing_national_range",
    "elecheck_purchasing_province_list",
    "elecheck_purchasing_province_month",
}
ELECHECK_PURCHASING_SPIDERS = {
    "elecheck_purchasing_national_month",
    "elecheck_purchasing_national_range",
    "elecheck_purchasing_province_list",
    "elecheck_purchasing_province_month",
}
ENTSOE_SPIDERS = {config["name"] for config in load_entsoe_request_configs()}
CREDENTIAL_ALIASES = {
    "gridstatus": "gridstatus_api_key",
    "elecheck": "elecheck_authorization",
    "entsoe": "entsoe_security_token",
}


@app.command("help-commands")
def show_command_guide() -> None:
    """Show common commands, usage examples, spiders, and database tables."""
    typer.echo(
        "\n".join(
            [
                "Powertrade Crawler command guide",
                "",
                "Basic commands:",
                "  init-db",
                "    Initialize or update the local database schema.",
                "    Example: powertrade init-db",
                "",
                "  gui",
                "    Open the Powertrade data browser GUI.",
                "    Example: powertrade gui",
                "",
                "  list-spiders",
                "    List all available crawlers.",
                "    Example: powertrade list-spiders",
                "",
                "  crawl SPIDER_NAME",
                "    Run one crawler and write its records to the database.",
                "    Example: powertrade crawl gridstatus_datasets",
                "    Preview only: powertrade crawl gridstatus_datasets --dry-run",
                "    ENTSO-E day-ahead prices: powertrade crawl entsoe_day_ahead_prices "
                "--area DE-LU --start-date 2026-06-01 --end-date 2026-06-02 --dry-run",
                "    Elecheck recent: powertrade crawl elecheck_clear_price --period recent",
                "    Elecheck 7 days: powertrade crawl elecheck_clear_price --period 7d",
                "    Elecheck 30 days: powertrade crawl elecheck_clear_price --period 30d",
                "    Elecheck half year: powertrade crawl elecheck_clear_price --period half-year",
                "    Elecheck by area: powertrade crawl elecheck_clear_price "
                "--area 山东 --period half-year",
                "    Elecheck custom dates: powertrade crawl elecheck_clear_price "
                "--start-date 2026-05-10 --end-date 2026-06-10",
                "    Elecheck purchasing national: powertrade crawl "
                "elecheck_purchasing_national_month --month 2026-06",
                "    Elecheck purchasing national range: powertrade crawl "
                "elecheck_purchasing_national_range --start-month 2024-02",
                "    Elecheck purchasing province list: powertrade crawl "
                "elecheck_purchasing_province_list",
                "    Elecheck purchasing province: powertrade crawl "
                "elecheck_purchasing_province_month --area 江苏 --month 2026-06",
                "    Elecheck mechanism price: powertrade crawl "
                "elecheck_mechanism_electricity_price",
                "",
                "  list-tables",
                "    List all database tables.",
                "    Example: powertrade list-tables",
                "",
                "  export-csv TABLE_NAME -o OUTPUT.csv",
                "    Export a database table to CSV.",
                "    Example: powertrade export-csv gridstatus_dataset_metadata "
                "-o exports/gridstatus_dataset_metadata.csv",
                "",
                "Translation commands:",
                "  make-translation-tasks",
                "    Export GridStatus descriptions that need Chinese translation.",
                "    Example: powertrade make-translation-tasks",
                "    Limit rows: powertrade make-translation-tasks --limit 50",
                "",
                "  import-translations CSV_FILE",
                "    Import translated Chinese descriptions back into the database.",
                "    Example: powertrade import-translations "
                "translation_tasks/gridstatus_description_translation_tasks.csv",
                "    Preview only: powertrade import-translations "
                "translation_tasks/gridstatus_description_translation_tasks.csv --dry-run",
                "",
                "Available spiders:",
                *[f"  - {spider_name}" for spider_name in list_spiders()],
                "",
                "Available database tables:",
                *[f"  - {table_name}" for table_name in list_tables()],
                "",
                "Tip:",
                "  You can also run: powertrade --help",
                "  Or for a specific command: powertrade COMMAND --help",
            ]
        )
    )


@app.command("init-db")
def init_database() -> None:
    init_db()
    typer.echo("Database initialized.")


@app.command("list-spiders")
def list_available_spiders() -> None:
    for spider_name in list_spiders():
        typer.echo(spider_name)


@app.command("entsoe-datasets")
def list_entsoe_datasets() -> None:
    """List configured ENTSO-E datasets with bilingual titles."""
    for config in load_entsoe_request_configs():
        typer.echo(
            f"{config['name']}\t{config['title_zh']}\t{config['title_en']}\t"
            f"[{config['category']}]"
        )


@app.command("entsoe-describe")
def describe_entsoe_dataset(
    dataset: Annotated[str, typer.Argument(help="ENTSO-E dataset/spider name.")],
) -> None:
    """Describe one ENTSO-E dataset in Chinese and English."""
    try:
        config = get_entsoe_request_config(dataset)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"{config['title_zh']} / {config['title_en']}")
    typer.echo(f"Command: {config['name']}")
    typer.echo(f"Category: {config['category']}")
    typer.echo(f"中文意义: {config['meaning_zh']}")
    typer.echo(f"Meaning: {config['meaning_en']}")
    typer.echo(f"Domain mode: {config['domain_mode']}")
    typer.echo(f"API parameters: {config['params']}")


@app.command("entsoe-areas")
def list_entsoe_areas() -> None:
    """List ENTSO-E area aliases bundled with this project."""
    for alias, eic in sorted(ENTSOE_BIDDING_ZONES.items()):
        typer.echo(f"{alias}\t{eic}")


@app.command("entsoe-query")
def query_entsoe_raw(
    param: Annotated[
        list[str],
        typer.Option(
            "--param",
            help="Repeatable official ENTSO-E query parameter in KEY=VALUE form.",
        ),
    ],
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Optional UTC query start."),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Optional UTC query end."),
    ] = None,
    output_path: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Optional JSON Lines output path."),
    ] = None,
    entsoe_token: Annotated[
        str | None,
        typer.Option("--entsoe-token", help="Temporary token override; defaults to .auth."),
    ] = None,
) -> None:
    """Run an advanced ENTSO-E request not yet included in the dataset catalog."""
    try:
        params = parse_key_value_options(param)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if (start_date is None) != (end_date is None):
        raise typer.BadParameter("--start-date and --end-date must be provided together.")

    client = EntsoeClient(security_token=entsoe_token)
    try:
        if start_date and end_date:
            params["periodStart"] = client.format_period(parse_entsoe_cli_datetime(start_date))
            params["periodEnd"] = client.format_period(parse_entsoe_cli_datetime(end_date))
        rows = client.query_document(params)
    finally:
        client.close()

    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        typer.echo(f"Wrote {len(lines)} ENTSO-E rows to {output_path}")
        return
    for line in lines:
        typer.echo(line)


@app.command("list-tables")
def list_database_tables() -> None:
    for table_name in list_tables():
        typer.echo(table_name)


@app.command("credentials-status")
def show_credentials_status() -> None:
    """Show whether each local credential is configured without revealing values."""
    typer.echo(f"Credential file: {get_credentials_path()}")
    for alias, credential_name in CREDENTIAL_ALIASES.items():
        status = "configured" if get_credential(credential_name) else "missing"
        typer.echo(f"{alias}: {status}")


@app.command("set-credential")
def set_credential(
    name: Annotated[
        str,
        typer.Argument(help="Credential name: gridstatus, elecheck, or entsoe."),
    ],
) -> None:
    """Securely save one credential in .auth/credentials.json."""
    normalized = name.strip().lower()
    credential_name = CREDENTIAL_ALIASES.get(normalized)
    if credential_name is None:
        available = ", ".join(CREDENTIAL_ALIASES)
        raise typer.BadParameter(f"Unknown credential: {name}. Available: {available}.")
    value = typer.prompt(f"{normalized} credential", hide_input=True).strip()
    if not value:
        raise typer.BadParameter("Credential cannot be empty.")
    path = save_credential(credential_name, value)
    typer.echo(f"Saved {normalized} credential to {path}")


@app.command("gui")
def open_gui() -> None:
    from powertrade_crawler.gui import launch_gui

    launch_gui()


@app.command("export-csv")
def export_csv(
    table_name: Annotated[str, typer.Argument(help="Database table name to export.")],
    output_path: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="CSV output path. Defaults to exports/{table}.csv."),
    ] = None,
) -> None:
    output = output_path or Path("exports") / f"{table_name}.csv"
    try:
        row_count = export_table_to_csv(table_name, output)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Exported {row_count} rows to {output}")


@app.command("make-translation-tasks")
def make_translation_tasks(
    output_path: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="CSV output path. Defaults to translation_tasks/gridstatus_description_translation_tasks.csv.",
        ),
    ] = Path("translation_tasks") / "gridstatus_description_translation_tasks.csv",
    limit: Annotated[int | None, typer.Option("--limit", help="Maximum rows to export.")] = None,
    include_done: Annotated[
        bool,
        typer.Option("--include-done", help="Also export rows that already have Chinese translations."),
    ] = False,
) -> None:
    row_count = export_gridstatus_translation_tasks(
        output_path=output_path,
        limit=limit,
        include_done=include_done,
    )
    typer.echo(f"Exported {row_count} translation tasks to {output_path}")


@app.command("import-translations")
def import_translations(
    input_path: Annotated[
        Path,
        typer.Argument(
            help="CSV file with dataset_id and description_chinese columns.",
        ),
    ] = Path("translation_tasks") / "gridstatus_description_translation_tasks.csv",
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Import without asking for confirmation."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview import counts without writing the database."),
    ] = False,
) -> None:
    try:
        stats = import_gridstatus_translations(input_path=input_path, dry_run=True)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"Translation CSV: {input_path}")
    typer.echo(f"Total rows: {stats['total_rows']}")
    typer.echo(f"Will update: {stats['updated']}")
    typer.echo(f"Skipped empty translations: {stats['skipped_empty_translation']}")
    typer.echo(f"Skipped error status: {stats['skipped_error_status']}")
    typer.echo(f"Skipped missing dataset_id: {stats['skipped_missing_dataset_id']}")
    typer.echo(f"Skipped dataset not found: {stats['skipped_not_found']}")

    if dry_run:
        typer.echo("Dry run only. Database was not changed.")
        return
    if stats["updated"] == 0:
        typer.echo("Nothing to import.")
        return
    if not yes and not typer.confirm("Write these Chinese translations to the database?"):
        typer.echo("Import cancelled.")
        return

    final_stats = import_gridstatus_translations(input_path=input_path, dry_run=False)
    typer.echo(f"Done. Imported {final_stats['updated']} Chinese descriptions.")


@app.command("crawl")
def crawl(
    spider_name: Annotated[str, typer.Argument(help="Spider name, for example demo-market.")],
    dry_run: Annotated[bool, typer.Option(help="Print records without writing database.")] = False,
    period: Annotated[
        str | None,
        typer.Option(
            "--period",
            help="Elecheck date shortcut: recent, 7d, 30d, or half-year.",
        ),
    ] = None,
    area_code: Annotated[
        str | None,
        typer.Option("--area-code", help="Elecheck area code, for example 320000000000."),
    ] = None,
    area: Annotated[
        str | None,
        typer.Option("--area", help="Elecheck area alias, for example 山东."),
    ] = None,
    month: Annotated[
        str | None,
        typer.Option("--month", help="Elecheck purchasing data month, for example 2026-06."),
    ] = None,
    start_month: Annotated[
        str | None,
        typer.Option(
            "--start-month",
            help="Elecheck purchasing chart start month, for example 2025-01.",
        ),
    ] = None,
    end_month: Annotated[
        str | None,
        typer.Option(
            "--end-month",
            help="Elecheck purchasing chart end month, for example 2025-12.",
        ),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Elecheck custom start date, YYYY-MM-DD."),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Elecheck custom end date, YYYY-MM-DD."),
    ] = None,
    daily: Annotated[
        bool,
        typer.Option(
            "--daily",
            help=(
                "For elecheck_clear_price, request each day separately and store all daily "
                "records for the selected date range."
            ),
        ),
    ] = False,
    authorization: Annotated[
        str | None,
        typer.Option(
            "--authorization",
            help=(
                "Authorization token override for Elecheck spiders. "
                "It is cached locally until Elecheck returns HTTP 401."
            ),
        ),
    ] = None,
    in_area_code: Annotated[
        str | None,
        typer.Option("--in-area-code", help="ENTSO-E input/from domain EIC code."),
    ] = None,
    in_area: Annotated[
        str | None,
        typer.Option("--in-area", help="ENTSO-E input/from area alias, for example FR."),
    ] = None,
    out_area_code: Annotated[
        str | None,
        typer.Option("--out-area-code", help="ENTSO-E output/to domain EIC code."),
    ] = None,
    out_area: Annotated[
        str | None,
        typer.Option("--out-area", help="ENTSO-E output/to area alias, for example DE-LU."),
    ] = None,
    entsoe_token: Annotated[
        str | None,
        typer.Option(
            "--entsoe-token",
            help="Temporary ENTSO-E token override. Defaults to .auth/credentials.json.",
        ),
    ] = None,
    psr_type: Annotated[
        str | None,
        typer.Option(
            "--psr-type",
            help="Optional ENTSO-E production type code, for example B16 for solar.",
        ),
    ] = None,
    entsoe_param: Annotated[
        list[str] | None,
        typer.Option(
            "--entsoe-param",
            help="Repeatable raw ENTSO-E parameter override in KEY=VALUE form.",
        ),
    ] = None,
) -> None:
    spider_kwargs = {}
    if spider_name == "elecheck_clear_price":
        configure_optional_elecheck_authorization(authorization)
        resolved_area_code = area_code
        if resolved_area_code is None and area:
            try:
                resolved_area_code = resolve_elecheck_area_code(area)
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
        spider_kwargs = {
            "period": period,
            "area_code": resolved_area_code,
            "start_date": start_date,
            "end_date": end_date,
            "daily": daily,
        }
    elif spider_name == "elecheck_purchasing_national_month":
        ensure_required_elecheck_authorization(authorization)
        if any([period, area_code, area, start_date, end_date, start_month, end_month, daily]):
            raise typer.BadParameter(
                "--period, --area, --area-code, --start-date, --end-date, "
                "--start-month, --end-month, and --daily "
                "are not supported by elecheck_purchasing_national_month."
            )
        spider_kwargs = {"data_month": month}
    elif spider_name == "elecheck_purchasing_national_range":
        ensure_required_elecheck_authorization(authorization)
        if any([period, area_code, area, month, start_date, end_date, daily]):
            raise typer.BadParameter(
                "--period, --area, --area-code, --month, --start-date, --end-date, "
                "and --daily are not supported by elecheck_purchasing_national_range."
            )
        spider_kwargs = {"start_month": start_month, "end_month": end_month}
    elif spider_name == "elecheck_purchasing_province_list":
        configure_optional_elecheck_authorization(authorization)
        if any([period, area_code, area, month, start_date, end_date, start_month, end_month, daily]):
            raise typer.BadParameter(
                "elecheck_purchasing_province_list does not support query options."
            )
    elif spider_name == "elecheck_purchasing_province_month":
        ensure_required_elecheck_authorization(authorization)
        if any([period, area_code, start_date, end_date, daily]):
            raise typer.BadParameter(
                "--period, --area-code, --start-date, --end-date, and --daily "
                "are not supported by elecheck_purchasing_province_month."
            )
        if not area:
            raise typer.BadParameter("--area is required for elecheck_purchasing_province_month.")
        if (start_month is None) != (end_month is None):
            raise typer.BadParameter("--start-month and --end-month must be provided together.")
        spider_kwargs = {
            "data_month": month,
            "province_name": area,
            "start_month": start_month,
            "end_month": end_month,
        }
    elif spider_name == "elecheck_mechanism_electricity_price":
        configure_optional_elecheck_authorization(authorization)
        if any([
            period,
            area_code,
            area,
            month,
            start_month,
            end_month,
            start_date,
            end_date,
            daily,
        ]):
            raise typer.BadParameter(
                "elecheck_mechanism_electricity_price does not support query options."
            )
    elif spider_name in ENTSOE_SPIDERS:
        if any([period, month, start_month, end_month, daily, authorization]):
            raise typer.BadParameter(
                "--period, --month, --start-month, --end-month, --daily, and "
                "--authorization are not supported by ENTSO-E spiders."
            )
        if spider_name == "entsoe_day_ahead_prices":
            if any([in_area_code, in_area, out_area_code, out_area, psr_type, entsoe_param]):
                raise typer.BadParameter(
                    "The compatibility entsoe_day_ahead_prices spider only supports "
                    "--area/--area-code and date options."
                )
            spider_kwargs = {
                "area_code": area_code,
                "area": area,
                "start_date": start_date,
                "end_date": end_date,
                "security_token": entsoe_token,
            }
        else:
            try:
                extra_params = parse_key_value_options(entsoe_param or [])
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
            spider_kwargs = {
                "area_code": area_code,
                "area": area,
                "in_area_code": in_area_code,
                "in_area": in_area,
                "out_area_code": out_area_code,
                "out_area": out_area,
                "start_date": start_date,
                "end_date": end_date,
                "psr_type": psr_type,
                "extra_params": extra_params,
                "security_token": entsoe_token,
            }
    elif any([
        period,
        area_code,
        area,
        month,
        start_month,
        end_month,
        start_date,
        end_date,
        daily,
        authorization,
        entsoe_token,
        in_area_code,
        in_area,
        out_area_code,
        out_area,
        psr_type,
        entsoe_param,
    ]):
        raise typer.BadParameter(
            "--period, --area, --area-code, --month, --start-date, --end-date, "
            "--start-month, --end-month, --daily, --authorization, and --entsoe-token "
            "are only supported by Elecheck or ENTSO-E spiders."
        )

    records = crawl_with_optional_elecheck_auth_retry(spider_name, spider_kwargs)
    logger.info("{} produced {} records", spider_name, len(records))
    if dry_run:
        for record in records:
            typer.echo(record.model_dump_json())
        return
    if all(isinstance(record, MarketRecord) for record in records):
        written = upsert_records(records)
    elif all(isinstance(record, EntsoeRecord) for record in records):
        written = upsert_entsoe_records(records)
    elif all(isinstance(record, GzpecNewsRecord) for record in records):
        written = upsert_gzpec_news_records(records)
    elif all(isinstance(record, GridStatusRecord) for record in records):
        written = upsert_gridstatus_records(records)
    elif all(isinstance(record, GridStatusDatasetMetadataRecord) for record in records):
        written = upsert_gridstatus_dataset_metadata_records(records)
    elif all(isinstance(record, ElecheckClearPriceRecord) for record in records):
        written = upsert_elecheck_clear_price_records(records)
    elif all(isinstance(record, ElecheckPurchasingProvinceRecord) for record in records):
        written = upsert_elecheck_purchasing_province_records(records)
    elif all(isinstance(record, ElecheckPurchasingRecord) for record in records):
        written = upsert_elecheck_purchasing_records(records)
    elif all(isinstance(record, ElecheckMechanismElectricityPriceRecord) for record in records):
        written = upsert_elecheck_mechanism_electricity_price_records(records)
    else:
        raise typer.BadParameter("A spider must return one record type per crawl.")
    typer.echo(f"Done. Upserted {written} records.")


def parse_key_value_options(options: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for option in options:
        key, separator, value = option.partition("=")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(
                f"Invalid --entsoe-param value: {option}. Expected KEY=VALUE."
            )
        parsed[key.strip()] = value.strip()
    return parsed


def parse_entsoe_cli_datetime(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value)
    if len(value) == 12 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d%H%M")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def crawl_with_optional_elecheck_auth_retry(spider_name: str, spider_kwargs: dict) -> list:
    prompted_for_authorization = False
    while True:
        spider = get_spider(spider_name, **spider_kwargs)
        try:
            return list(spider.crawl())
        except ElecheckUnauthorizedError as exc:
            if spider_name not in ELECHECK_SPIDERS or prompted_for_authorization:
                raise typer.BadParameter(
                    "Elecheck returned HTTP 401. The authorization token is missing, "
                    "expired, or invalid."
                ) from exc
            typer.echo(
                "Elecheck returned HTTP 401. Paste the raw authorization JWT from the "
                "WeChat mini program request header, without Bearer."
            )
            prompt_and_cache_elecheck_authorization()
            prompted_for_authorization = True
        finally:
            spider.close()


def configure_optional_elecheck_authorization(authorization_override: str | None = None) -> None:
    authorization = resolve_elecheck_authorization(authorization_override)
    if not authorization:
        return

    cache_elecheck_authorization(authorization)
    set_elecheck_authorization_for_current_process(authorization)


def ensure_required_elecheck_authorization(authorization_override: str | None = None) -> None:
    authorization = resolve_elecheck_authorization(authorization_override)
    if not authorization:
        typer.echo(
            "Elecheck authorization is required for purchasing spiders. Paste the raw "
            "authorization JWT from the WeChat mini program request header, without Bearer."
        )
        prompt_and_cache_elecheck_authorization()
        return

    cache_elecheck_authorization(authorization)
    set_elecheck_authorization_for_current_process(authorization)


def prompt_and_cache_elecheck_authorization() -> None:
    authorization = typer.prompt("authorization", hide_input=True).strip()
    if not authorization:
        raise typer.BadParameter(
            "authorization is required after Elecheck returned HTTP 401."
        )
    cache_elecheck_authorization(authorization)
    set_elecheck_authorization_for_current_process(authorization)


if __name__ == "__main__":
    app()
