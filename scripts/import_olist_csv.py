"""Validate and atomically import all Olist CSV files into PostgreSQL."""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from src.config.settings import get_settings


@dataclass(frozen=True)
class CsvTable:
    table: str
    filename: str
    columns: tuple[str, ...]
    expected_rows: int


TABLES = (
    CsvTable(
        "customers",
        "olist_customers_dataset.csv",
        (
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ),
        99441,
    ),
    CsvTable(
        "products",
        "olist_products_dataset.csv",
        (
            "product_id",
            "product_category_name",
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ),
        32951,
    ),
    CsvTable(
        "sellers",
        "olist_sellers_dataset.csv",
        ("seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"),
        3095,
    ),
    CsvTable(
        "orders",
        "olist_orders_dataset.csv",
        (
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
        99441,
    ),
    CsvTable(
        "order_items",
        "olist_order_items_dataset.csv",
        (
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ),
        112650,
    ),
    CsvTable(
        "order_payments",
        "olist_order_payments_dataset.csv",
        ("order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"),
        103886,
    ),
    CsvTable(
        "order_reviews",
        "olist_order_reviews_dataset.csv",
        (
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ),
        99224,
    ),
    CsvTable(
        "category_translation",
        "product_category_name_translation.csv",
        ("product_category_name", "product_category_name_english"),
        71,
    ),
    CsvTable(
        "geolocation",
        "olist_geolocation_dataset.csv",
        (
            "geolocation_zip_code_prefix",
            "geolocation_lat",
            "geolocation_lng",
            "geolocation_city",
            "geolocation_state",
        ),
        1000163,
    ),
)


def validate_csv(path: Path, table: CsvTable) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing CSV for {table.table}: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader, []))
    if header != table.columns:
        raise ValueError(
            f"Invalid columns in {path.name}: expected {table.columns}, received {header}"
        )


def ensure_reader_role(cursor: psycopg.Cursor, role: str, password: str) -> None:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    if cursor.fetchone() is None:
        cursor.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    else:
        cursor.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )


def import_data(data_dir: Path) -> dict[str, int]:
    settings = get_settings()
    paths = {table.table: data_dir / table.filename for table in TABLES}
    for table in TABLES:
        validate_csv(paths[table.table], table)

    connection_string = (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"dbname={settings.postgres_db} user={settings.postgres_user} "
        f"password={settings.postgres_password}"
    )
    schema_path = Path(__file__).resolve().parents[1] / "src" / "database" / "schema.sql"
    counts: dict[str, int] = {}
    with psycopg.connect(connection_string) as connection:  # noqa: SIM117
        with connection.cursor() as cursor:
            ensure_reader_role(cursor, settings.postgres_read_user, settings.postgres_read_password)
            cursor.execute(schema_path.read_text(encoding="utf-8"))
            table_names = sql.SQL(", ").join(
                sql.Identifier("olist", table.table) for table in reversed(TABLES)
            )
            cursor.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(table_names))

            for table in TABLES:
                copy_statement = sql.SQL(
                    "COPY {} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')"
                ).format(
                    sql.Identifier("olist", table.table),
                    sql.SQL(", ").join(map(sql.Identifier, table.columns)),
                )
                with cursor.copy(copy_statement) as copy:  # noqa: SIM117
                    with paths[table.table].open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            copy.write(chunk)
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier("olist", table.table))
                )
                count = int(cursor.fetchone()[0])
                if count != table.expected_rows:
                    raise ValueError(
                        f"Row count mismatch for {table.table}: "
                        f"expected {table.expected_rows}, received {count}"
                    )
                counts[table.table] = count

            orphan_checks = {
                "orders_without_customer": "SELECT COUNT(*) FROM olist.orders o LEFT JOIN olist.customers c ON c.customer_id=o.customer_id WHERE c.customer_id IS NULL",
                "items_without_order": "SELECT COUNT(*) FROM olist.order_items i LEFT JOIN olist.orders o ON o.order_id=i.order_id WHERE o.order_id IS NULL",
                "payments_without_order": "SELECT COUNT(*) FROM olist.order_payments p LEFT JOIN olist.orders o ON o.order_id=p.order_id WHERE o.order_id IS NULL",
            }
            for name, statement in orphan_checks.items():
                cursor.execute(statement)
                if int(cursor.fetchone()[0]) != 0:
                    raise ValueError(f"Join integrity check failed: {name}")

            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA olist TO {}").format(
                    sql.Identifier(settings.postgres_read_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA olist TO {}").format(
                    sql.Identifier(settings.postgres_read_user)
                )
            )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    for table, count in import_data(args.data_dir).items():
        print(f"{table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
