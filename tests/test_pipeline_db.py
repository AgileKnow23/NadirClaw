"""Tests for pipeline DB operations and SOC2 redaction gate."""

import pytest
from nadirclaw.pipeline_db import (
    PIPELINE_SCHEMA_DDL,
    redact_secrets,
)


class TestSOC2Redaction:
    """Test the SOC2 redaction gate strips secrets before storage."""

    def test_redact_api_key_sk_prefix(self):
        text = "Use this key: sk-abc123def456ghi789jkl012mno345"
        result = redact_secrets(text)
        assert "sk-abc123" not in result
        assert "[REDACTED]" in result

    def test_redact_anthropic_token(self):
        text = "Token: sk-ant-api03-abcdef1234567890abcdef1234567890"
        result = redact_secrets(text)
        assert "sk-ant-" not in result
        assert "[REDACTED]" in result

    def test_redact_jwt_token(self):
        text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = redact_secrets(text)
        assert "eyJhbGci" not in result

    def test_redact_email(self):
        text = "Contact user@example.com for details"
        result = redact_secrets(text)
        assert "user@example.com" not in result
        assert "[REDACTED]" in result

    def test_redact_phone_number(self):
        text = "Call 555-123-4567 for support"
        result = redact_secrets(text)
        assert "555-123-4567" not in result

    def test_redact_password_assignment(self):
        text = "password: mysecretpassword123"
        result = redact_secrets(text)
        assert "mysecretpassword123" not in result

    def test_preserves_normal_text(self):
        text = "The architecture uses a microservices pattern with event-driven communication"
        result = redact_secrets(text)
        assert result == text

    def test_redact_supabase_token(self):
        text = "Key: sbp_abcdefghij1234567890"
        result = redact_secrets(text)
        assert "sbp_" not in result

    def test_redact_multiple_secrets(self):
        text = "Key: sk-abc123def456789012345678 and email: admin@corp.io"
        result = redact_secrets(text)
        assert "sk-abc" not in result
        assert "admin@corp" not in result
        assert result.count("[REDACTED]") >= 2


class TestPipelineSchemaDefinition:
    """Test that the schema DDL is well-formed."""

    def test_schema_defines_pipeline_run_table(self):
        assert "DEFINE TABLE IF NOT EXISTS pipeline_run" in PIPELINE_SCHEMA_DDL

    def test_schema_defines_decision_table(self):
        assert "DEFINE TABLE IF NOT EXISTS decision" in PIPELINE_SCHEMA_DDL

    def test_schema_defines_repo_context_table(self):
        assert "DEFINE TABLE IF NOT EXISTS repo_context" in PIPELINE_SCHEMA_DDL

    def test_schema_has_full_text_search_on_decisions(self):
        assert "idx_decision_ft" in PIPELINE_SCHEMA_DDL
        assert "BM25" in PIPELINE_SCHEMA_DDL

    def test_schema_has_pipeline_id_unique_index(self):
        assert "idx_pipeline_id" in PIPELINE_SCHEMA_DDL
        assert "UNIQUE" in PIPELINE_SCHEMA_DDL
