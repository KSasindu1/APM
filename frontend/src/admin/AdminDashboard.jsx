import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AdminApiError, adminApi, adminErrorMessage, isAdminSessionValid, validateAdminKnowledgeResponse } from "../services/adminApi.js";

import "./Admin.css";

function AdminDashboard({ session, onLogout }) {
  const [projectId, setProjectId] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);

  const [uploading, setUploading] = useState(false);
  const [loadingDocuments, setLoadingDocuments] = useState(false);
  const [loadingChunks, setLoadingChunks] = useState(false);
  const [searching, setSearching] = useState(false);
  const [deletingDocumentId, setDeletingDocumentId] = useState(null);

  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [knowledgeError, setKnowledgeError] = useState("");

  const [knowledge, setKnowledge] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [selectedDocument, setSelectedDocument] = useState(null);
  const [chunks, setChunks] = useState([]);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);

  const scope = useRef({ mounted: false, generation: 0, requests: new Map() });
  const fileInput = useRef(null);

  useEffect(() => {
    const current = scope.current;
    current.mounted = true;
    return () => {
      current.mounted = false;
      current.generation += 1;
      current.requests.forEach(operation => operation.controller.abort());
      current.requests.clear();
    };
  }, []);

  function cancelRequest(kind) {
    scope.current.requests.get(kind)?.controller.abort();
    scope.current.requests.delete(kind);
  }

  function invalidateRequests() {
    scope.current.generation += 1;
    scope.current.requests.forEach(operation => operation.controller.abort());
    scope.current.requests.clear();
    setUploading(false);
    setLoadingDocuments(false);
    setLoadingChunks(false);
    setSearching(false);
    setDeletingDocumentId(null);
  }

  function clearKnowledgeResults() {
    setKnowledge(null);
    setDocuments([]);
    setSelectedDocument(null);
    setChunks([]);
    setSearchResults([]);
    setKnowledgeError("");
    setMessage("");
  }

  function clearSensitiveState() {
    invalidateRequests();
    clearKnowledgeResults();
    setProjectId("");
    setSelectedFile(null);
    if (fileInput.current) fileInput.current.value = "";
    setSearchQuery("");
    setMessage("");
    setError("");
  }

  function handleLogout() {
    clearSensitiveState();
    onLogout();
  }

  function beginRequest(kind, id) {
    if (!isAdminSessionValid(session)) {
      handleLogout();
      return null;
    }
    cancelRequest(kind);
    const operation = { kind, id, generation: scope.current.generation, controller: new AbortController() };
    scope.current.requests.set(kind, operation);
    return operation;
  }

  function isCurrent(operation) {
    return scope.current.mounted && scope.current.generation === operation.generation && scope.current.requests.get(operation.kind) === operation && !operation.controller.signal.aborted;
  }

  async function apiRequest(operation, path, options = {}) {
    try {
      return await adminApi.request(path, session, { ...options, signal: operation.controller.signal });
    } catch (err) {
      if (isCurrent(operation) && err.status === 401) handleLogout();
      throw err;
    }
  }

  function showRequestError(operation, err, setter) {
    if (isCurrent(operation) && err?.name !== "AbortError") setter(adminErrorMessage(err));
  }

  function finishRequest(operation, setter) {
    if (isCurrent(operation)) {
      scope.current.requests.delete(operation.kind);
      setter(false);
    }
  }

  async function loadDocuments(projectOverride) {
    if (projectOverride === undefined && (uploading || deletingDocumentId !== null)) return;
    const id = (projectOverride ?? projectId).trim();
    setKnowledgeError("");
    setMessage("");
    setError("");
    if (!id) {
      setKnowledgeError("Enter a project ID first.");
      return;
    }
    const operation = beginRequest("documents", id);
    if (!operation) return;
    cancelRequest("chunks");
    cancelRequest("search");
    setLoadingChunks(false);
    setSearching(false);
    setLoadingDocuments(true);
    clearKnowledgeResults();

    try {
      const data = validateAdminKnowledgeResponse(await apiRequest(operation,
        `/api/risk/knowledge/documents?project_id=${encodeURIComponent(id)}`
      ), id, "documents");
      if (!isCurrent(operation)) return;
      setKnowledge(data);
      setDocuments(data.documents);
    } catch (err) {
      showRequestError(operation, err, setKnowledgeError);
    } finally {
      finishRequest(operation, setLoadingDocuments);
    }
  }

  async function viewChunks(document) {
    if (loadingDocuments || uploading || deletingDocumentId !== null) return;
    const id = projectId.trim();
    const operation = beginRequest("chunks", id);
    if (!operation) return;
    setKnowledgeError("");
    setSelectedDocument(document);
    setChunks([]);
    setLoadingChunks(true);

    try {
      const data = validateAdminKnowledgeResponse(await apiRequest(operation,
        `/api/risk/knowledge/documents/${encodeURIComponent(document.document_id)}/chunks?project_id=${encodeURIComponent(id)}`
      ), id, "chunks", document.document_id);
      if (isCurrent(operation)) setChunks(data.chunks);
    } catch (err) {
      showRequestError(operation, err, setKnowledgeError);
    } finally {
      finishRequest(operation, setLoadingChunks);
    }
  }

  async function deleteDocument(document) {
    const id = projectId.trim();

    if (!id) {
      setKnowledgeError("Enter a project ID first.");
      return;
    }

    if (deletingDocumentId !== null || loadingDocuments || loadingChunks || uploading || scope.current.requests.has("delete")) {
      return;
    }

    const confirmed = window.confirm(
      `Are you sure you want to delete "${document.name}" from this project's indexed knowledge base?\n\n` +
        "This permanently removes its indexed chunks from ChromaDB. " +
        "The original PDF file is not deleted."
    );

    if (!confirmed) {
      return;
    }

    const operation = beginRequest("delete", id);
    if (!operation) return;
    cancelRequest("search");
    setSearching(false);
    setSearchResults([]);
    setKnowledgeError("");
    setMessage("");
    setError("");
    setDeletingDocumentId(document.document_id);

    try {
      const data = await apiRequest(operation,
        `/api/risk/knowledge/documents/${encodeURIComponent(document.document_id)}?project_id=${encodeURIComponent(id)}`,
        { method: "DELETE" }
      );
      if (!isCurrent(operation)) return;
      if (data.project_id !== id || data.document_id !== document.document_id || !Number.isSafeInteger(data.deleted_chunks) || data.deleted_chunks <= 0) {
        throw new AdminApiError("response");
      }

      if (selectedDocument?.document_id === document.document_id) {
        setSelectedDocument(null);
        setChunks([]);
      }

      // Keep deletion, refresh, and confirmation bound to the original project.
      await loadDocuments(id);
      if (isCurrent(operation)) {
        setMessage(`Document deleted successfully. ${data.deleted_chunks} indexed chunks removed.`);
      }
    } catch (err) {
      showRequestError(operation, err, setKnowledgeError);
    } finally {
      finishRequest(operation, () => setDeletingDocumentId(null));
    }
  }

  async function handleSearch(event) {
    event.preventDefault();
    if (deletingDocumentId !== null) return;
    setKnowledgeError("");
    setSearchResults([]);
    const id = projectId.trim();
    if (!id) {
      setKnowledgeError("Enter a project ID before searching.");
      return;
    }
    if (!searchQuery.trim()) {
      setKnowledgeError("Enter a search question or phrase.");
      return;
    }
    const operation = beginRequest("search", id);
    if (!operation) return;
    setSearching(true);

    try {
      const data = validateAdminKnowledgeResponse(await apiRequest(operation, "/api/risk/knowledge/search", {
        method: "POST",
        body: { project_id: id, query: searchQuery.trim(), top_k: 5 },
      }), id, "results");
      if (isCurrent(operation)) setSearchResults(data.results);
    } catch (err) {
      showRequestError(operation, err, setKnowledgeError);
    } finally {
      finishRequest(operation, setSearching);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    if (uploading || deletingDocumentId !== null) return;
    setMessage("");
    setError("");
    const id = projectId.trim();
    if (!id) {
      setError("Please enter a project ID.");
      return;
    }
    if (!selectedFile) {
      setError("Please select a PDF document.");
      return;
    }
    if (!selectedFile.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files are supported.");
      return;
    }
    const operation = beginRequest("upload", id);
    if (!operation) return;
    const formData = new FormData();
    formData.append("file", selectedFile);
    setUploading(true);

    try {
      const data = await apiRequest(operation, `/api/risk/documents?project_id=${encodeURIComponent(id)}`, { method: "POST", body: formData });
      if (!isCurrent(operation)) return;
      const count = data.result?.chunks_created;
      const confirmation = `Document uploaded successfully.${Number.isSafeInteger(count) && count >= 0 ? ` ${count} chunks indexed.` : ""}`;
      setSelectedFile(null);
      if (fileInput.current) fileInput.current.value = "";
      // Keep the refresh and confirmation bound to the original project/upload.
      await loadDocuments(id);
      if (isCurrent(operation)) setMessage(confirmation);
    } catch (err) {
      showRequestError(operation, err, setError);
    } finally {
      finishRequest(operation, setUploading);
    }
  }

  return (
    <main className="admin-dashboard">
      <div className="admin-dashboard__container">
        <header className="admin-dashboard__header">
          <div className="admin-dashboard__identity">
            <p className="admin-eyebrow">
              PROJECTFLOW AI
            </p>

            <h1 className="admin-title">
              Administrator dashboard
            </h1>

            <p className="admin-dashboard__session">
              Signed in as {session.username}
            </p>
          </div>

          <div className="admin-dashboard__actions">
            <Link to="/monitoring" onClick={clearSensitiveState} className="admin-button admin-button--secondary">
              Monitor all projects
            </Link>

            <Link to="/schedule" onClick={clearSensitiveState} className="admin-button admin-button--secondary">
              Project schedules
            </Link>


            <button type="button" onClick={handleLogout} className="admin-button admin-button--danger">
              Sign out
            </button>
          </div>
        </header>

        <section className="admin-stats">
          <article className="admin-card admin-stat">
            <p className="admin-stat__label">Account role</p>
            <h2 className="admin-stat__value">Administrator</h2>
            <p className="admin-stat__description">
              Authenticated access to protected knowledge management.
            </p>
          </article>

          <article className="admin-card admin-stat">
            <p className="admin-stat__label">Indexed documents</p>
            <h2 className="admin-stat__value">
              {knowledge ? knowledge.total_documents : "—"}
            </h2>
            <p className="admin-stat__description">
              PDF documents indexed for the selected project.
            </p>
          </article>

          <article className="admin-card admin-stat">
            <p className="admin-stat__label">Stored chunks</p>
            <h2 className="admin-stat__value">
              {knowledge ? knowledge.total_chunks : "—"}
            </h2>
            <p className="admin-stat__description">
              Text chunks available in the existing ChromaDB collection.
            </p>
          </article>
        </section>


        <section className="admin-card admin-upload">
          <h2 className="admin-panel-title">
            Upload project document
          </h2>

          <p className="admin-intro">
            Upload a PDF to extract text, generate embeddings, identify named
            entities, and add project knowledge to ChromaDB.
          </p>

          <form onSubmit={handleUpload} className="admin-upload__form">
            <label htmlFor="project-id" className="admin-label admin-upload__project-label">
              Project ID
            </label>

            <input
              id="project-id"
              type="text"
              value={projectId}
              onChange={(event) => {
                clearSensitiveState();
                setProjectId(event.target.value);
              }}
              placeholder="e.g. OUTDOOR_WIRELESS_AT_MIT"
              maxLength={200}
              required
              className="admin-input admin-upload__project-input"
            />

            <label htmlFor="project-document" className="admin-label admin-upload__file-label">
              PDF document
            </label>

            <input
              id="project-document"
              ref={fileInput}
              type="file"
              accept=".pdf,application/pdf"
              onChange={(event) => {
                cancelRequest("upload");
                setUploading(false);
                setMessage("");
                setError("");
                setSelectedFile(event.target.files?.[0] || null);
              }}
              required
              className="admin-input admin-input--file admin-upload__file-input"
            />

            {message && <p role="status" className="admin-message admin-message--success">{message}</p>}
            {error && <p role="alert" className="admin-message admin-message--error">{error}</p>}

            <button
              type="submit"
              disabled={uploading || deletingDocumentId !== null}
              className="admin-button admin-button--primary admin-upload__submit"
            >
              {uploading ? "Uploading document..." : "Upload PDF"}
            </button>
          </form>
        </section>

        <section className="admin-card admin-knowledge">
          <h2 className="admin-panel-title">
            ChromaDB Knowledge Base
          </h2>

          <p className="admin-intro">
            Browse, inspect, and delete indexed documents and chunks stored for
            a project. Use the same project ID that you used when uploading
            its PDFs.
          </p>

          <button
            type="button"
            onClick={() => loadDocuments()}
            disabled={loadingDocuments || uploading || deletingDocumentId !== null}
            className="admin-button admin-button--primary admin-knowledge__load"
          >
            {loadingDocuments ? "Loading knowledge..." : "Load project knowledge"}
          </button>

          {knowledgeError && (
            <p role="alert" className="admin-message admin-message--error">
              {knowledgeError}
            </p>
          )}

          {knowledge && (
            <div className="admin-knowledge__body">
              <div className="admin-knowledge__summary">
                <strong>Project:</strong> {knowledge.project_id}
                <span>
                  <strong>Documents:</strong> {knowledge.total_documents}
                </span>
                <span>
                  <strong>Chunks:</strong> {knowledge.total_chunks}
                </span>
              </div>

              <h3 className="admin-subheading">Indexed documents</h3>

              {documents.length === 0 ? (
                <p className="admin-muted">
                  No indexed documents were found for this project.
                </p>
              ) : (
                <div className="admin-document-list">
                  {documents.map((document) => (
                    <article key={document.document_id} className="admin-document">
                      <div className="admin-document__body">
                        <h4 className="admin-document__name">
                          {document.name}
                        </h4>

                        <p className="admin-muted">
                          {document.chunks_count} chunks
                        </p>

                        <details className="admin-details">
                          <summary className="admin-details__summary">
                            Document metadata
                          </summary>

                          <pre className="admin-metadata">
                            {JSON.stringify(
                              {
                                document_id: document.document_id,
                                project_id: document.project_id,
                                source: document.source,
                                document_hash: document.document_hash,
                              },
                              null,
                              2
                            )}
                          </pre>
                        </details>
                      </div>

                      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "flex-start" }}>
                        <button
                          type="button"
                          onClick={() => viewChunks(document)}
                          disabled={loadingChunks || deletingDocumentId !== null || loadingDocuments || uploading}
                          className="admin-button admin-button--secondary"
                        >
                          View chunks
                        </button>

                        <button
                          type="button"
                          onClick={() => deleteDocument(document)}
                          disabled={deletingDocumentId !== null || loadingDocuments || loadingChunks || uploading}
                          className="admin-button admin-button--danger"
                        >
                          {deletingDocumentId === document.document_id
                            ? "Deleting..."
                            : "Delete"}
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              )}

              {selectedDocument && (
                <div className="admin-chunks">
                  <h3 className="admin-subheading">
                    Chunks: {selectedDocument.name}
                  </h3>

                  {loadingChunks && (
                    <p className="admin-muted">Loading stored chunks...</p>
                  )}

                  {!loadingChunks && chunks.length === 0 && (
                    <p className="admin-muted">No chunks to display.</p>
                  )}

                  <div className="admin-result-list">
                    {chunks.map((chunk) => (
                      <article key={chunk.chunk_id} className="admin-result">
                        <div className="admin-result__header admin-result__header--chunk">
                          <strong>Chunk {chunk.chunk_index}</strong>
                          <span className="admin-chip">Stored in ChromaDB</span>
                        </div>

                        <p className="admin-result__text">
                          {chunk.text || "(No text stored)"}
                        </p>

                        <details className="admin-details">
                          <summary className="admin-details__summary">
                            View chunk metadata
                          </summary>

                          <pre className="admin-metadata">
                            {JSON.stringify(
                              {
                                chunk_id: chunk.chunk_id,
                                ...chunk.metadata,
                              },
                              null,
                              2
                            )}
                          </pre>
                        </details>
                      </article>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>

        <section className="admin-card admin-search">
          <h2 className="admin-panel-title">
            Search project knowledge
          </h2>

          <p className="admin-intro">
            Search relevant document chunks and any matching Planning Agent
            knowledge available through the existing search endpoint.
          </p>

          <form onSubmit={handleSearch} className="admin-search__form">
            <label htmlFor="knowledge-query" className="admin-label">
              Search question or phrase
            </label>

            <textarea
              id="knowledge-query"
              value={searchQuery}
              onChange={(event) => {
                cancelRequest("search");
                setSearching(false);
                setSearchResults([]);
                setSearchQuery(event.target.value);
              }}
              placeholder="e.g. What are the project's main resource risks?"
              rows={3}
              className="admin-input admin-input--textarea"
              required
            />

            <button
              type="submit"
              disabled={searching || deletingDocumentId !== null}
              className="admin-button admin-button--primary admin-search__submit"
            >
              {searching ? "Searching..." : "Search knowledge"}
            </button>
          </form>

          {searchResults.length > 0 && (
            <div className="admin-search__results">
              <h3 className="admin-subheading">
                Search results ({searchResults.length})
              </h3>

              <div className="admin-result-list">
                {searchResults.map((result, index) => (
                  <article
                    key={result.chunk_id || result.record_id || index}
                    className="admin-result"
                  >
                    <div className="admin-result__header">
                      <span className="admin-chip">
                        {result.knowledge_type || "Knowledge"}
                      </span>
                      <strong>{result.source || "Unknown source"}</strong>
                    </div>

                    {result.score != null && (
                      <p className="admin-muted">
                        Relevance score: {Number(result.score).toFixed(4)}
                      </p>
                    )}

                    <p className="admin-result__text">
                      {result.content || "(No content returned)"}
                    </p>

                    <details className="admin-details">
                      <summary className="admin-details__summary">
                        Result metadata
                      </summary>

                      <pre className="admin-metadata">
                        {JSON.stringify(
                          {
                            chunk_id: result.chunk_id,
                            record_id: result.record_id,
                            metadata: result.metadata || {},
                          },
                          null,
                          2
                        )}
                      </pre>
                    </details>
                  </article>
                ))}
              </div>
            </div>
          )}

          {!searching &&
            searchResults.length === 0 &&
            searchQuery.trim() !== "" && (
              <p className="admin-muted">
                Run a search to see matching knowledge here.
              </p>
            )}
        </section>
      </div>
    </main>
  );
}



export default AdminDashboard;