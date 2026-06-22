from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus


MYSQL_HOST = ""
MYSQL_PORT = ""
MYSQL_DB = ""
MYSQL_USER = ""
MYSQL_PASSWORD = ""

POSTGRES_HOST = ""
POSTGRES_PORT = ""
POSTGRES_DB = "metabase"
POSTGRES_USER = ""
POSTGRES_PASSWORD = ""

TARGET_SCHEMA = ""
MYSQL_CHARSET = ""
PAGE_SIZE = 5000
DRY_RUN = False
RUN_SCUD_STAFF_REPORT_PROCEDURE = True
RUN_REPORTS_PRESENSE_PROCEDURE = True
SCUD_STAFF_REPORT_PROCEDURE = "scud.sp_staff_report_base_with_valid_reason"
REPORTS_PRESENSE_PROCEDURE = "reports.sp_presense_with_valid_reason"
LOG_FILE = Path(__file__).with_name("bitrix_etl_template_log.txt")


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("bitrix_etl")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


log = setup_logging()


@dataclass(frozen=True)
class BitrixConfig:
    mysql_url: str
    postgres_url: str
    target_schema: str
    page_size: int
    dry_run: bool
    run_scud_staff_report_procedure: bool
    run_reports_presense_procedure: bool


@dataclass(frozen=True)
class QuerySpec:
    label: str
    table: str
    columns: Tuple[str, ...]
    mode: str
    key_column: Optional[str]
    sql: str


def require_filled(name: str, value: str) -> str:
    if not str(value).strip() or value.startswith("<") and value.endswith(">"):
        raise RuntimeError(f"fail {name}")
    return value


def require_port(name: str, value: str) -> str:
    port = require_filled(name, value).strip()
    return port


def normalize_mysql_user(value: str) -> str:
    user = require_filled("MYSQL_USER", value).strip().strip("'\"")
    if "@" in user:
        user = user.split("@", 1)[0].strip().strip("'\"")
    return user


def build_mysql_url() -> str:
    host = require_filled("MYSQL_HOST", MYSQL_HOST)
    port = require_port("MYSQL_PORT", MYSQL_PORT)
    db_name = require_filled("MYSQL_DB", MYSQL_DB)
    user = quote_plus(normalize_mysql_user(MYSQL_USER))
    password = quote_plus(require_filled("MYSQL_PASSWORD", MYSQL_PASSWORD))
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset={MYSQL_CHARSET}"


def build_postgres_url() -> str:
    host = require_filled("POSTGRES_HOST", POSTGRES_HOST)
    port = require_port("POSTGRES_PORT", POSTGRES_PORT)
    db_name = require_filled("POSTGRES_DB", POSTGRES_DB)
    user = quote_plus(require_filled("POSTGRES_USER", POSTGRES_USER))
    password = quote_plus(require_filled("POSTGRES_PASSWORD", POSTGRES_PASSWORD))
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def load_config() -> BitrixConfig:
    return BitrixConfig(
        mysql_url=build_mysql_url(),
        postgres_url=build_postgres_url(),
        target_schema=TARGET_SCHEMA,
        page_size=PAGE_SIZE,
        dry_run=DRY_RUN,
        run_scud_staff_report_procedure=RUN_SCUD_STAFF_REPORT_PROCEDURE,
        run_reports_presense_procedure=RUN_REPORTS_PRESENSE_PROCEDURE,
    )


ABSENSE_SQL = """
SELECT
    p_user.VALUE AS user_id,
    e.XML_ID AS absence_type_xml_id,
    DATE(e1.ACTIVE_FROM) AS date_start,
    DATE(e1.ACTIVE_TO) AS date_end
FROM b_iblock_element e1
INNER JOIN b_iblock_element_property p_user
    ON e1.ID = p_user.IBLOCK_ELEMENT_ID AND p_user.IBLOCK_PROPERTY_ID = 4
INNER JOIN b_iblock_element_property p_type
    ON e1.ID = p_type.IBLOCK_ELEMENT_ID AND p_type.IBLOCK_PROPERTY_ID = 7
LEFT JOIN b_iblock_property_enum e
    ON p_type.VALUE = e.ID AND e.PROPERTY_ID = 7
WHERE e1.IBLOCK_ID = 3
  AND e1.ACTIVE = 'Y'
"""


