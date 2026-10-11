from datetime import date

from app.model.planning import (
    ProjectPlanningRequest,
)
from app.services.planning_service import (
    PlanningService,
)


def test_service_fallback_schedule():

    request = ProjectPlanningRequest(
        project_id="TEST-001",
        project_name="Test Project",
        objective=(
            "Build a simple project management system"
        ),
        start_date=date(2026, 1, 1),
        deadline=date(2026, 2, 28),
        use_llm=False,
    )

    service = PlanningService()

    result = service.create_schedule(
        request
    )

    assert result.project_id == "TEST-001"

    assert len(result.schedule) > 0

    assert result.responsible_ai.ai_generated is False

    assert (
        result.responsible_ai.human_review_required
        is True
    )

    assert len(
        result.analysis.critical_path
    ) > 0