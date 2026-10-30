"""Site content snapshot and the pure function that applies a validated operation to it."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from muda.domain.design import DesignConfig
from muda.domain.operations import UpdateDesign, UpdateMenuPrice


class UnknownProductError(Exception):
    pass


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Hero(_Frozen):
    title: str
    headline: str
    description: str
    cta_label: str


class MenuItem(_Frozen):
    id: str
    name: str
    price: Decimal


class SiteContent(_Frozen):
    hero: Hero
    menu: tuple[MenuItem, ...]
    currency: Literal["TRY"] = "TRY"
    design: DesignConfig = DesignConfig()


def initial_content() -> SiteContent:
    return SiteContent(
        hero=Hero(
            title="Luna Cafe",
            headline="Every Cup Tells a Story",
            description="Small-batch coffee, warm corners and slow mornings in the heart of town.",
            cta_label="See the menu",
        ),
        menu=(
            MenuItem(id="espresso", name="Espresso", price=Decimal("90")),
            MenuItem(id="americano", name="Americano", price=Decimal("110")),
            MenuItem(id="latte", name="Latte", price=Decimal("130")),
        ),
    )


def apply_operation(content: SiteContent, op: UpdateMenuPrice | UpdateDesign) -> SiteContent:
    if isinstance(op, UpdateDesign):
        return content.model_copy(update={"design": op.changes.apply(content.design)})
    if not any(item.id == op.product_id for item in content.menu):
        raise UnknownProductError(op.product_id)
    menu = tuple(
        item.model_copy(update={"price": op.new_price}) if item.id == op.product_id else item
        for item in content.menu
    )
    return content.model_copy(update={"menu": menu})


class Change(_Frozen):
    field: str
    old: str
    new: str


def diff_content(old: SiteContent, new: SiteContent) -> list[Change]:
    """Human-readable differences between two snapshots (menu prices and design)."""
    changes: list[Change] = []
    old_prices = {i.id: i.price for i in old.menu}
    for item in new.menu:
        before = old_prices.get(item.id)
        if before != item.price:
            changes.append(
                Change(
                    field=f"menu.{item.id}.price",
                    old="" if before is None else str(before),
                    new=str(item.price),
                )
            )
    old_design, new_design = old.design.model_dump(), new.design.model_dump()
    for key, value in new_design.items():
        if old_design[key] != value:
            changes.append(Change(field=f"design.{key}", old=str(old_design[key]), new=str(value)))
    return changes
