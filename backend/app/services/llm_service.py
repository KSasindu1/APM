
import json

from groq import Groq

from app.core.config import settings


class LLMService:
    """
    Interface to the external Groq-hosted LLM.
    """

    def __init__(self):
        self.client = Groq(
            api_key=settings.groq_api_key,
        )

    def analyse_risks(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:

        response = self.client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "risk_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string"
                            },
                            "risks": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "risk_id": {
                                            "type": "string"
                                        },
                                        "task_id": {
                                            "type": [
                                                "string",
                                                "null"
                                            ]
                                        },
                                        "risk": {
                                            "type": "string"
                                        },
                                        "description": {
                                            "type": "string"
                                        },
                                        "risk_level": {
                                            "type": "string"
                                        },
                                        "impact_days": {
                                            "type": [
                                                "integer",
                                                "null"
                                            ],
                                            "minimum": 0
                                        },
                                        "confidence": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 1
                                        },
                                        "evidence": {
                                            "type": "string"
                                        },
                                        "source": {
                                            "type": "string"
                                        },
                                        "affected_area": {
                                            "type": "string"
                                        },
                                        "recommended_action": {
                                            "type": "string"
                                        },
                                    },
                                    "required": [
                                        "risk_id",
                                        "task_id",
                                        "risk",
                                        "description",
                                        "risk_level",
                                        "impact_days",
                                        "confidence",
                                        "evidence",
                                        "source",
                                        "affected_area",
                                        "recommended_action",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                            "summary": {
                                "type": "string"
                            },
                            "evidence_used": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                            },
                        },
                        "required": [
                            "project_id",
                            "risks",
                            "summary",
                            "evidence_used",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
        )

        content = response.choices[0].message.content

        if not content:
            raise RuntimeError(
                "The LLM returned an empty response."
            )

        return json.loads(content)
