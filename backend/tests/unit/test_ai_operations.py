import pytest
from pydantic import ValidationError

from muda.domain.operations import UpdateDesign, UpdateMenuPrice, parse_operation


def test_parse_price_update() -> None:
    op = parse_operation(
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "150"}
    )
    assert isinstance(op, UpdateMenuPrice)
    assert str(op.new_price) == "150"


def test_parse_design_update() -> None:
    op = parse_operation(
        {"operation": "update_design", "changes": {"theme": "dark", "card_radius": "large"}}
    )
    assert isinstance(op, UpdateDesign)
    assert op.changes.theme == "dark"


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "run_shell", "command": "rm -rf /"},
        {"operation": "delete_database"},
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "-5"},
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "0"},
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "100000"},
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "NaN"},
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "1.234"},
        {"operation": "update_menu_price", "product_id": "../etc", "new_price": "10"},
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "10", "x": 1},
        {"operation": "update_design", "changes": {"html": "<script>1</script>"}},
        {},
    ],
)
def test_invalid_operations_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_operation(payload)
