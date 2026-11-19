from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.safe.protection_result import ProtectionResult


class ProtectorBase(ABC):
    @abstractmethod
    def validate(self, query: str) -> "ProtectionResult":
        """Validate query"""
