"""AML source registry facade."""

from __future__ import annotations

from typing import List

from venombot.catalog import all_sources
from venombot.models import AMLSource
from venombot.samples import SAMPLE_CONTENT

__all__ = ["AMLSourcesRegistry", "SAMPLE_CONTENT"]


class AMLSourcesRegistry:
    @staticmethod
    def get_sources() -> List[AMLSource]:
        return all_sources()
