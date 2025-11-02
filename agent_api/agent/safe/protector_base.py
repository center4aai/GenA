from abc import ABC, abstractmethod

from agent.safe.protection_result import ProtectionResult


class ProtectorBase(ABC):
    @abstractmethod
    def validate(self, query: str) -> ProtectionResult:
        """Validate query"""
