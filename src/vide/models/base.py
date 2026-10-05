"""Shared column mixins and the enum-column helper."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column


def enum_column(enum_cls: type[StrEnum], **kwargs: Any) -> Any:
    """A VARCHAR + CHECK column backed by a Python StrEnum.

    ``native_enum=False`` keeps migrations cheap: adding a value is a CHECK
    constraint change rather than an ALTER TYPE.
    """
    return mapped_column(
        SAEnum(
            enum_cls,
            native_enum=False,
            length=48,
            values_callable=lambda e: [m.value for m in e],
        ),
        **kwargs,
    )


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
