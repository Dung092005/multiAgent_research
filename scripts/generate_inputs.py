"""Generate 50 deterministic demo cases from real Olist orders."""

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from src.errors import PolicyNoMatchError
from src.finance.calculator import FinancialCalculator
from src.policy.engine import PolicyEngine
from src.schemas.case_input import CaseInput, CustomerRequest
from src.schemas.records import ItemRecord, OrderRecord, PaymentRecord, PolicyContext

DISTRIBUTION = (
    ("canceled_order_paid", 9),
    ("unavailable_order_paid", 9),
    ("late_delivery_seller", 8),
    ("late_delivery_logistics", 8),
    ("valid_split_payment", 8),
    ("unsupported_late_claim", 8),
)

MESSAGES = {
    "canceled_order_paid": (
        "Đơn hàng {order_id} đã bị hủy nhưng tôi đã thanh toán. Hãy kiểm tra khoản hoàn tiền phù hợp.",
        "Tôi cần hỗ trợ về khoản thanh toán của đơn {order_id} sau khi đơn bị hủy.",
    ),
    "unavailable_order_paid": (
        "Đơn {order_id} báo không khả dụng dù tôi đã thanh toán. Vui lòng kiểm tra quyền lợi của tôi.",
        "Hãy kiểm tra khoản tiền đã trả cho đơn không khả dụng {order_id}.",
    ),
    "late_delivery_seller": (
        "Đơn {order_id} được giao trễ. Hãy kiểm tra nguyên nhân và bên chịu trách nhiệm.",
        "Tôi nhận đơn {order_id} sau ngày dự kiến và muốn được xem xét quyền lợi.",
    ),
    "late_delivery_logistics": (
        "Đơn {order_id} đến muộn hơn cam kết. Vui lòng điều tra quá trình giao hàng.",
        "Hãy xác minh nguyên nhân giao trễ của đơn {order_id} và khoản hỗ trợ phù hợp.",
    ),
    "valid_split_payment": (
        "Đơn {order_id} hiển thị nhiều khoản thanh toán. Hãy kiểm tra giúp tôi có bị tính trùng không.",
        "Tôi thấy thanh toán của đơn {order_id} được tách thành nhiều dòng và cần được giải thích.",
    ),
    "unsupported_late_claim": (
        "Tôi cho rằng đơn {order_id} giao trễ. Hãy đối chiếu mốc giao hàng thực tế.",
        "Vui lòng kiểm tra khiếu nại giao trễ đối với đơn {order_id}.",
    ),
}


def parse_datetime(value: str) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def load_contexts(data_dir: Path) -> list[PolicyContext]:
    orders: dict[str, OrderRecord] = {}
    with (data_dir / "olist_orders_dataset.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            orders[row["order_id"]] = OrderRecord(
                order_id=row["order_id"],
                customer_id=row["customer_id"],
                order_status=row["order_status"],
                order_purchase_timestamp=parse_datetime(row["order_purchase_timestamp"]),
                order_approved_at=parse_datetime(row["order_approved_at"]),
                order_delivered_carrier_date=parse_datetime(row["order_delivered_carrier_date"]),
                order_delivered_customer_date=parse_datetime(row["order_delivered_customer_date"]),
                order_estimated_delivery_date=parse_datetime(row["order_estimated_delivery_date"]),
            )

    items: dict[str, list[ItemRecord]] = defaultdict(list)
    with (data_dir / "olist_order_items_dataset.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            items[row["order_id"]].append(
                ItemRecord(
                    order_id=row["order_id"],
                    order_item_id=int(row["order_item_id"]),
                    product_id=row["product_id"],
                    seller_id=row["seller_id"],
                    shipping_limit_date=parse_datetime(row["shipping_limit_date"]),
                    price=Decimal(row["price"]),
                    freight_value=Decimal(row["freight_value"]),
                )
            )

    payments: dict[str, list[PaymentRecord]] = defaultdict(list)
    with (data_dir / "olist_order_payments_dataset.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            payments[row["order_id"]].append(
                PaymentRecord(
                    order_id=row["order_id"],
                    payment_sequential=int(row["payment_sequential"]),
                    payment_type=row["payment_type"],
                    payment_installments=int(row["payment_installments"]),
                    payment_value=Decimal(row["payment_value"]),
                )
            )

    contexts = []
    for order_id in sorted(orders):
        order_items = sorted(items[order_id], key=lambda row: row.order_item_id)
        order_payments = sorted(payments[order_id], key=lambda row: row.payment_sequential)
        if len(order_items) > 5 or len(order_payments) > 5:
            continue
        if len({item.seller_id for item in order_items}) > 5:
            continue
        contexts.append(
            PolicyContext(
                order=orders[order_id],
                items=order_items,
                payments=order_payments,
                financials=FinancialCalculator.calculate(order_items, order_payments),
            )
        )
    return contexts


