from app.agents.planning.agent import PlanningAgent


def test_fallback_tasks():

    tasks = PlanningAgent.fallback_tasks(
        "Build a project management system"
    )

    assert len(tasks) == 6

    assert tasks[0].task_id == "T001"

    assert tasks[1].dependencies == ["T001"]

    assert tasks[-1].dependencies == ["T005"]


def test_parse_llm_json():

    content = """
    {
        "tasks": [
            {
                "task_id": "T001",
                "name": "Requirements",
                "description": "Analyze requirements.",
                "duration_days": 2,
                "priority": "HIGH",
                "dependencies": []
            }
        ]
    }
    """

    parsed = PlanningAgent.parse_llm_json(
        content
    )

    assert len(parsed) == 1

    tasks = PlanningAgent.convert_to_tasks(
        parsed
    )

    assert tasks[0].name == "Requirements"
    assert tasks[0].task_id == "T001"