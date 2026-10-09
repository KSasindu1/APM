from datetime import date

from app.agents.planning.tools import (
    calculate_critical_path,
    detect_dependency_cycle,
    schedule_tasks,
)
from app.model.planning import TaskInput


def test_schedule_tasks():

    tasks = [
        TaskInput(
            task_id="T001",
            name="Requirements",
            duration_days=2,
            priority="HIGH",
            dependencies=[],
        ),
        TaskInput(
            task_id="T002",
            name="Implementation",
            duration_days=3,
            priority="HIGH",
            dependencies=["T001"],
        ),
    ]

    result = schedule_tasks(
        tasks=tasks,
        start_date=date(2026, 1, 1),
        risks=[],
    )

    assert len(result) == 2

    assert result[0]["start_date"] == date(
        2026,
        1,
        1,
    )

    assert result[0]["end_date"] == date(
        2026,
        1,
        2,
    )

    assert result[1]["start_date"] == date(
        2026,
        1,
        3,
    )


def test_dependency_cycle():

    tasks = [
        TaskInput(
            task_id="T001",
            name="Task 1",
            duration_days=1,
            dependencies=["T002"],
        ),
        TaskInput(
            task_id="T002",
            name="Task 2",
            duration_days=1,
            dependencies=["T001"],
        ),
    ]

    assert detect_dependency_cycle(tasks) is True


def test_critical_path():

    tasks = [
        TaskInput(
            task_id="T001",
            name="Requirements",
            duration_days=2,
            dependencies=[],
        ),
        TaskInput(
            task_id="T002",
            name="Design",
            duration_days=3,
            dependencies=["T001"],
        ),
        TaskInput(
            task_id="T003",
            name="Testing",
            duration_days=2,
            dependencies=["T002"],
        ),
    ]

    scheduled = schedule_tasks(
        tasks,
        date(2026, 1, 1),
        [],
    )

    path, duration = calculate_critical_path(
        scheduled
    )

    assert path == [
        "T001",
        "T002",
        "T003",
    ]

    assert duration == 7