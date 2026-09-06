"""Topology traversal with EXPLICIT direct vs transitive semantics.

* Direct downstream = services the affected service *directly* depends on.
* Direct upstream   = services that *directly* depend on the affected service.
* Dependency chain  = transitive (recursive) downstream closure.
* Dependent chain   = transitive (recursive) upstream closure.

Recursive descendants are never silently labelled "downstream" — they are only
ever exposed as the explicitly-named ``dependency_chain`` / ``dependent_chain``.
Backed by PostgreSQL — no graph database required for Phase 1.
"""

from __future__ import annotations

from collections import deque

from app.db.repositories import TopologyRepository
from app.domain.models import ServiceDependency, ServiceTopology


class TopologyService:
    def __init__(self, repo: TopologyRepository) -> None:
        self.repo = repo

    # -- direct edges (one hop) -------------------------------------------
    def direct_dependencies(self, service: str) -> list[ServiceDependency]:
        """Edges where ``service`` is the source (its direct downstream deps)."""
        return self.repo.edges_from(service)

    def direct_dependents(self, service: str) -> list[ServiceDependency]:
        """Edges where ``service`` is the target (its direct upstream callers)."""
        return self.repo.edges_into(service)

    def direct_downstream(self, service: str) -> list[str]:
        return sorted({e.target_service for e in self.repo.edges_from(service)})

    def direct_upstream(self, service: str) -> list[str]:
        return sorted({e.source_service for e in self.repo.edges_into(service)})

    # -- transitive closures (recursive) ----------------------------------
    def dependency_chain(self, service: str) -> list[str]:
        """All services ``service`` transitively depends on (recursive downstream)."""
        return self._traverse(service, forward=True)

    def dependent_chain(self, service: str) -> list[str]:
        """All services that transitively depend on ``service`` (recursive upstream)."""
        return self._traverse(service, forward=False)

    def _traverse(self, service: str, *, forward: bool) -> list[str]:
        adjacency: dict[str, set[str]] = {}
        for edge in self.repo.all_edges():
            if forward:
                adjacency.setdefault(edge.source_service, set()).add(edge.target_service)
            else:
                adjacency.setdefault(edge.target_service, set()).add(edge.source_service)

        seen: set[str] = set()
        queue: deque[str] = deque([service])
        while queue:
            node = queue.popleft()
            for neighbour in adjacency.get(node, ()):  # noqa: SIM113
                if neighbour not in seen and neighbour != service:
                    seen.add(neighbour)
                    queue.append(neighbour)
        return sorted(seen)

    # -- composed view -----------------------------------------------------
    def build_topology(self, service: str) -> ServiceTopology:
        """The dependency context attached to an ``IncidentContext``."""
        return ServiceTopology(
            service=service,
            direct_dependencies=self.direct_dependencies(service),
            direct_dependents=self.direct_dependents(service),
            direct_downstream=self.direct_downstream(service),
            direct_upstream=self.direct_upstream(service),
            dependency_chain=self.dependency_chain(service),
            dependent_chain=self.dependent_chain(service),
        )
