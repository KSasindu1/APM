
import { useState } from "react";

function AdminDashboard({ session, onLogout, onBack }) {
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

  // Keep VITE_API_BASE_URL if it is configured in your frontend .env.
  const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

  async function apiRequest(path, options = {}) {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        ...(options.body instanceof FormData
          ? {}
          : options.body
            ? { "Content-Type": "application/json" }
            : {}),
        ...options.headers,
      },
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        throw new Error(
          "Your administrator session may have expired or lacks permission. Please sign in again."
        );
      }

      throw new Error(
        data.detail || `Request failed with status ${response.status}.`
      );
    }

    return data;
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

  async function loadDocuments(projectOverride) {
    const id = (projectOverride ?? projectId).trim();

    setKnowledgeError("");
    setMessage("");
    setError("");

    if (!id) {
      setKnowledgeError("Enter a project ID first.");
      return;
    }

    setLoadingDocuments(true);
    setKnowledge(null);
    setDocuments([]);
    setSelectedDocument(null);
    setChunks([]);
    setSearchResults([]);

    try {
      const data = await apiRequest(
        `/api/risk/knowledge/documents?project_id=${encodeURIComponent(id)}`
      );

      setKnowledge(data);
      setDocuments(data.documents || []);
    } catch (err) {
      setKnowledgeError(
        `${err.message} Make sure the knowledge-management endpoints have been added to the backend.`
      );
    } finally {
      setLoadingDocuments(false);
    }
  }

  async function viewChunks(document) {
    setKnowledgeError("");
    setSelectedDocument(document);
    setChunks([]);
    setLoadingChunks(true);

    try {
      const data = await apiRequest(
        `/api/risk/knowledge/documents/${encodeURIComponent(
          document.document_id
        )}/chunks?project_id=${encodeURIComponent(projectId.trim())}`
      );

      setChunks(data.chunks || []);
    } catch (err) {
      setKnowledgeError(err.message);
    } finally {
      setLoadingChunks(false);
    }
  }

  async function deleteDocument(document) {
    const id = projectId.trim();

    if (!id) {
      setKnowledgeError("Enter a project ID first.");
      return;
    }

    if (deletingDocumentId !== null || loadingDocuments || loadingChunks) {
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

    setKnowledgeError("");
    setMessage("");
    setError("");
    setDeletingDocumentId(document.document_id);

    try {
      const data = await apiRequest(
        `/api/risk/knowledge/documents/${encodeURIComponent(
          document.document_id
        )}?project_id=${encodeURIComponent(id)}`,
        {
          method: "DELETE",
        }
      );

      if (selectedDocument?.document_id === document.document_id) {
        setSelectedDocument(null);
        setChunks([]);
      }

      setSearchResults([]);

      // Refresh the knowledge base after a successful deletion.
      await loadDocuments(id);

      setMessage(
        `${data.message || "Document deleted successfully."} ` +
          `${data.deleted_chunks ?? 0} indexed chunks removed.`
      );
    } catch (err) {
      setKnowledgeError(err.message);
    } finally {
      setDeletingDocumentId(null);
    }
  }

  async function handleSearch(event) {
    event.preventDefault();

    setKnowledgeError("");
    setSearchResults([]);

    if (!projectId.trim()) {
      setKnowledgeError("Enter a project ID before searching.");
      return;
    }

    if (!searchQuery.trim()) {
      setKnowledgeError("Enter a search question or phrase.");
      return;
    }

    setSearching(true);

    try {
      const data = await apiRequest("/api/risk/knowledge/search", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId.trim(),
          query: searchQuery.trim(),
          top_k: 5,
        }),
      });

      setSearchResults(data.results || []);
    } catch (err) {
      setKnowledgeError(err.message);
    } finally {
      setSearching(false);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    setMessage("");
    setError("");

    if (!projectId.trim()) {
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

    const formData = new FormData();
    formData.append("file", selectedFile);

    setUploading(true);

    try {
      const data = await apiRequest(
        `/api/risk/documents?project_id=${encodeURIComponent(
          projectId.trim()
        )}`,
        {
          method: "POST",
          body: formData,
        }
      );

      const uploadedName = selectedFile.name;

      setSelectedFile(null);
      event.target.reset();

      // Refresh the list so the newly indexed document appears.
      await loadDocuments(projectId.trim());

      setMessage(
        `${data.message || "Document uploaded successfully."} ${
          data.result?.chunks_created != null
            ? `${data.result.chunks_created} chunks indexed.`
            : ""
        }`
      );

      if (!uploadedName) {
        setMessage(data.message || "Document uploaded successfully.");
      }
    } catch (err) {
      setError(
        `${err.message} Check that the backend is running at ${API_BASE_URL}.`
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        padding: "32px 20px",
        background: "#f8fafc",
        color: "#172033",
        fontFamily: '"Segoe UI", Arial, sans-serif',
      }}
    >
      <div style={{ maxWidth: "1100px", margin: "0 auto" }}>
        <header
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "16px",
            flexWrap: "wrap",
            marginBottom: "32px",
          }}
        >
          <div>
            <p
              style={{
                margin: "0 0 6px",
                color: "#5b4de8",
                fontSize: "12px",
                fontWeight: 800,
                letterSpacing: "1.2px",
              }}
            >
              PROJECTFLOW AI
            </p>

            <h1 style={{ margin: 0, fontSize: "30px" }}>
              Administrator dashboard
            </h1>

            <p style={{ color: "#64748b", marginBottom: 0 }}>
              Signed in as {session.username}
            </p>
          </div>

          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
            <button type="button" onClick={onBack} style={secondaryButtonStyle}>
              Back to home
            </button>

            <button type="button" onClick={onLogout} style={logoutButtonStyle}>
              Sign out
            </button>
          </div>
        </header>

        <section
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: "18px",
            marginBottom: "28px",
          }}
        >
          <article style={cardStyle}>
            <p style={cardLabelStyle}>Account role</p>
            <h2 style={cardValueStyle}>Administrator</h2>
            <p style={cardDescriptionStyle}>
              Authenticated access to protected knowledge management.
            </p>
          </article>

          <article style={cardStyle}>
            <p style={cardLabelStyle}>Indexed documents</p>
            <h2 style={cardValueStyle}>
              {knowledge ? knowledge.total_documents : "—"}
            </h2>
            <p style={cardDescriptionStyle}>
              PDF documents indexed for the selected project.
            </p>
          </article>

          <article style={cardStyle}>
            <p style={cardLabelStyle}>Stored chunks</p>
            <h2 style={cardValueStyle}>
              {knowledge ? knowledge.total_chunks : "—"}
            </h2>
            <p style={cardDescriptionStyle}>
              Text chunks available in the existing ChromaDB collection.
            </p>
          </article>
        </section>

        <section style={{ ...cardStyle, marginBottom: "24px" }}>
          <h2 style={{ marginTop: 0, marginBottom: "8px" }}>
            Upload project document
          </h2>

          <p style={{ color: "#64748b", lineHeight: 1.6 }}>
            Upload a PDF to extract text, generate embeddings, identify named
            entities, and add project knowledge to ChromaDB.
          </p>

          <form onSubmit={handleUpload}>
            <label htmlFor="project-id" style={labelStyle}>
              Project ID
            </label>

            <input
              id="project-id"
              type="text"
              value={projectId}
              onChange={(event) => {
                setProjectId(event.target.value);
                clearKnowledgeResults();
              }}
              placeholder="e.g. OUTDOOR_WIRELESS_AT_MIT"
              maxLength={200}
              required
              style={inputStyle}
            />

            <label htmlFor="project-document" style={labelStyle}>
              PDF document
            </label>

            <input
              id="project-document"
              type="file"
              accept=".pdf,application/pdf"
              onChange={(event) =>
                setSelectedFile(event.target.files?.[0] || null)
              }
              required
              style={{ ...inputStyle, padding: "10px" }}
            />

            {message && (
              <p role="status" style={successMessageStyle}>
                {message}
              </p>
            )}

            {error && (
              <p role="alert" style={errorMessageStyle}>
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={uploading}
              style={{
                ...primaryButtonStyle,
                opacity: uploading ? 0.7 : 1,
                cursor: uploading ? "wait" : "pointer",
              }}
            >
              {uploading ? "Uploading document..." : "Upload PDF"}
            </button>
          </form>
        </section>

        <section style={{ ...cardStyle, marginBottom: "24px" }}>
          <h2 style={{ marginTop: 0, marginBottom: "8px" }}>
            ChromaDB Knowledge Base
          </h2>

          <p style={{ color: "#64748b", lineHeight: 1.6 }}>
            Browse, inspect, and delete indexed documents and chunks stored for
            a project. Use the same project ID that you used when uploading
            its PDFs.
          </p>

          <button
            type="button"
            onClick={() => loadDocuments()}
            disabled={loadingDocuments || deletingDocumentId !== null}
            style={{
              ...primaryButtonStyle,
              marginTop: "8px",
              opacity: loadingDocuments ? 0.7 : 1,
            }}
          >
            {loadingDocuments ? "Loading knowledge..." : "Load project knowledge"}
          </button>

          {knowledgeError && (
            <p role="alert" style={errorMessageStyle}>
              {knowledgeError}
            </p>
          )}

          {knowledge && (
            <div style={{ marginTop: "24px" }}>
              <div style={summaryStyle}>
                <strong>Project:</strong> {knowledge.project_id}
                <span>
                  <strong>Documents:</strong> {knowledge.total_documents}
                </span>
                <span>
                  <strong>Chunks:</strong> {knowledge.total_chunks}
                </span>
              </div>

              <h3 style={subheadingStyle}>Indexed documents</h3>

              {documents.length === 0 ? (
                <p style={mutedTextStyle}>
                  No indexed documents were found for this project.
                </p>
              ) : (
                <div style={{ display: "grid", gap: "12px" }}>
                  {documents.map((document) => (
                    <article key={document.document_id} style={documentStyle}>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <h4
                          style={{
                            margin: "0 0 8px",
                            overflowWrap: "anywhere",
                          }}
                        >
                          {document.name}
                        </h4>

                        <p style={mutedTextStyle}>
                          {document.chunks_count} chunks
                        </p>

                        <details>
                          <summary
                            style={{ cursor: "pointer", color: "#64748b" }}
                          >
                            Document metadata
                          </summary>

                          <pre style={metadataStyle}>
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

                      <div
                        style={{
                          display: "flex",
                          gap: "8px",
                          flexWrap: "wrap",
                          alignItems: "flex-start",
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => viewChunks(document)}
                          disabled={
                            loadingChunks ||
                            deletingDocumentId !== null ||
                            loadingDocuments
                          }
                          style={{
                            ...secondaryButtonStyle,
                            opacity:
                              loadingChunks ||
                              deletingDocumentId !== null ||
                              loadingDocuments
                                ? 0.7
                                : 1,
                          }}
                        >
                          View chunks
                        </button>

                        <button
                          type="button"
                          onClick={() => deleteDocument(document)}
                          disabled={
                            deletingDocumentId !== null ||
                            loadingDocuments ||
                            loadingChunks
                          }
                          style={{
                            ...secondaryButtonStyle,
                            color: "#b91c1c",
                            border: "1px solid #fecaca",
                            background: "#fff",
                            opacity:
                              deletingDocumentId === document.document_id
                                ? 0.7
                                : 1,
                            cursor:
                              deletingDocumentId !== null ||
                              loadingDocuments ||
                              loadingChunks
                                ? "not-allowed"
                                : "pointer",
                          }}
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
                <div style={{ marginTop: "28px" }}>
                  <h3 style={subheadingStyle}>
                    Chunks: {selectedDocument.name}
                  </h3>

                  {loadingChunks && (
                    <p style={mutedTextStyle}>Loading stored chunks...</p>
                  )}

                  {!loadingChunks && chunks.length === 0 && (
                    <p style={mutedTextStyle}>No chunks to display.</p>
                  )}

                  <div style={{ display: "grid", gap: "14px" }}>
                    {chunks.map((chunk) => (
                      <article key={chunk.chunk_id} style={chunkStyle}>
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            gap: "10px",
                            flexWrap: "wrap",
                            marginBottom: "12px",
                          }}
                        >
                          <strong>Chunk {chunk.chunk_index}</strong>
                          <span style={chipStyle}>Stored in ChromaDB</span>
                        </div>

                        <p style={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}>
                          {chunk.text || "(No text stored)"}
                        </p>

                        <details>
                          <summary
                            style={{ cursor: "pointer", color: "#64748b" }}
                          >
                            View chunk metadata
                          </summary>

                          <pre style={metadataStyle}>
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

        <section style={cardStyle}>
          <h2 style={{ marginTop: 0, marginBottom: "8px" }}>
            Search project knowledge
          </h2>

          <p style={{ color: "#64748b", lineHeight: 1.6 }}>
            Search relevant document chunks and any matching Planning Agent
            knowledge available through the existing search endpoint.
          </p>

          <form onSubmit={handleSearch}>
            <label htmlFor="knowledge-query" style={labelStyle}>
              Search question or phrase
            </label>

            <textarea
              id="knowledge-query"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="e.g. What are the project's main resource risks?"
              rows={3}
              style={{ ...inputStyle, resize: "vertical" }}
              required
            />

            <button
              type="submit"
              disabled={searching}
              style={{
                ...primaryButtonStyle,
                opacity: searching ? 0.7 : 1,
              }}
            >
              {searching ? "Searching..." : "Search knowledge"}
            </button>
          </form>

          {searchResults.length > 0 && (
            <div style={{ marginTop: "24px" }}>
              <h3 style={subheadingStyle}>
                Search results ({searchResults.length})
              </h3>

              <div style={{ display: "grid", gap: "14px" }}>
                {searchResults.map((result, index) => (
                  <article
                    key={result.chunk_id || result.record_id || index}
                    style={chunkStyle}
                  >
                    <div
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        gap: "8px",
                        alignItems: "center",
                        marginBottom: "10px",
                      }}
                    >
                      <span style={chipStyle}>
                        {result.knowledge_type || "Knowledge"}
                      </span>
                      <strong>{result.source || "Unknown source"}</strong>
                    </div>

                    {result.score != null && (
                      <p style={mutedTextStyle}>
                        Relevance score: {Number(result.score).toFixed(4)}
                      </p>
                    )}

                    <p style={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}>
                      {result.content || "(No content returned)"}
                    </p>

                    <details>
                      <summary
                        style={{ cursor: "pointer", color: "#64748b" }}
                      >
                        Result metadata
                      </summary>

                      <pre style={metadataStyle}>
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
              <p style={mutedTextStyle}>
                Run a search to see matching knowledge here.
              </p>
            )}
        </section>
      </div>
    </main>
  );
}

const cardStyle = {
  padding: "26px",
  border: "1px solid #e2e8f0",
  borderRadius: "16px",
  background: "#ffffff",
  boxShadow: "0 8px 24px rgba(15, 23, 42, 0.04)",
};

const cardLabelStyle = {
  margin: "0 0 10px",
  color: "#64748b",
  fontSize: "13px",
};

const cardValueStyle = {
  margin: "0 0 10px",
  fontSize: "21px",
};

const cardDescriptionStyle = {
  margin: 0,
  color: "#64748b",
  fontSize: "13px",
  lineHeight: 1.6,
};

const labelStyle = {
  display: "block",
  margin: "20px 0 8px",
  color: "#334155",
  fontSize: "13px",
  fontWeight: 600,
};

const inputStyle = {
  display: "block",
  boxSizing: "border-box",
  width: "100%",
  padding: "12px 13px",
  border: "1px solid #cbd5e1",
  borderRadius: "9px",
  background: "#ffffff",
  color: "#172033",
  fontSize: "14px",
};

const primaryButtonStyle = {
  marginTop: "22px",
  padding: "13px 20px",
  border: 0,
  borderRadius: "9px",
  background: "linear-gradient(135deg, #4f46e5, #7c3aed)",
  color: "#ffffff",
  fontSize: "14px",
  fontWeight: 700,
  cursor: "pointer",
};

const secondaryButtonStyle = {
  padding: "11px 16px",
  border: "1px solid #cbd5e1",
  borderRadius: "9px",
  background: "#ffffff",
  color: "#334155",
  cursor: "pointer",
  flexShrink: 0,
};

const logoutButtonStyle = {
  padding: "11px 16px",
  border: 0,
  borderRadius: "9px",
  background: "#fee2e2",
  color: "#b91c1c",
  cursor: "pointer",
  fontWeight: 600,
};

const successMessageStyle = {
  padding: "12px",
  borderRadius: "8px",
  background: "#ecfdf5",
  color: "#047857",
  lineHeight: 1.6,
  overflowWrap: "anywhere",
};

const errorMessageStyle = {
  padding: "12px",
  borderRadius: "8px",
  background: "#fef2f2",
  color: "#b91c1c",
  lineHeight: 1.6,
  overflowWrap: "anywhere",
};

const summaryStyle = {
  display: "flex",
  flexWrap: "wrap",
  gap: "16px",
  padding: "14px",
  borderRadius: "10px",
  background: "#f8fafc",
  color: "#334155",
  fontSize: "13px",
};

const subheadingStyle = {
  marginTop: "24px",
  marginBottom: "14px",
  fontSize: "17px",
};

const mutedTextStyle = {
  color: "#64748b",
  fontSize: "13px",
  lineHeight: 1.6,
  overflowWrap: "anywhere",
};

const documentStyle = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: "16px",
  flexWrap: "wrap",
  padding: "18px",
  border: "1px solid #e2e8f0",
  borderRadius: "12px",
  background: "#ffffff",
};

const chunkStyle = {
  padding: "18px",
  border: "1px solid #e2e8f0",
  borderRadius: "12px",
  background: "#ffffff",
  overflowWrap: "anywhere",
};

const chipStyle = {
  display: "inline-block",
  padding: "5px 9px",
  borderRadius: "20px",
  background: "#eef2ff",
  color: "#4338ca",
  fontSize: "12px",
  fontWeight: 600,
};

const metadataStyle = {
  marginTop: "12px",
  padding: "12px",
  borderRadius: "8px",
  background: "#f8fafc",
  color: "#334155",
  fontSize: "12px",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  overflowX: "auto",
};

export default AdminDashboard;