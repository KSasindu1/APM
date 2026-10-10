const BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

async function post(path, payload) {
  const headers = { "Content-Type": "application/json" };
  if (import.meta.env.VITE_PLANNING_API_KEY) headers["X-API-Key"] = import.meta.env.VITE_PLANNING_API_KEY;
  let response;
  try {
    response = await fetch(`${BASE_URL}/api/coordinator/${path}`, { method: "POST", headers, body: JSON.stringify(payload) });
  } catch {
    throw new Error(`Cannot reach the Coordinator API at ${BASE_URL}. Start the backend from the backend folder with: python -m uvicorn app.main:app --reload. If it is already running, check its startup errors and that CORS allows this page's origin (${window.location.origin}).`);
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`The API returned a non-JSON response (${response.status}). Check the backend URL and server logs.`);
  }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((item) => `${item.loc.join(".")}: ${item.msg}`).join("; ") : data.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return data;
}

export const interpretProjectInput = (text) => post("interpret", { text });
export const submitCoordinatorRequest = (payload) => post("request", payload);
