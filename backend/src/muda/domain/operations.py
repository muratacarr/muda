"""Allowlisted operations the AI may propose (discriminated union)."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from muda.domain.design import DesignPatch

MAX_PRICE = Decimal("10000")


class UpdateMenuPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["update_menu_price"]
    product_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    new_price: Decimal

    @field_validator("new_price")
    @classmethod
    def _valid_price(cls, v: Decimal) -> Decimal:
        if not v.is_finite() or v <= 0 or v > MAX_PRICE:
            raise ValueError("price must be a finite number in (0, 10000]")
        if v.as_tuple().exponent < -2:  # type: ignore[operator]
            raise ValueError("price allows at most 2 decimal places")
        return v


class UpdateDesign(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["update_design"]
    changes: DesignPatch


Operation = Annotated[UpdateMenuPrice | UpdateDesign, Field(discriminator="operation")]
_adapter: TypeAdapter[UpdateMenuPrice | UpdateDesign] = TypeAdapter(Operation)


def parse_operation(payload: object) -> UpdateMenuPrice | UpdateDesign:
    return _adapter.validate_python(payload)
