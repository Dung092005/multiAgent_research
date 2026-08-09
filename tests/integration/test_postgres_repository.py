import os
from pathlib import Path

import pytest
from sqlalchemy import text

from scripts.import_olist_csv import TABLES
from scripts.validate_inputs import load_and_validate_inputs
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 with PostgreSQL available",
)


@pytest.mark.asyncio
async def test_full_import_row_counts_and_input_coverage():
    engine = create_engine(get_settings(), read_only=True)
    cases = load_and_validate_inputs(Path("input"))
    async with engine.connect() as connection:
        for table in TABLES:
            count = await connection.scalar(text(f"SELECT COUNT(*) FROM olist.{table.table}"))
            assert count == table.expected_rows
        ids = [case.customer_request.claimed_order_id for case in cases]
        found = (
            (
                await connection.execute(
                    text("SELECT order_id FROM olist.orders WHERE order_id = ANY(:ids)"),
                    {"ids": ids},
                )
            )
            .scalars()
            .all()
        )
        assert set(found) == set(ids)
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_does_not_cartesian_multiply_two_by_two_order():
    engine = create_engine(get_settings(), read_only=True)
    repository = OlistRepository(create_session_factory(engine))
    candidate = None
    for case in load_and_validate_inputs(Path("input")):
        context = await repository.get_policy_context(case.customer_request.claimed_order_id)
        if len(context.items) == 2 and len(context.payments) == 2:
            candidate = context
            break
    assert candidate is not None, "generated inputs should include a 2-item/2-payment order"
    totals = await repository.calculate_order_financials(candidate.order.order_id)
    assert totals.item_total_brl == sum(item.price for item in candidate.items)
    assert totals.freight_total_brl == sum(item.freight_value for item in candidate.items)
    assert totals.payment_total_brl == sum(payment.payment_value for payment in candidate.payments)
    assert totals.payment_row_count == 2
    await engine.dispose()
