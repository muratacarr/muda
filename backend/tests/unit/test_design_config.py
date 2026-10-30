import pytest
from pydantic import ValidationError

from muda.domain.design import DesignConfig, DesignPatch


def test_default_design_is_valid() -> None:
    d = DesignConfig()
    assert d.theme == "light"
    assert d.menu_columns == 3


def test_patch_applies_only_given_fields() -> None:
    base = DesignConfig()
    new = DesignPatch(theme="dark", hero_alignment="center").apply(base)
    assert new.theme == "dark"
    assert new.hero_alignment == "center"
    assert new.palette == base.palette
    assert base.theme == "light"  # original is immutable


def test_config_is_frozen() -> None:
    with pytest.raises(ValidationError):
        DesignConfig().theme = "dark"  # type: ignore[misc]


@pytest.mark.parametrize(
    "bad",
    [
        {"theme": "neon"},
        {"palette": "#ff0000"},
        {"card_radius": "huge"},
        {"menu_columns": 4},
        {"css": "body{display:none}"},
        {"theme": "dark", "script": "<script>alert(1)</script>"},
    ],
)
def test_patch_rejects_unknown_keys_and_values(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DesignPatch(**bad)


def test_empty_patch_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DesignPatch()
