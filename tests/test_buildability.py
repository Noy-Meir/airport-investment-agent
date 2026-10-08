import json
import os

BUILDABILITY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "reference", "buildability.json",
)

REQUIRED_FIELDS = ("type", "value", "unit", "source_url", "verified_by_user")


def _load():
    with open(BUILDABILITY_PATH) as f:
        return json.load(f)


def test_every_constraint_has_required_fields():
    data = _load()
    for code, airport in data["airports"].items():
        for i, constraint in enumerate(airport["constraints"]):
            for field in REQUIRED_FIELDS:
                assert field in constraint, f"{code} constraint[{i}] missing {field!r}"
            assert constraint["source_url"], f"{code} constraint[{i}] has an empty source_url"