DEPARTMENT_SQL = """
SELECT
    ID AS department_id,
    IBLOCK_SECTION_ID AS parent_department_id,
    NAME AS department_full_name,
    DEPTH_LEVEL AS level,
    uts.UF_HEAD AS head_id
FROM b_iblock_section s
LEFT JOIN b_uts_iblock_5_section uts ON s.ID = uts.VALUE_ID
WHERE IBLOCK_ID = 5
  AND ACTIVE = 'Y'
"""


EMPLOYEES_SQL = """
SELECT
    uu.VALUE_ID AS bitrix_employee_id,
    uu.VALUE_INT AS department_id,
    TRIM(u.last_name) AS last_name,
    TRIM(u.NAME) AS first_name,
    COALESCE(TRIM(u.second_name), '') AS middle_name,
    CONCAT(TRIM(u.last_name), TRIM(u.NAME), COALESCE(TRIM(u.second_name), '')) AS key_fio,
    u.EMAIL AS email,
    u.WORK_POSITION AS position_name,
    buu.UF_1C_PR51E769A663EB AS main_organization_name
FROM b_utm_user uu
LEFT JOIN b_user u ON uu.VALUE_ID = u.ID
LEFT JOIN b_uts_user buu ON buu.VALUE_ID = u.ID
WHERE u.ACTIVE = 'Y'
  AND u.last_name IS NOT NULL
  AND u.last_name <> ''
  AND u.NAME IS NOT NULL
  AND u.NAME <> ''
"""

LOCAL_TRIP_SQL = """
SELECT
    UF_USER AS bitrix_employee_id,
    DATE(UF_TRIP_DATE_START) AS local_trip_date,
    CONCAT(LPAD(HOUR(UF_TRIP_DATE_START), 2, '0'), ':', LPAD(MINUTE(UF_TRIP_DATE_START), 2, '0')) AS local_trip_start_time,
    CONCAT(LPAD(HOUR(UF_TRIP_DATE_END), 2, '0'), ':', LPAD(MINUTE(UF_TRIP_DATE_END), 2, '0')) AS local_trip_end_time,
    TIMESTAMPDIFF(MINUTE, UF_TRIP_DATE_START, UF_TRIP_DATE_END) / 60 AS local_trip_duration_hrs
FROM gs_requests_localtrip
WHERE UF_STATUS = 37
"""


REMOTE_WORK_SQL = """
SELECT
    p_sotrudnik.VALUE AS bitrix_employee_id,
    p_date_start.VALUE AS remote_work_date_start,
    p_date_end.VALUE AS remote_work_date_end
FROM b_bp_workflow_state s
LEFT JOIN b_bp_workflow_instance i ON s.ID = i.ID
INNER JOIN b_iblock_element e1 ON s.DOCUMENT_ID = e1.ID
LEFT JOIN b_iblock_element_property p_sotrudnik ON e1.ID = p_sotrudnik.IBLOCK_ELEMENT_ID
    AND p_sotrudnik.IBLOCK_PROPERTY_ID = 476
LEFT JOIN b_user u ON p_sotrudnik.VALUE = u.ID
LEFT JOIN b_iblock_element_property p_date_start ON e1.ID = p_date_start.IBLOCK_ELEMENT_ID
    AND p_date_start.IBLOCK_PROPERTY_ID = 481
LEFT JOIN b_iblock_element_property p_date_end ON e1.ID = p_date_end.IBLOCK_ELEMENT_ID
    AND p_date_end.IBLOCK_PROPERTY_ID = 482
WHERE e1.IBLOCK_ID = 76
  AND s.STATE_TITLE IN (
      'Заявка выполнена',
      'Ознакомление с документами об удаленной работе',
      'Подготовить документы для дистанционной работы'
  )
  AND p_sotrudnik.VALUE IS NOT NULL
  AND p_date_start.VALUE IS NOT NULL
  AND p_date_end.VALUE IS NOT NULL
"""


