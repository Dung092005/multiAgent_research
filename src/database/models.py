"""SQLAlchemy mappings for the nine Olist CSV datasets."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Identity, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = {"schema": "olist"}
    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_unique_id: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_zip_code_prefix: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_city: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_state: Mapped[str] = mapped_column(String(2), nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = {"schema": "olist"}
    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_status: Mapped[str] = mapped_column(String(32), nullable=False)
    order_purchase_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    order_approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    order_delivered_carrier_date: Mapped[datetime | None] = mapped_column(DateTime)
    order_delivered_customer_date: Mapped[datetime | None] = mapped_column(DateTime)
    order_estimated_delivery_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = {"schema": "olist"}
    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seller_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    shipping_limit_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    freight_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class OrderPayment(Base):
    __tablename__ = "order_payments"
    __table_args__ = {"schema": "olist"}
    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payment_sequential: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_installments: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class OrderReview(Base):
    __tablename__ = "order_reviews"
    __table_args__ = {"schema": "olist"}
    review_row_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    review_score: Mapped[int] = mapped_column(Integer, nullable=False)
    review_comment_title: Mapped[str | None] = mapped_column(Text)
    review_comment_message: Mapped[str | None] = mapped_column(Text)
    review_creation_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    review_answer_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = {"schema": "olist"}
    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_category_name: Mapped[str | None] = mapped_column(String(128))
    product_name_lenght: Mapped[int | None] = mapped_column(Integer)
    product_description_lenght: Mapped[int | None] = mapped_column(Integer)
    product_photos_qty: Mapped[int | None] = mapped_column(Integer)
    product_weight_g: Mapped[int | None] = mapped_column(Integer)
    product_length_cm: Mapped[int | None] = mapped_column(Integer)
    product_height_cm: Mapped[int | None] = mapped_column(Integer)
    product_width_cm: Mapped[int | None] = mapped_column(Integer)


class Seller(Base):
    __tablename__ = "sellers"
    __table_args__ = {"schema": "olist"}
    seller_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seller_zip_code_prefix: Mapped[int] = mapped_column(Integer, nullable=False)
    seller_city: Mapped[str] = mapped_column(String(128), nullable=False)
    seller_state: Mapped[str] = mapped_column(String(2), nullable=False)


class CategoryTranslation(Base):
    __tablename__ = "category_translation"
    __table_args__ = {"schema": "olist"}
    product_category_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    product_category_name_english: Mapped[str] = mapped_column(String(128), nullable=False)


class Geolocation(Base):
    __tablename__ = "geolocation"
    __table_args__ = {"schema": "olist"}
    geolocation_row_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    geolocation_zip_code_prefix: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    geolocation_lat: Mapped[Decimal] = mapped_column(Numeric(12, 9), nullable=False)
    geolocation_lng: Mapped[Decimal] = mapped_column(Numeric(12, 9), nullable=False)
    geolocation_city: Mapped[str] = mapped_column(String(128), nullable=False)
    geolocation_state: Mapped[str] = mapped_column(String(2), nullable=False)
