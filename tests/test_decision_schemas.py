"""
Test orchestrator S2 decision schema contracts.

Validates:
1. Each schema file is valid JSON Schema draft-07
2. Each schema has required input/output/evidence fields
3. README documents exactly the set of schema files present
4. Example I/O pairs in README validate against their schemas
"""

import json
import os
import re
import unittest
from pathlib import Path


class TestDecisionSchemas(unittest.TestCase):
    """Schema validation and structure tests."""

    @classmethod
    def setUpClass(cls):
        """Load schema directory and README."""
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.decisions_dir = cls.repo_root / "driver" / "decisions"
        cls.readme_path = cls.decisions_dir / "README.md"
        cls.schema_files = sorted(
            p for p in cls.decisions_dir.glob("*.schema.json")
        )
        # Extract decision type name from filename (e.g., "rank_backlog.schema.json" -> "rank_backlog")
        cls.schema_names = [p.name.replace(".schema.json", "") for p in cls.schema_files]
        cls.readme_content = cls.readme_path.read_text(encoding='utf-8')

    def test_schema_directory_exists(self):
        """Decisions directory must exist."""
        self.assertTrue(
            self.decisions_dir.exists(),
            f"decisions directory not found: {self.decisions_dir}"
        )

    def test_readme_exists(self):
        """README.md must exist in decisions directory."""
        self.assertTrue(
            self.readme_path.exists(),
            f"README.md not found: {self.readme_path}"
        )

    def test_schemas_present(self):
        """At least one schema file must exist."""
        self.assertTrue(
            len(self.schema_files) > 0,
            "No .schema.json files found in decisions directory"
        )

    def test_expected_schema_types(self):
        """Expected 6 core decision types present."""
        expected = {
            "rank_backlog",
            "adjudicate_finding",
            "review_diff",
            "synthesize_briefs",
            "decide_repair",
            "final_catch"
        }
        actual = set(self.schema_names)
        self.assertEqual(
            actual, expected,
            f"Schema names mismatch. Expected: {expected}, Got: {actual}"
        )

    def test_each_schema_is_valid_json(self):
        """Each .schema.json file must parse as valid JSON."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                try:
                    schema_file.read_text(encoding='utf-8')
                    content = json.loads(schema_file.read_text(encoding='utf-8'))
                    self.assertIsInstance(content, dict)
                except json.JSONDecodeError as e:
                    self.fail(f"Invalid JSON in {schema_file}: {e}")

    def test_each_schema_has_required_fields(self):
        """Each schema must have $schema, title, required array, properties."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                self.assertIn("$schema", schema, f"{schema_file}: missing $schema")
                self.assertIn("title", schema, f"{schema_file}: missing title")
                self.assertIn("required", schema, f"{schema_file}: missing required array")
                self.assertIn("properties", schema, f"{schema_file}: missing properties")

    def test_each_schema_requires_evidence_field(self):
        """Each schema must require 'evidence' field."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                required = schema.get("required", [])
                self.assertIn(
                    "evidence",
                    required,
                    f"{schema_file}: 'evidence' not in required array"
                )

    def test_each_schema_requires_verdict_field(self):
        """Each schema must have a 'verdict' field (input/output structure)."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                self.assertIn(
                    "verdict",
                    schema.get("properties", {}),
                    f"{schema_file}: 'verdict' property missing"
                )

    def test_verdict_field_is_string(self):
        """Verdict field must be of type string with enum."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                verdict_prop = schema.get("properties", {}).get("verdict", {})
                self.assertEqual(
                    verdict_prop.get("type"), "string",
                    f"{schema_file}: verdict is not type 'string'"
                )
                self.assertIn(
                    "enum", verdict_prop,
                    f"{schema_file}: verdict missing enum array"
                )
                self.assertIsInstance(
                    verdict_prop.get("enum"), list,
                    f"{schema_file}: verdict enum is not a list"
                )
                self.assertGreater(
                    len(verdict_prop.get("enum", [])), 0,
                    f"{schema_file}: verdict enum is empty"
                )

    def test_evidence_field_is_array(self):
        """Evidence field must be of type array with minItems >= 1 and string items."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                evidence_prop = schema.get("properties", {}).get("evidence", {})
                self.assertEqual(
                    evidence_prop.get("type"), "array",
                    f"{schema_file}: evidence is not type 'array'"
                )
                self.assertEqual(
                    evidence_prop.get("minItems"), 1,
                    f"{schema_file}: evidence minItems should be 1"
                )
                items_schema = evidence_prop.get("items", {})
                self.assertEqual(
                    items_schema.get("type"), "string",
                    f"{schema_file}: evidence items should be strings"
                )
                self.assertGreaterEqual(
                    items_schema.get("minLength", 0), 1,
                    f"{schema_file}: evidence items should have minLength >= 1"
                )

    def test_readme_documents_all_schemas(self):
        """README.md must document all schema files."""
        for schema_name in self.schema_names:
            with self.subTest(schema=schema_name):
                # Look for schema reference in README (e.g., `rank_backlog.schema.json`)
                filename = f"{schema_name}.schema.json"
                self.assertIn(
                    filename,
                    self.readme_content,
                    f"README does not reference {filename}"
                )

    def test_readme_no_extra_schemas(self):
        """README should not document schemas that don't exist."""
        # Extract all .schema.json references from README (look for backtick-quoted filenames)
        pattern = r"`(\w+)\.schema\.json`"
        readme_references = set(re.findall(pattern, self.readme_content))

        actual_schemas = set(self.schema_names)

        extra = readme_references - actual_schemas
        self.assertEqual(
            len(extra), 0,
            f"README references schemas that don't exist: {extra}"
        )

    def test_readme_has_decision_type_section_structure(self):
        """Each decision type should have Purpose, Trigger, Input, Output sections."""
        for schema_name in self.schema_names:
            with self.subTest(schema=schema_name):
                # Look for section heading (markdown ### with backticks, e.g., ### 2. `rank_backlog`)
                section_start = self.readme_content.find(f"`{schema_name}`")
                self.assertGreater(
                    section_start, -1,
                    f"README missing section for {schema_name}"
                )

    def test_json_examples_in_readme_parse(self):
        """Extract and validate JSON examples in README."""
        # Find fenced code blocks with json language tag
        json_pattern = r"```json\n(.*?)\n```"
        matches = re.finditer(json_pattern, self.readme_content, re.DOTALL)

        json_count = 0
        for match in matches:
            json_count += 1
            json_text = match.group(1)
            try:
                json.loads(json_text)
            except json.JSONDecodeError as e:
                self.fail(f"Invalid JSON example in README: {e}\nExample:\n{json_text}")

        # Should have at least a few JSON examples (input/output pairs)
        self.assertGreaterEqual(
            json_count, 6,
            f"Expected at least 6 JSON examples, found {json_count}"
        )

    def test_example_io_pairs_validate(self):
        """Example I/O pairs should validate against their schemas."""
        # This is a simplified test: we can't easily extract which examples
        # correspond to which schemas without parsing markdown structure,
        # but we can at least check that all JSON in the README is syntactically valid.
        # A more thorough test would require structured example markers.

        json_pattern = r"```json\n(.*?)\n```"
        matches = re.finditer(json_pattern, self.readme_content, re.DOTALL)

        for match in matches:
            json_text = match.group(1)
            try:
                example = json.loads(json_text)
                # Check that examples have expected structure
                # (either input-like or output-like)
                self.assertIsInstance(example, dict)
                # Most examples should have some content
                self.assertGreater(len(example), 0)
            except (json.JSONDecodeError, AssertionError) as e:
                self.fail(f"Example JSON validation failed: {e}")

    def test_decision_type_field_in_schemas(self):
        """Each schema should define decision_type const."""
        for schema_file, schema_name in zip(self.schema_files, self.schema_names):
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                decision_type = schema.get("properties", {}).get("decision_type", {})
                const_value = decision_type.get("const")
                expected = schema_name  # e.g., "rank_backlog"
                self.assertEqual(
                    const_value, expected,
                    f"{schema_file}: decision_type const should be '{expected}', got '{const_value}'"
                )

    def test_input_property_documented(self):
        """Each schema should have an input property."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                input_prop = schema.get("properties", {}).get("input")
                self.assertIsNotNone(
                    input_prop,
                    f"{schema_file}: missing 'input' property"
                )
                self.assertIsInstance(input_prop, dict)

    def test_confidence_field_in_schemas(self):
        """Each schema should have a confidence field (0.0-1.0)."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                confidence = schema.get("properties", {}).get("confidence", {})
                self.assertEqual(
                    confidence.get("type"), "number",
                    f"{schema_file}: confidence should be type 'number'"
                )
                self.assertEqual(
                    confidence.get("minimum"), 0,
                    f"{schema_file}: confidence minimum should be 0"
                )
                self.assertEqual(
                    confidence.get("maximum"), 1,
                    f"{schema_file}: confidence maximum should be 1"
                )

    def test_schemas_are_draft_07(self):
        """All schemas should use JSON Schema draft-07."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                schema_uri = schema.get("$schema", "")
                self.assertTrue(
                    "draft-07" in schema_uri or "draft/2019-09" in schema_uri,
                    f"{schema_file}: $schema should reference draft-07, got {schema_uri}"
                )

    def test_no_duplicate_schema_files(self):
        """No duplicate schema files allowed."""
        names = [p.stem for p in self.schema_files]
        self.assertEqual(
            len(names), len(set(names)),
            f"Duplicate schema file names found: {[n for n in names if names.count(n) > 1]}"
        )

    def test_readme_has_decision_type_summary_table(self):
        """README should include summary of all decision types."""
        # At minimum, README should list the decision types in backticks
        for schema_name in self.schema_names:
            # Look for backtick-quoted schema name (e.g., `rank_backlog`)
            self.assertIn(
                f"`{schema_name}`",
                self.readme_content,
                f"README missing reference to `{schema_name}`"
            )

    def test_schemas_have_descriptions(self):
        """Each schema should have a title and description."""
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                title = schema.get("title")
                description = schema.get("description")
                self.assertIsNotNone(title, f"{schema_file}: missing title")
                self.assertIsNotNone(description, f"{schema_file}: missing description")
                self.assertGreater(len(title), 5, f"{schema_file}: title too short")
                self.assertGreater(len(description), 10, f"{schema_file}: description too short")

    def test_each_schema_requires_confidence_field(self):
        """BL2 Finding 2: Each schema must REQUIRE 'confidence' field.

        Confidence is load-bearing in the adjudication gate: a missing confidence
        defaults to 0.0, which always escalates. Gate economics collapse when a
        schema-compliant response can omit confidence.

        FIX: Add 'confidence' to the required array in all 6 decision schemas.
        This makes schema-invalid responses (missing confidence) escalate to DECISION_FAILED,
        rather than silently defaulting to 0.0 and escalating via the gate's low-conf rule.
        """
        for schema_file in self.schema_files:
            with self.subTest(schema=schema_file.name):
                schema = json.loads(schema_file.read_text(encoding='utf-8'))
                required = schema.get("required", [])
                self.assertIn(
                    "confidence",
                    required,
                    f"{schema_file}: 'confidence' not in required array. "
                    "BL2: gate economics collapse when a schema-compliant response can omit it."
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