QUERY_SPECS = (
    QuerySpec(
        label="Absense",
        table="absense",
        columns=("user_id", "absence_type_xml_id", "date_start", "date_end"),
        mode="replace",
        key_column=None,
        sql=ABSENSE_SQL,
    ),
    QuerySpec(
        label="Departments",
        table="departments",
        columns=("department_id", "parent_department_id", "department_full_name", "level", "head_id"),
        mode="upsert",
        key_column="department_id",
        sql=DEPARTMENT_SQL,
    ),
    QuerySpec(
        label="Employees",
        table="employees",
        columns=(
            "bitrix_employee_id",
            "department_id",
            "last_name",
            "first_name",
            "middle_name",
            "key_fio",
            "email",
            "position_name",
            "main_organization_name",
        ),
        mode="upsert",
        key_column="bitrix_employee_id",
        sql=EMPLOYEES_SQL,
    ),
    QuerySpec(
        label="Local trip",
        table="local_trip",
        columns=(
            "bitrix_employee_id",
            "local_trip_date",
            "local_trip_start_time",
            "local_trip_end_time",
            "local_trip_duration_hrs",
        ),
        mode="replace",
        key_column=None,
        sql=LOCAL_TRIP_SQL,
    ),
    QuerySpec(
        label="Remote work",
        table="remote_work",
        columns=("bitrix_employee_id", "remote_work_date_start", "remote_work_date_end"),
        mode="replace",
        key_column=None,
        sql=REMOTE_WORK_SQL,
    ),
)


TABLE_DDLS = (
    """
    CREATE TABLE IF NOT EXISTS {schema}.absense (
        user_id bigint,
        absence_type_xml_id varchar,
        date_start date,
        date_end date
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.departments (
        department_id bigint PRIMARY KEY,
        parent_department_id bigint,
        department_full_name varchar,
        level integer,
        head_id bigint
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.employees (
        bitrix_employee_id bigint PRIMARY KEY,
        department_id bigint,
        last_name varchar,
        first_name varchar,
        middle_name varchar,
        key_fio varchar,
        email varchar,
        position_name varchar,
        main_organization_name varchar
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.local_trip (
        bitrix_employee_id bigint,
        local_trip_date date,
        local_trip_start_time time,
        local_trip_end_time time,
        local_trip_duration_hrs numeric
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.remote_work (
        bitrix_employee_id bigint,
        remote_work_date_start date,
        remote_work_date_end date
    )
    """,
)


TABLE_COLUMNS = (
    (
        "absense",
        (
            ("user_id", "bigint"),
            ("absence_type_xml_id", "varchar"),
            ("date_start", "date"),
            ("date_end", "date"),
        ),
    ),
    (
        "departments",
        (
            ("department_id", "bigint"),
            ("parent_department_id", "bigint"),
            ("department_full_name", "varchar"),
            ("level", "integer"),
            ("head_id", "bigint"),
        ),
    ),
    (
        "employees",
        (
            ("bitrix_employee_id", "bigint"),
            ("department_id", "bigint"),
            ("last_name", "varchar"),
            ("first_name", "varchar"),
            ("middle_name", "varchar"),
            ("key_fio", "varchar"),
            ("email", "varchar"),
            ("position_name", "varchar"),
            ("main_organization_name", "varchar"),
        ),
    ),
    (
        "local_trip",
        (
            ("bitrix_employee_id", "bigint"),
            ("local_trip_date", "date"),
            ("local_trip_start_time", "time"),
            ("local_trip_end_time", "time"),
            ("local_trip_duration_hrs", "numeric"),
        ),
    ),
    (
        "remote_work",
        (
            ("bitrix_employee_id", "bigint"),
            ("remote_work_date_start", "date"),
            ("remote_work_date_end", "date"),
        ),
    ),
)


COLUMN_TYPE_FALLBACKS = {
    "user_id": "bigint",
    "absence_type_xml_id": "varchar",
    "date_start": "date",
    "date_end": "date",
    "department_id": "bigint",
    "parent_department_id": "bigint",
    "department_full_name": "varchar",
    "level": "integer",
    "head_id": "bigint",
    "bitrix_employee_id": "bigint",
    "last_name": "varchar",
    "first_name": "varchar",
    "middle_name": "varchar",
    "key_fio": "varchar",
    "email": "varchar",
    "position_name": "varchar",
    "main_organization_name": "varchar",
    "local_trip_date": "date",
    "local_trip_start_time": "time",
    "local_trip_end_time": "time",
    "local_trip_duration_hrs": "numeric",
    "remote_work_date_start": "date",
    "remote_work_date_end": "date",
}


def import_sqlalchemy():
    try:
        from sqlalchemy import create_engine, text
    except ModuleNotFoundError as exc:
        raise RuntimeError("fail: sqlalchemy, pymysql, psycopg2-binary") from exc
    return create_engine, text


