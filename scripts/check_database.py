"""Verify imported Olist data, required types, joins, and input order coverage."""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import text

from scripts.import_olist_csv import TABLES
from scripts.validate_inputs import load_and_validate_inputs
from src.config.settings import get_settings
from src.database.connection import create_engine


async def check_database(input_dir: Path) -> None:
    settings = get_settings()
    engine = create_engine(settings, read_only=True)
    cases = load_and_validate_inputs(input_dir)
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        for table in TABLES:
            count = await connection.scalar(text(f"SELECT COUNT(*) FROM olist.{table.table}"))
            if not count:
                raise RuntimeError(f"Table is empty: olist.{table.table}")
        monetary = await connection.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema='olist' AND (table_name, column_name) IN "
                "(('order_items','price'),('order_items','freight_value'),"
                "('order_payments','payment_value'))"
            )
        )
        bad_types = [tuple(row) for row in monetary if row.data_type != "numeric"]
        if bad_types:
            raise RuntimeError(f"Monetary columns are not NUMERIC: {bad_types}")
        order_ids = [case.customer_request.claimed_order_id for case in cases]
        found = (
            (
                await connection.execute(
                    text("SELECT order_id FROM olist.orders WHERE order_id = ANY(:order_ids)"),
                    {"order_ids": order_ids},
                )
            )
            .scalars()
            .all()
        )
        missing = sorted(set(order_ids) - set(found))
        if missing:
            raise RuntimeError(f"Input order IDs missing from database: {missing}")
        orphan_count = await connection.scalar(
            text(
                "SELECT COUNT(*) FROM olist.order_items i "
                "LEFT JOIN olist.orders o ON o.order_id=i.order_id WHERE o.order_id IS NULL"
            )
        )
        if orphan_count:
            raise RuntimeError(f"Found {orphan_count} item rows without orders")
    await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    args = parser.parse_args()
    asyncio.run(check_database(args.input_dir))
    print("PASS: database is ready and all 50 input orders are queryable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
