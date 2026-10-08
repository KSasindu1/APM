TASK_DECOMPOSITION_PROMPT = """
You are the Planning and Scheduling Agent of an
AI-powered project management system.

Convert the project objective into a realistic set of
executable project tasks.

PROJECT NAME:
{project_name}

PROJECT OBJECTIVE:
{objective}

PROJECT START DATE:
{start_date}

PROJECT DEADLINE:
{deadline}

AVAILABLE PROJECT DAYS:
{available_days}

Return ONLY valid JSON.

The top-level JSON value MUST be an object with one key:
"tasks".

Example:

{{
  "tasks": [
    {{
      "task_id": "T001",
      "name": "Requirements Analysis",
      "description": "Analyze project requirements.",
      "duration_days": 3,
      "priority": "HIGH",
      "dependencies": []
    }}
  ]
}}

Every task MUST contain exactly these fields:

- task_id
- name
- description
- duration_days
- priority
- dependencies

Rules:

1. Every task_id must be unique.

2. Task IDs must use:
   T001, T002, T003, etc.

3. duration_days must be a positive integer.

4. priority must be exactly one of:
   LOW
   MEDIUM
   HIGH
   CRITICAL

5. dependencies must contain only existing task IDs.

6. Do not create circular dependencies.

7. Dependencies must logically represent task order.

8. Create practical implementation tasks.

9. Include requirements, design, implementation,
   testing and deployment when appropriate.

10. Do not create unnecessary tasks.

11. Do not invent information that is not provided.

12. Create approximately 5-12 tasks depending on
    project complexity.

13. Use "name", never "task_name".

14. The total duration must be reasonable relative
    to the available project days.

15. Do not create tasks merely to fill available time.

16. The first task should normally have no dependencies.

17. Later tasks should depend on earlier tasks only
    when there is a real logical dependency.

18. Do not use dates inside individual tasks.

19. Do not include markdown.

20. Do not include explanations outside the JSON object.

21. Return the JSON object only.
"""