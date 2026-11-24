"""Holds the provider adapters. One failing provider never hides the others."""
from typing import Optional

from .agents import (AgentWorkspacesProvider, AntigravityProvider, ClaudeCodeProvider,
                     CodexProvider, HermesProvider)
from .base import Provider
from .process import ProcScanner


class Registry:
    def __init__(self, providers=()):
        self._providers = {}
        self.errors = {}  # provider id -> last error message from discover()
        for p in providers:
            self.register(p)

    def register(self, provider: Provider) -> None:
        if not provider.id:
            raise ValueError("provider has no id")
        if provider.id in self._providers:
            raise ValueError(f"duplicate provider id {provider.id!r}")
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> Optional[Provider]:
        return self._providers.get(provider_id)

    def providers(self) -> list:
        return list(self._providers.values())

    def discover_all(self) -> list:
        sessions = []
        for pid_, provider in self._providers.items():
            try:
                sessions.extend(provider.discover())
                self.errors.pop(pid_, None)
            except Exception as e:  # a broken adapter must not break the others
                self.errors[pid_] = f"{type(e).__name__}: {e}"
        return sessions


def default_registry(scanner: Optional[ProcScanner] = None) -> Registry:
    """The five Phase 2 adapters. AgentDock/AIgency stays out of this list (optional extra)."""
    return Registry([
        ClaudeCodeProvider(scanner),
        CodexProvider(scanner),
        AntigravityProvider(scanner),
        HermesProvider(scanner),
        AgentWorkspacesProvider(scanner),
    ])
