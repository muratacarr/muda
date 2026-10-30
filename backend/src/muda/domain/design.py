"""Closed-vocabulary design configuration. The AI can only pick from these values."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

Theme = Literal["light", "dark"]
Palette = Literal["coffee", "neutral", "forest"]
HeroAlignment = Literal["left", "center"]
HeadingSize = Literal["normal", "large"]
CardRadius = Literal["small", "medium", "large"]
MenuColumns = Literal[1, 2, 3]


class DesignConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme: Theme = "light"
    palette: Palette = "coffee"
    hero_alignment: HeroAlignment = "left"
    heading_size: HeadingSize = "normal"
    card_radius: CardRadius = "medium"
    menu_columns: MenuColumns = 3


class DesignPatch(BaseModel):
    """A partial update proposed by the AI. Unknown keys and values are rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme: Theme | None = None
    palette: Palette | None = None
    hero_alignment: HeroAlignment | None = None
    heading_size: HeadingSize | None = None
    card_radius: CardRadius | None = None
    menu_columns: MenuColumns | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_dump(exclude_none=True):
            raise ValueError("design patch must change at least one property")
        return self

    def apply(self, base: DesignConfig) -> DesignConfig:
        return base.model_copy(update=self.model_dump(exclude_none=True))
