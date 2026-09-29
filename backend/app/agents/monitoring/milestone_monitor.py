"""Classify milestones using their planned task memberships and due dates."""

from datetime import date

from .schemas import ActualProjectSnapshot, MilestoneAssessment, MonitoringSettings, PlanningSnapshot, TaskAssessment


def assess_milestones(planning: PlanningSnapshot, actual: ActualProjectSnapshot,
                      tasks: list[TaskAssessment], as_of: date,
                      settings: MonitoringSettings) -> list[MilestoneAssessment]:
    by_id = {task.task_id: task for task in tasks}
    actual_by_id = {task.task_id: task for task in actual.tasks}
    results = []
    for milestone in planning.milestones:
        members = [by_id[task_id] for task_id in milestone.task_ids]
        complete = all(t.status == "completed" for t in members)
        overdue = as_of > milestone.due_date and not complete
        completed_late = complete and any(
            (actual_by_id[t.task_id].completed_on or date.min) > milestone.due_date
            for t in members
        )
        approaching = 0 <= (milestone.due_date - as_of).days <= settings.deadline_approaching_days
        if overdue or completed_late:
            status = "Delayed"
        elif complete:
            status = "Completed"
        elif any(t.delayed or t.status == "missing" for t in members) or approaching:
            status = "At Risk"
        else:
            status = "On Track"
        results.append(MilestoneAssessment(
            milestone_id=milestone.milestone_id, title=milestone.title,
            progress=(round(sum(t.actual_progress or 0 for t in members) / len(members), 2)
                      if all(t.actual_progress is not None for t in members) else None),
            completed=complete, overdue=overdue, status=status,
        ))
    return results
