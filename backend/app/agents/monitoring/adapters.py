"""Translate the sample team payloads to monitoring's strict version-1 contract.

Dates and project IDs not present in a team payload are supplied explicitly by the caller.
"""

from datetime import date

from .schemas import ActualProjectSnapshot, PlanningSnapshot, RiskSnapshot


def adapt_planning(payload: dict, updated_on: date) -> PlanningSnapshot:
    tasks = []
    milestones = {}
    for item in payload["tasks"]:
        tasks.append({
            "task_id": item["task_id"], "title": item["task_name"],
            "start_date": item["planned_start_date"], "due_date": item["planned_end_date"],
            "expected_progress": item.get("expected_progress"),
            "priority": item.get("priority", "medium").lower(),
            "dependency_ids": item.get("dependencies", []),
        })
        if item.get("milestone"):
            name = item["milestone"]
            entry = milestones.setdefault(name, {"milestone_id": f"M{len(milestones) + 1}", "title": name,
                                              "due_date": item["planned_end_date"], "task_ids": []})
            entry["task_ids"].append(item["task_id"])
            if date.fromisoformat(item["planned_end_date"]) > date.fromisoformat(entry["due_date"]):
                entry["due_date"] = item["planned_end_date"]
    return PlanningSnapshot.model_validate({"project_id": payload["project_id"],
        "updated_on": updated_on, "tasks": tasks, "milestones": list(milestones.values())})


def adapt_actual(payload: dict, updated_on: date) -> ActualProjectSnapshot:
    statuses = {"in progress": "in_progress", "not started": "not_started",
                "completed": "completed", "blocked": "blocked"}
    tasks = [{"task_id": item["task_id"], "progress": item["actual_progress"],
              "status": statuses[item["status"].strip().lower()],
              "updated_on": item["last_updated"],
              "completed_on": item.get("actual_completion_date")}
             for item in payload["tasks"]]
    return ActualProjectSnapshot.model_validate({"project_id": payload["project_id"],
        "updated_on": updated_on, "tasks": tasks})


def adapt_risks(payload: dict, updated_on: date) -> RiskSnapshot:
    risks = [{"risk_id": item["risk_id"], "description": item["description"],
              "level": item["risk_level"].lower(),
              "task_ids": [item["task_id"]] if item.get("task_id") else []}
             for item in payload["risks"]]
    return RiskSnapshot.model_validate({"project_id": payload["project_id"],
        "updated_on": updated_on, "risks": risks})