def feature_selectors(rule: str) -> list[Callable[[PolicyContext], bool]]:
    selectors: dict[str, list[Callable[[PolicyContext], bool]]] = {
        "canceled_order_paid": [
            lambda c: not c.items,
            lambda c: len(c.payments) >= 2,
            lambda c: len(c.items) >= 2,
        ],
        "unavailable_order_paid": [
            lambda c: bool(c.items),
            lambda c: len(c.payments) >= 2,
            lambda c: not c.items,
        ],
        "late_delivery_seller": [
            lambda c: len({i.seller_id for i in c.items}) >= 2,
            lambda c: len(c.items) == 2 and len(c.payments) == 2,
            lambda c: len(c.items) >= 2,
        ],
        "late_delivery_logistics": [
            lambda c: len({i.seller_id for i in c.items}) >= 2,
            lambda c: len(c.items) == 2 and len(c.payments) == 2,
            lambda c: len(c.items) >= 2,
        ],
        "valid_split_payment": [
            lambda c: len({i.seller_id for i in c.items}) >= 2,
            lambda c: len(c.items) == 2 and len(c.payments) == 2,
            lambda c: len(c.payments) >= 3,
        ],
        "unsupported_late_claim": [
            lambda c: len({i.seller_id for i in c.items}) >= 2,
            lambda c: len(c.items) >= 2,
        ],
    }
    return selectors[rule]


def select_contexts(contexts: list[PolicyContext]) -> list[tuple[str, PolicyContext]]:
    engine = PolicyEngine()
    pools: dict[str, list[PolicyContext]] = defaultdict(list)
    for context in contexts:
        try:
            rule = engine.classify(context)
        except PolicyNoMatchError:
            continue
        if rule.startswith("late_delivery") and (
            not context.financials.payment_matches_order_total
            or context.financials.freight_total_brl <= 0
        ):
            continue
        pools[rule].append(context)

    selected: list[tuple[str, PolicyContext]] = []
    used: set[str] = set()
    for rule, target in DISTRIBUTION:
        chosen: list[PolicyContext] = []
        for selector in feature_selectors(rule):
            candidate = next(
                (
                    context
                    for context in pools[rule]
                    if context.order.order_id not in used and selector(context)
                ),
                None,
            )
            if candidate:
                chosen.append(candidate)
                used.add(candidate.order.order_id)
        for context in pools[rule]:
            if len(chosen) == target:
                break
            if context.order.order_id not in used:
                chosen.append(context)
                used.add(context.order.order_id)
        if len(chosen) != target:
            raise RuntimeError(f"Not enough qualified orders for {rule}: {len(chosen)}/{target}")
        selected.extend((rule, context) for context in chosen)
    return selected


def opened_at(context: PolicyContext) -> datetime:
    order = context.order
    timestamps = [
        order.order_purchase_timestamp,
        order.order_approved_at,
        order.order_delivered_carrier_date,
        order.order_delivered_customer_date,
        order.order_estimated_delivery_date,
    ]
    latest = max(value for value in timestamps if value is not None) + timedelta(days=1)
    return latest.replace(tzinfo=timezone(timedelta(hours=-3)))


def generate(data_dir: Path, input_dir: Path, overwrite: bool) -> list[Path]:
    existing = sorted(input_dir.glob("EC_*.json")) if input_dir.exists() else []
    if existing and not overwrite:
        raise FileExistsError("Input files already exist; use --overwrite to replace EC_*.json")
    input_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for path in existing:
            path.unlink()

    selected = select_contexts(load_contexts(data_dir))
    written: list[Path] = []
    for number, (rule, context) in enumerate(selected, start=1):
        case_id = f"EC_{number:03d}"
        templates = MESSAGES[rule]
        case = CaseInput(
            case_id=case_id,
            opened_at=opened_at(context),
            customer_request=CustomerRequest(
                language="vi",
                message=templates[(number - 1) % len(templates)].format(
                    order_id=context.order.order_id
                ),
                claimed_order_id=context.order.order_id,
            ),
            policy_version="EC_POLICY_V1",
        )
        path = input_dir / f"{case_id}.json"
        path.write_text(
            json.dumps(case.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    files = generate(args.data_dir, args.input_dir, args.overwrite)
    print(f"Generated {len(files)} grounded demo cases in {args.input_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
