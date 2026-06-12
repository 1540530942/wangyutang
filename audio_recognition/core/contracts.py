from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RouteKind = Literal["action", "face", "none"]


class PlannedTask(BaseModel):
    skill_id: str = Field("", max_length=80)
    route: RouteKind = "none"
    planner: str = Field("rule", max_length=40)
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    transcript: str = Field("", max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.skill_id) and self.route != "none"
