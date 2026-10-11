from collections import defaultdict

from transformers import pipeline


class NLPService:
    """
    NLP service for the Risk & Knowledge Retrieval Agent.

    Performs Named Entity Recognition (NER) using a
    pretrained transformer model.

    NER identifies entities such as:
    - people
    - organizations
    - locations
    - miscellaneous named entities
    """

    def __init__(
        self,
        model_name: str = "dslim/bert-base-NER",
    ):
        self.model_name = model_name

        self.ner_pipeline = pipeline(
            "ner",
            model=model_name,
            aggregation_strategy="simple",
        )

    def extract_entities(
        self,
        text: str,
    ) -> list[dict]:
        """
        Extract named entities from project text.

        Adjacent entities with the same label are merged
        when they represent fragments of the same entity.
        """

        if not text or not text.strip():
            return []

        entities = self.ner_pipeline(text)

        raw_entities = []

        for entity in entities:

            entity_text = str(
                entity.get(
                    "word",
                    "",
                )
            ).strip()

            if not entity_text:
                continue

            raw_entities.append(
                {
                    "text": entity_text,
                    "label": entity.get(
                        "entity_group",
                        "UNKNOWN",
                    ),
                    "score": float(
                        entity.get(
                            "score",
                            0.0,
                        )
                    ),
                    "start": entity.get(
                        "start"
                    ),
                    "end": entity.get(
                        "end"
                    ),
                }
            )

        if not raw_entities:
            return []

        merged_entities = []

        for entity in raw_entities:

            if not merged_entities:
                merged_entities.append(
                    entity.copy()
                )
                continue

            previous = merged_entities[-1]

            same_label = (
                previous["label"]
                == entity["label"]
            )

            adjacent = (
                previous["end"] is not None
                and entity["start"] is not None
                and entity["start"]
                <= previous["end"] + 1
            )

            if same_label and adjacent:

                previous["text"] = (
                    text[
                        previous["start"] : entity["end"]
                    ]
                )

                previous["end"] = entity["end"]

                previous["score"] = max(
                    previous["score"],
                    entity["score"],
                )

            else:
                merged_entities.append(
                    entity.copy()
                )

        results = []

        for entity in merged_entities:

            entity_text = (
                entity["text"].strip()
            )

            if not entity_text:
                continue

            results.append(
                {
                    "text": entity_text,
                    "label": entity["label"],
                    "score": round(
                        entity["score"],
                        4,
                    ),
                }
            )

        return results

    def extract_entities_by_type(
        self,
        text: str,
    ) -> dict[str, list[str]]:
        """
        Extract entities and group them by entity type.

        Example:

        {
            "ORG": ["MIT", "Cisco"],
            "LOC": ["Boston"]
        }
        """

        entities = self.extract_entities(
            text
        )

        grouped = defaultdict(list)

        for entity in entities:

            entity_text = (
                entity["text"].strip()
            )

            entity_label = entity["label"]

            if not entity_text:
                continue

            if entity_text not in grouped[
                entity_label
            ]:
                grouped[
                    entity_label
                ].append(
                    entity_text
                )

        return dict(grouped)