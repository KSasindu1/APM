const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  "http://127.0.0.1:8000";

const API_KEY =
  import.meta.env.VITE_PLANNING_API_KEY || null;

function buildHeaders(hasJsonBody = false) {
  const headers = {};

  if (hasJsonBody) {
    headers["Content-Type"] = "application/json";
  }

  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }

  return headers;
}

async function handleResponse(response) {
  let data = null;

  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail =
      data?.detail ||
      data?.message ||
      `Request failed with status ${response.status}`;

    throw new Error(
      typeof detail === "string"
        ? detail
        : JSON.stringify(detail)
    );
  }

  return data;
}

export async function checkPlanningHealth() {
  const response = await fetch(
    `${API_BASE_URL}/api/planning/health`,
    {
      method: "GET",
      headers: buildHeaders(false),
    }
  );

  return handleResponse(response);
}

export function buildPlanningRequest(form) {
  return {
    project_id: form.project_id,
    project_name: form.project_name,
    objective: form.objective,
    start_date: form.start_date,
    deadline: form.deadline,

    tasks: form.tasks || [],
    resources: form.resources || [],
    risks: form.risks || [],

    use_llm: form.use_llm !== false,
  };
}

export async function createSchedule(form) {
  const response = await fetch(
    `${API_BASE_URL}/api/planning/schedule`,
    {
      method: "POST",
      headers: buildHeaders(true),
      body: JSON.stringify(
        buildPlanningRequest(form)
      ),
    }
  );

  return handleResponse(response);
}

export async function getSchedule(projectId) {
  const response = await fetch(
    `${API_BASE_URL}/api/planning/schedule/${encodeURIComponent(
      projectId
    )}`,
    {
      method: "GET",
      headers: buildHeaders(false),
    }
  );

  return handleResponse(response);
}

/*
 * NEW
 *
 * Gets every saved project schedule.
 */
export async function getAllSchedules() {
  const response = await fetch(
    `${API_BASE_URL}/api/planning/schedules`,
    {
      method: "GET",
      headers: buildHeaders(false),
    }
  );

  return handleResponse(response);
}

export async function updateSchedule(
  projectId,
  form
) {
  const response = await fetch(
    `${API_BASE_URL}/api/planning/schedule/${encodeURIComponent(
      projectId
    )}`,
    {
      method: "PUT",
      headers: buildHeaders(true),
      body: JSON.stringify(
        buildPlanningRequest(form)
      ),
    }
  );

  return handleResponse(response);
}

export async function deleteSchedule(projectId) {
  const response = await fetch(
    `${API_BASE_URL}/api/planning/schedule/${encodeURIComponent(
      projectId
    )}`,
    {
      method: "DELETE",
      headers: buildHeaders(false),
    }
  );

  return handleResponse(response);
}