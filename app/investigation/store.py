"""Persistence for investigations (reuses the Phase 1 database layer).

Stores the serialized ``InvestigationResult`` plus the running list of
operator-supplied facts, so an investigation can be fetched and resumed with
additional information.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import InvestigationORM
from app.investigation.models import InvestigationResult


class InvestigationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, result: InvestigationResult, human_facts: list[str]) -> None:
        now = datetime.utcnow()
        row = self.session.get(InvestigationORM, result.investigation_id)
        payload = result.model_dump(mode="json")
        if row is None:
            row = InvestigationORM(
                id=result.investigation_id,
                incident_id=result.incident_id,
                status=result.status.value,
                result=payload,
                human_facts=list(human_facts),
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row.status = result.status.value
            row.result = payload
            row.human_facts = list(human_facts)
            row.updated_at = now
        self.session.commit()

    def get(self, investigation_id: str) -> InvestigationORM | None:
        return self.session.get(InvestigationORM, investigation_id)

    def get_result(self, investigation_id: str) -> InvestigationResult | None:
        row = self.get(investigation_id)
        if row is None:
            return None
        return InvestigationResult.model_validate(row.result)
