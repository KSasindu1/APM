class EmbeddingService:
    """
    Generates semantic embeddings for ProjectFlow AI knowledge.

    The embedding model is used for:
    - uploaded project documents
    - semantic document retrieval
    - knowledge retrieval before RAG
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
    ):
        self.model_name = model_name
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required. Install it with "
                "'pip install sentence-transformers'."
            ) from exc

        self.model = SentenceTransformer(model_name)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple text chunks.
        """

        if not texts:
            return []

        embeddings = self.model.encode(
            texts,
            normalize_embeddings=True,
        )

        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        """
        Generate an embedding for a search query.
        """

        if not query or not query.strip():
            raise ValueError("Query cannot be empty.")

        embedding = self.model.encode(
            [query],
            normalize_embeddings=True,
        )[0]

        return embedding.tolist()