def create_source_engine(cfg: BitrixConfig):
    try:
        import pymysql
    except ModuleNotFoundError as exc:
        raise RuntimeError("fail pymysql") from exc
    create_engine, _ = import_sqlalchemy()
    return create_engine(cfg.mysql_url, pool_pre_ping=True)


def create_target_engine(cfg: BitrixConfig):
    try:
        import psycopg2
    except ModuleNotFoundError as exc:
        raise RuntimeError("fail psycopg2-binary") from exc
    create_engine, _ = import_sqlalchemy()
    return create_engine(cfg.postgres_url, pool_pre_ping=True)


def clean_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value
    return value


def normalize_row(raw_row: Dict[str, Any], columns: Tuple[str, ...]) -> Dict[str, Any]:
    row_lower = {str(key).lower(): value for key, value in raw_row.items()}
    return {column: clean_value(row_lower.get(column)) for column in columns}


def chunks(rows: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for idx in range(0, len(rows), size):
        yield rows[idx:idx + size]


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_name(schema: str, table: str) -> str:
    return f"{ident(schema)}.{ident(table)}"


def add_column_sql(schema: str, table: str, column: str, data_type: str) -> str:
    return f"ALTER TABLE {table_name(schema, table)} ADD COLUMN IF NOT EXISTS {ident(column)} {data_type}"


def drop_table_sql(schema: str, table: str) -> str:
    return f"DROP TABLE IF EXISTS {table_name(schema, table)}"


def create_table_sql(schema: str, table: str) -> str:
    columns_sql = ", ".join(
        f"{ident(column)} {data_type}"
        for column, data_type in table_column_types(table).items()
    )
    return f"CREATE TABLE {table_name(schema, table)} ({columns_sql})"


def table_column_types(table: str) -> Dict[str, str]:
    for known_table, columns in TABLE_COLUMNS:
        if known_table == table:
            return dict(columns)
    raise RuntimeError(f"Unknown target table: {table}")


def add_column_sqls_for_spec(schema: str, spec: QuerySpec) -> Tuple[str, ...]:
    column_types = dict(COLUMN_TYPE_FALLBACKS)
    column_types.update(table_column_types(spec.table))
    missing_types = [column for column in spec.columns if column not in column_types]
    if missing_types:
        raise RuntimeError(f"{spec.label}: missing PostgreSQL column types: {', '.join(missing_types)}")
    return tuple(add_column_sql(schema, spec.table, column, column_types[column]) for column in spec.columns)


def ensure_spec_columns(conn, schema: str, spec: QuerySpec) -> None:
    _, text = import_sqlalchemy()
    for sql in add_column_sqls_for_spec(schema, spec):
        conn.execute(text(sql))


def temp_table_name(table: str) -> str:
    return ident(f"tmp_bitrix_{table}")


def column_list(columns: Tuple[str, ...]) -> str:
    return ", ".join(ident(column) for column in columns)


def values_list(columns: Tuple[str, ...]) -> str:
    return ", ".join(f":{column}" for column in columns)


PRESERVE_EXISTING_ON_NULL_COLUMNS = {"main_organization_name"}


def update_assignment_sql(column: str) -> str:
    if column in PRESERVE_EXISTING_ON_NULL_COLUMNS:
        return f"{ident(column)} = COALESCE(s.{ident(column)}, t.{ident(column)})"
    return f"{ident(column)} = s.{ident(column)}"


def changed_condition_sql(column: str) -> str:
    if column in PRESERVE_EXISTING_ON_NULL_COLUMNS:
        return f"(s.{ident(column)} IS NOT NULL AND t.{ident(column)} IS DISTINCT FROM s.{ident(column)})"
    return f"t.{ident(column)} IS DISTINCT FROM s.{ident(column)}"


def count_rows(conn, schema: str, table: str) -> int:
    _, text = import_sqlalchemy()
    return int(conn.execute(text(f"SELECT COUNT(*) FROM {table_name(schema, table)}")).scalar() or 0)


def dedupe_rows(rows: List[Dict[str, Any]], key_column: str) -> Tuple[List[Dict[str, Any]], int]:
    by_key: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []
    skipped = 0

    for row in rows:
        key = clean_value(row.get(key_column))
        if key is None:
            skipped += 1
            continue
        row[key_column] = key
        if key not in by_key:
            order.append(key)
        by_key[key] = row

    deduped = [by_key[key] for key in order]
    duplicates = len(rows) - len(deduped) - skipped
    return deduped, duplicates


def mysql_select_denied_hint(exc: Exception) -> Optional[str]:
    message = str(exc)
    if "SELECT command denied" not in message:
        return None

    match = re.search(r"for table ['\"]?([^'\"\)]+)", message)
    table = match.group(1) if match else ""
    db_name = MYSQL_DB.strip() or "sitemanager"
    target = table if "." in table else f"{db_name}.{table}" if table else f"{db_name}.*"
    return f"MySQL SELECT denied for {target}. Ask DBA to grant SELECT on {target} or {db_name}.* for this user from runner IP."


def fetch_rows(source_engine, spec: QuerySpec) -> List[Dict[str, Any]]:
    _, text = import_sqlalchemy()
    log.info(f"{spec.label}: fetching from Bitrix")
    with source_engine.connect() as conn:
        try:
            result = conn.execute(text(spec.sql))
            rows = [normalize_row(dict(row), spec.columns) for row in result.mappings()]
        except Exception as exc:
            hint = mysql_select_denied_hint(exc)
            if hint:
                log.error(f"{spec.label}: {hint}")
            raise
    log.info(f"{spec.label}: fetched {len(rows)}")
    return rows


def check_source_connection(source_engine) -> None:
    _, text = import_sqlalchemy()
    log.info("Checking MySQL connection")
    try:
        with source_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        message = str(exc)
        if "Access denied" in message:
            if "@" in MYSQL_USER:
                log.error("MYSQL_USER must be only login without @host. Example: user_reader")
            log.error("MySQL access denied. Check MYSQL_USER, MYSQL_PASSWORD and that this user is allowed from this server IP.")
        raise
    log.info("MySQL connection OK")


def check_target_connection(target_engine) -> None:
    _, text = import_sqlalchemy()
    log.info("Checking PostgreSQL connection")
    with target_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    log.info("PostgreSQL connection OK")


def ensure_schema_and_tables(conn, schema: str) -> None:
    _, text = import_sqlalchemy()
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {ident(schema)}"))
    schema_name = ident(schema)
    for ddl in TABLE_DDLS:
        conn.execute(text(ddl.format(schema=schema_name)))
    log.info("Checking PostgreSQL table columns")
    for table, columns in TABLE_COLUMNS:
        for column, data_type in columns:
            conn.execute(text(add_column_sql(schema, table, column, data_type)))


def insert_rows(conn, destination: str, columns: Tuple[str, ...], rows: List[Dict[str, Any]], page_size: int) -> None:
    if not rows:
        return
    _, text = import_sqlalchemy()
    sql = text(f"INSERT INTO {destination} ({column_list(columns)}) VALUES ({values_list(columns)})")
    for batch in chunks(rows, page_size):
        conn.execute(sql, batch)


def replace_table(conn, cfg: BitrixConfig, spec: QuerySpec, rows: List[Dict[str, Any]]) -> None:
    _, text = import_sqlalchemy()
    destination = table_name(cfg.target_schema, spec.table)
    before = count_rows(conn, cfg.target_schema, spec.table)
    conn.execute(text(drop_table_sql(cfg.target_schema, spec.table)))
    conn.execute(text(create_table_sql(cfg.target_schema, spec.table)))
    ensure_spec_columns(conn, cfg.target_schema, spec)
    insert_rows(conn, destination, spec.columns, rows, cfg.page_size)
    after = count_rows(conn, cfg.target_schema, spec.table)
    log.info(f"{spec.label}: replaced {before} -> {after}")


def upsert_table(conn, cfg: BitrixConfig, spec: QuerySpec, rows: List[Dict[str, Any]]) -> None:
    if not spec.key_column:
        raise RuntimeError(f"{spec.label}: key column is not set")

    _, text = import_sqlalchemy()
    rows, duplicates = dedupe_rows(rows, spec.key_column)
    if duplicates:
        log.info(f"{spec.label}: duplicates by {spec.key_column}: {duplicates}")

    destination = table_name(cfg.target_schema, spec.table)
    ensure_spec_columns(conn, cfg.target_schema, spec)
    temp_name = temp_table_name(spec.table)
    non_key_columns = tuple(column for column in spec.columns if column != spec.key_column)

    conn.execute(text(f"DROP TABLE IF EXISTS {temp_name}"))
    conn.execute(text(f"CREATE TEMP TABLE {temp_name} (LIKE {destination} INCLUDING DEFAULTS) ON COMMIT DROP"))
    insert_rows(conn, temp_name, spec.columns, rows, cfg.page_size)

    if non_key_columns:
        assignments = ", ".join(update_assignment_sql(column) for column in non_key_columns)
        changed = " OR ".join(changed_condition_sql(column) for column in non_key_columns)
        update_sql = text(f"""
            UPDATE {destination} AS t
            SET {assignments}
            FROM {temp_name} AS s
            WHERE t.{ident(spec.key_column)} = s.{ident(spec.key_column)}
              AND ({changed})
        """)
        updated = conn.execute(update_sql).rowcount or 0
    else:
        updated = 0

    insert_sql = text(f"""
        INSERT INTO {destination} ({column_list(spec.columns)})
        SELECT {column_list(spec.columns)}
        FROM {temp_name} AS s
        WHERE NOT EXISTS (
            SELECT 1
            FROM {destination} AS t
            WHERE t.{ident(spec.key_column)} = s.{ident(spec.key_column)}
        )
    """)
    inserted = conn.execute(insert_sql).rowcount or 0
    total = count_rows(conn, cfg.target_schema, spec.table)
    log.info(f"{spec.label}: inserted {inserted}, updated {updated}, total {total}")


def write_rows(target_engine, cfg: BitrixConfig, rows_by_table: Dict[str, List[Dict[str, Any]]]) -> None:
    log.info("Writing to PostgreSQL")
    with target_engine.begin() as conn:
        ensure_schema_and_tables(conn, cfg.target_schema)
        for spec in QUERY_SPECS:
            rows = rows_by_table[spec.table]
            if spec.mode == "replace":
                replace_table(conn, cfg, spec, rows)
            elif spec.mode == "upsert":
                upsert_table(conn, cfg, spec, rows)
            else:
                raise RuntimeError(f"{spec.label}: unknown mode {spec.mode}")


def missing_procedure_hint(exc: Exception, procedure: str) -> Optional[str]:
    message = str(exc)
    if "does not exist" not in message:
        return None
    return f"{procedure}: procedure not found in PostgreSQL, skipped. Create it or set its run flag to False."


def call_procedure(target_engine, procedure: str) -> None:
    _, text = import_sqlalchemy()
    log.info(f"Calling {procedure}")
    try:
        with target_engine.begin() as conn:
            conn.execute(text(f"CALL {procedure}()"))
    except Exception as exc:
        hint = missing_procedure_hint(exc, procedure)
        if hint:
            log.error(hint)
            return
        raise
    log.info(f"{procedure}: done")


def run_procedures(target_engine, cfg: BitrixConfig) -> None:
    if cfg.run_scud_staff_report_procedure:
        call_procedure(target_engine, SCUD_STAFF_REPORT_PROCEDURE)
    if cfg.run_reports_presense_procedure:
        call_procedure(target_engine, REPORTS_PRESENSE_PROCEDURE)


def main() -> None:
    try:
        log.info("=" * 50)
        log.info("BITRIX ETL START")
        log.info(f"DRY_RUN: {DRY_RUN}")
        log.info(f"MySQL: {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}")
        log.info(f"PostgreSQL: {POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
        log.info(f"Target schema: {TARGET_SCHEMA}")
        log.info("=" * 50)

        cfg = load_config()
        source_engine = create_source_engine(cfg)
        check_source_connection(source_engine)

        target_engine = None
        if not cfg.dry_run:
            target_engine = create_target_engine(cfg)
            check_target_connection(target_engine)

        rows_by_table: Dict[str, List[Dict[str, Any]]] = {}
        for spec in QUERY_SPECS:
            rows_by_table[spec.table] = fetch_rows(source_engine, spec)

        if cfg.dry_run:
            total = sum(len(rows) for rows in rows_by_table.values())
            log.info(f"DRY RUN - database write skipped. Total rows: {total}")
            return

        if target_engine is None:
            raise RuntimeError("PostgreSQL engine is not initialized")

        write_rows(target_engine, cfg, rows_by_table)
        run_procedures(target_engine, cfg)
        log.info("=" * 50)
        log.info("BITRIX ETL END")
        log.info("=" * 50)
    except Exception as exc:
        log.exception(f"BITRIX ETL FAILED: {exc}")
        raise


if __name__ == "__main__":
    main()
