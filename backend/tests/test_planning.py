from datetime import date

from app.agents.planning.tools import (
    calculate_critical_path,
    detect_dependency_cycle,
    validate_dependencies,
)
from app.model.planning import (
    ProjectPlanningRequest,
    TaskInput,
    TaskPriority,
)
from app.services.planning_service import (
    PlanningService,
)


def test_dependency_validation():

    tasks = [

        TaskInput(
            task_id="T001",
            name="Requirements",
            duration_days=2,
        ),

        TaskInput(
            task_id="T002",
            name="Development",
            duration_days=5,
            dependencies=[
                "UNKNOWN"
            ],
        ),
    ]

    errors = validate_dependencies(
        tasks
    )

    assert len(errors) == 1


def test_cycle_detection():

    tasks = [

        TaskInput(
            task_id="T001",
            name="Task A",
            duration_days=2,
            dependencies=[
                "T002"
            ],
        ),

        TaskInput(
            task_id="T002",
            name="Task B",
            duration_days=2,
            dependencies=[
                "T001"
            ],
        ),
    ]

    assert (
        detect_dependency_cycle(
            tasks
        )
        is True
    )


def test_no_cycle():

    tasks = [

        TaskInput(
            task_id="T001",
            name="Task A",
            duration_days=2,
        ),

        TaskInput(
            task_id="T002",
            name="Task B",
            duration_days=2,
            dependencies=[
                "T001"
            ],
        ),
    ]

    assert (
        detect_dependency_cycle(
            tasks
        )
        is False
    )


def test_critical_path():

    tasks = [

        TaskInput(
            task_id="T001",
            name="Requirements",
            duration_days=3,
        ),

        TaskInput(
            task_id="T002",
            name="Development",
            duration_days=5,
            dependencies=[
                "T001"
            ],
        ),

        TaskInput(
            task_id="T003",
            name="Testing",
            duration_days=2,
            dependencies=[
                "T002"
            ],
        ),
    ]

    path, duration = (
        calculate_critical_path(
            tasks
        )
    )

    assert path == [
        "T001",
        "T002",
        "T003",
    ]

    assert duration == 10


def test_schedule_generation():

    service = PlanningService()

    request = ProjectPlanningRequest(

        project_id="P001",

        project_name="Test Project",

        objective=(
            "Build a simple "
            "web application"
        ),

        start_date=date(
            2026,
            10,
            1,
        ),

        deadline=date(
            2026,
            10,
            30,
        ),

        use_llm=False,

        tasks=[

            TaskInput(
                task_id="T001",
                name="Requirements",
                duration_days=2,
                priority=TaskPriority.HIGH,
            ),

            TaskInput(
                task_id="T002",
                name="Development",
                duration_days=5,
                priority=TaskPriority.CRITICAL,
                dependencies=[
                    "T001"
                ],
            ),
        ],
    )

    result = (
        service.create_schedule(
            request
        )
    )

    assert (
        result.project_id
        == "P001"
    )

    assert (
        len(result.schedule)
        == 2
    )

    assert (
        result.schedule[0]
        .planned_start
        == date(2026, 10, 1)
    )

    assert (
        result.schedule[1]
        .planned_start
        > result.schedule[0]
        .planned_end
    )


def test_deadline_detection():

    service = PlanningService()

    request = ProjectPlanningRequest(

        project_id="P002",

        project_name="Deadline Test",

        objective=(
            "Build a large "
            "software project"
        ),

        start_date=date(
            2026,
            10,
            1,
        ),

        deadline=date(
            2026,
            10,
            3,
        ),

        use_llm=False,

        tasks=[

            TaskInput(
                task_id="T001",
                name="Long Task",
                duration_days=10,
            ),
        ],
    )

    result = (
        service.create_schedule(
            request
        )
    )

    assert (
        result.analysis.deadline_met
        is False
    )

    assert (
        result.analysis.delay_days
        > 0
    )