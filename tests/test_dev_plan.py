from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".agentic-sdlc" / "cao" / "workflows" / "dev_plan.py"

stub = types.ModuleType("cao_workflow")
stub.emit_output = lambda value: None
stub.get_inputs = lambda: {}
stub.step = lambda *args, **kwargs: None
sys.modules.setdefault("cao_workflow", stub)

spec = importlib.util.spec_from_file_location("dev_plan", WORKFLOW)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def valid_context() -> dict:
    jira_ref = [{"source_id": "JIRA:T-1", "location": "AC-1"}]
    return {
        "schema_version": "1.0",
        "ticket": {"id": "T-1", "summary": "Demo"},
        "sources": [
            {"source_id": "JIRA:T-1", "type": "JIRA", "title": "Demo", "status": "RETRIEVED", "content_digest": None}
        ],
        "problem_statement": {"text": "Do a thing", "sources": jira_ref},
        "scope": {"in": [{"text": "Thing", "sources": jira_ref}], "out": [], "uncertain": []},
        "acceptance_criteria": [
            {"id": "AC-1", "text": "Thing works", "origin": "EXPLICIT", "sources": jira_ref}
        ],
        "functional_requirements": [],
        "non_functional_requirements": [],
        "constraints": [],
        "dependencies": [],
        "open_questions": [],
        "contradictions": [],
        "retrieval_warnings": [],
    }


class WorkflowHelpersTest(unittest.TestCase):
    def test_valid_context_is_ready(self):
        self.assertEqual(mod.validate_planning_context(valid_context(), "T-1"), [])

    def test_blocking_question_stops_readiness(self):
        context = valid_context()
        context["open_questions"].append({
            "id": "Q-1",
            "question": "Which behaviour?",
            "reason": "Not specified",
            "blocking": True,
            "sources": [{"source_id": "JIRA:T-1", "location": "description"}],
        })
        blockers = mod.validate_planning_context(context, "T-1")
        self.assertIn("blocking open question Q-1", blockers)

    def test_unknown_provenance_is_rejected(self):
        context = valid_context()
        context["acceptance_criteria"][0]["sources"][0]["source_id"] = "CONF:UNKNOWN"
        with self.assertRaises(mod.WorkflowContractError):
            mod.validate_planning_context(context, "T-1")

    def test_review_status_must_match_findings(self):
        review = {
            "review_status": "PASS",
            "summary": "looks fine",
            "findings": [{
                "id": "PLAN-1",
                "impact": "MEDIUM",
                "category": "TESTING",
                "disposition": "PLAN_CHANGE_REQUIRED",
                "plan_section": "Verification",
                "description": "Missing test",
                "evidence": ["tests/example.py"],
                "required_action": "Add verification",
                "confidence": 0.9,
            }],
        }
        with self.assertRaises(mod.WorkflowContractError):
            mod.validate_review(review)

    def test_fixture_retrieval_marks_missing_required_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "input"
            raw = base / "raw"
            source.mkdir()
            (source / "jira.md").write_text("ticket", encoding="utf-8")
            (source / "context.json").write_text(json.dumps({
                "schema_version": "1.0",
                "ticket": {
                    "id": "T-1", "source_id": "JIRA:T-1", "title": "T", "file": "jira.md", "required": True
                },
                "confluence": [{
                    "source_id": "CONF:1", "title": "Missing", "file": "missing.md", "required": True
                }],
            }), encoding="utf-8")
            retrieval = mod.retrieve_fixture_sources(source, raw, "T-1")
            self.assertEqual(mod.retrieval_blockers(retrieval), ["required source unavailable: CONF:1"])

    def test_json_fence_tolerance(self):
        self.assertEqual(mod._parse_json_output('```json\n{"a": 1}\n```', "x"), {"a": 1})


if __name__ == "__main__":
    unittest.main()
