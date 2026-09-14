from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum as SAEnum, MetaData, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Migrations create JSON columns as JSONB; SQLite unit tests fall back to JSON.
JsonDocument = JSON().with_variant(JSONB(), "postgresql")


def enum_value_type(enum_cls: type[Enum], *, name: str) -> SAEnum:
    # Persist enum values instead of member names so ORM writes match SQL check constraints.
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [str(member.value) for member in cls],
        validate_strings=True,
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


def enum_check_constraint_name(table_name: str, column_name: str) -> str:
    # PostgreSQL's default name for an inline column CHECK, which the
    # migrations rely on.
    return f"{table_name}_{column_name}_check"


def install_enum_check_constraints(metadata: MetaData) -> None:
    """Add a CHECK listing the enum's values to every enum-valued column.

    The allowed values come from the Python enum, so ``create_all`` schemas
    enforce them and the PostgreSQL drift test can compare migrations with
    them. Migrations spell the values out literally.
    """
    for table in metadata.tables.values():
        existing = {constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)}
        for column in table.columns:
            column_type = column.type
            if not isinstance(column_type, SAEnum) or column_type.native_enum:
                continue
            name = enum_check_constraint_name(table.name, column.name)
            if name in existing:
                continue
            allowed = ", ".join("'" + value.replace("'", "''") + "'" for value in column_type.enums)
            table.append_constraint(CheckConstraint(f"{column.name} IN ({allowed})", name=name))
