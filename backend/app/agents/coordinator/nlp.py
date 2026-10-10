"""Local rule-based extraction. Missing information is left for user review."""
import re
from datetime import date
from pydantic import BaseModel, Field, field_validator


class NaturalLanguageRequest(BaseModel):
    text: str = Field(min_length=10, max_length=5000)

    @field_validator("text")
    @classmethod
    def clean(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("Describe your project in at least 10 characters.")
        return value.strip()


class NaturalLanguageResult(BaseModel):
    method: str = "rule_based"
    draft: dict
    missing_fields: list[str]
    notes: list[str]


def extract_project(text: str) -> NaturalLanguageResult:
    draft = {"objective": text[:3000], "message": text[:1500], "resources": []}
    notes = ["Review all extracted details. Only explicit YYYY-MM-DD dates are recognized; relative dates need manual entry."]
    name = re.search(r'(?:project(?:\s+name)?\s*[:=]|called\s+|named\s+)([^\n.;]+)', text, re.I)
    if name:
        draft["project_name"] = re.split(r'\s+(?:starting|starts|with|from|by)\b', name.group(1), flags=re.I)[0].strip()[:200]
    for key, label in [("start_date", r"(?:start(?:ing|s)?(?:\s+date)?|from)"), ("deadline", r"(?:deadline|due(?:\s+date)?|by)")]:
        match = re.search(label + r'\s*(?:[:=]|on|is)?\s*(\d{4}-\d{2}-\d{2})\b', text, re.I)
        if match:
            try:
                draft[key] = date.fromisoformat(match.group(1)).isoformat()
            except ValueError:
                notes.append(f"Invalid {key.replace('_', ' ')}; please correct it.")
    for index, match in enumerate(re.finditer(r'\b(\d{1,3})\s+(developers?|designers?|testers?|engineers?|analysts?)\b', text, re.I), 1):
        if 1 <= int(match.group(1)) <= 100:
            draft["resources"].append({"resource_id": f"R{index}", "name": match.group(2).capitalize(), "capacity": int(match.group(1))})
    if len(text) > 1500:
        notes.append("Planning instructions were shortened to 1,500 characters. Review them to retain important requirements.")
    return NaturalLanguageResult(draft=draft, missing_fields=[key for key in ["project_name", "objective", "start_date", "deadline"] if not draft.get(key)], notes=notes)
