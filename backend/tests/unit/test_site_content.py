from decimal import Decimal

import pytest

from muda.domain.operations import UpdateDesign, UpdateMenuPrice, parse_operation
from muda.domain.site import SiteContent, UnknownProductError, apply_operation, initial_content


def test_initial_content_matches_spec() -> None:
    c = initial_content()
    prices = {i.id: i.price for i in c.menu}
    assert prices == {
        "espresso": Decimal("90"),
        "americano": Decimal("110"),
        "latte": Decimal("130"),
    }
    assert c.hero.headline == "Every Cup Tells a Story"
    assert c.currency == "TRY"


def test_price_update_changes_only_target_product() -> None:
    base = initial_content()
    op = parse_operation(
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "150"}
    )
    assert isinstance(op, UpdateMenuPrice)
    new = apply_operation(base, op)
    assert {i.id: i.price for i in new.menu}["latte"] == Decimal("150")
    assert {i.id: i.price for i in new.menu}["espresso"] == Decimal("90")
    assert new.design == base.design
    assert {i.id: i.price for i in base.menu}["latte"] == Decimal("130")


def test_unknown_product_is_rejected() -> None:
    op = UpdateMenuPrice(operation="update_menu_price", product_id="pizza", new_price=Decimal("5"))
    with pytest.raises(UnknownProductError):
        apply_operation(initial_content(), op)


def test_design_update_changes_only_design() -> None:
    base = initial_content()
    op = parse_operation({"operation": "update_design", "changes": {"theme": "dark"}})
    assert isinstance(op, UpdateDesign)
    new = apply_operation(base, op)
    assert new.design.theme == "dark"
    assert new.menu == base.menu


def test_snapshot_roundtrip_preserves_decimal() -> None:
    c = initial_content()
    again = SiteContent.model_validate(c.model_dump(mode="json"))
    assert again == c
