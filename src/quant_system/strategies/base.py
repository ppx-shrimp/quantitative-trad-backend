from abc import ABC, abstractmethod


class Strategy(ABC):
    name: str

    @abstractmethod
    def should_open(self, symbol: str) -> tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    def should_close(self, symbol: str) -> tuple[bool, str]:
        raise NotImplementedError
