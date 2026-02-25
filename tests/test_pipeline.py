"""Tests for the multi-model pipeline engine."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from nadirclaw.pipeline import (
    PipelineResult,
    StepResult,
    cache_pipeline_result,
    execute_pipeline,
    get_latest_pipeline,
    get_pipeline_by_id,
    pipeline_result_to_dict,
    _get_pipeline_configs,
    BUILDER_TEMPLATE,
    JUDGE_TEMPLATE,
    COMPRESSOR_TEMPLATE,
)


class TestPipelineDataClasses:
    def test_step_result_defaults(self):
        step = StepResult(role="builder", model="test/model", content="hello", status="ok")
        assert step.role == "builder"
        assert step.prompt_tokens == 0
        assert step.completion_tokens == 0
        assert step.latency_ms == 0
        assert step.error is None

    def test_pipeline_result_defaults(self):
        result = PipelineResult(pipeline_id="test-123", intent="code_generation", status="ok")
        assert result.pipeline_id == "test-123"
        assert result.steps == []
        assert result.final_content == ""
        assert result.total_latency_ms == 0

    def test_pipeline_result_to_dict(self):
        result = PipelineResult(
            pipeline_id="test-123",
            intent="code_generation",
            status="ok",
            total_latency_ms=1500,
            steps=[
                StepResult(
                    role="builder",
                    model="ollama/qwen3:8b",
                    content="Built",
                    status="ok",
                    prompt_tokens=100,
                    completion_tokens=200,
                    latency_ms=1000,
                ),
                StepResult(
                    role="judge",
                    model="ollama/deepseek-r1:8b",
                    content="Reviewed",
                    status="ok",
                    prompt_tokens=150,
                    completion_tokens=100,
                    latency_ms=500,
                ),
            ],
        )
        d = pipeline_result_to_dict(result)
        assert d["pipeline_id"] == "test-123"
        assert d["intent"] == "code_generation"
        assert d["status"] == "ok"
        assert len(d["steps"]) == 2
        assert d["steps"][0]["role"] == "builder"
        assert d["steps"][1]["role"] == "judge"


class TestPipelineConfigs:
    def test_all_intents_have_configs(self):
        configs = _get_pipeline_configs()
        expected = [
            "code_generation", "code_review", "architecture",
            "debugging", "security_analysis", "documentation", "general_qa",
        ]
        for intent in expected:
            assert intent in configs, f"Missing config for {intent}"

    def test_configs_have_builder_and_judge(self):
        configs = _get_pipeline_configs()
        for intent, config in configs.items():
            assert "builder" in config, f"{intent} missing builder"
            assert "judge" in config, f"{intent} missing judge"
            # compressor can be None (skipped for some intents)
            assert "compressor" in config, f"{intent} missing compressor key"


class TestPipelineCache:
    def test_cache_and_retrieve_latest(self):
        result = PipelineResult(pipeline_id="cache-test", intent="debugging", status="ok")
        cache_pipeline_result(result)
        latest = get_latest_pipeline()
        assert latest is not None
        assert latest.pipeline_id == "cache-test"

    def test_get_by_id(self):
        result = PipelineResult(pipeline_id="lookup-test", intent="architecture", status="ok")
        cache_pipeline_result(result)
        found = get_pipeline_by_id("lookup-test")
        assert found is not None
        assert found.pipeline_id == "lookup-test"

    def test_get_by_id_not_found(self):
        assert get_pipeline_by_id("nonexistent-id-12345") is None


class TestPromptTemplates:
    def test_builder_template_has_placeholder(self):
        assert "{user_prompt}" in BUILDER_TEMPLATE

    def test_judge_template_has_placeholders(self):
        assert "{user_prompt}" in JUDGE_TEMPLATE
        assert "{builder_output}" in JUDGE_TEMPLATE

    def test_compressor_template_has_placeholders(self):
        assert "{user_prompt}" in COMPRESSOR_TEMPLATE
        assert "{builder_output}" in COMPRESSOR_TEMPLATE
        assert "{judge_output}" in COMPRESSOR_TEMPLATE

    def test_compressor_template_warns_about_secrets(self):
        """Compressor template should instruct to not include secrets."""
        assert "API keys" in COMPRESSOR_TEMPLATE or "tokens" in COMPRESSOR_TEMPLATE
        assert "passwords" in COMPRESSOR_TEMPLATE or "PII" in COMPRESSOR_TEMPLATE


@pytest.mark.asyncio
class TestPipelineExecution:
    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_execute_pipeline_success_sequential(self, mock_dispatch):
        """Test successful sequential pipeline execution (debugging intent)."""
        mock_dispatch.return_value = {
            "content": "Generated content",
            "finish_reason": "stop",
            "prompt_tokens": 100,
            "completion_tokens": 200,
        }

        result = await execute_pipeline(
            intent="debugging",
            messages=[{"role": "user", "content": "Fix this null pointer"}],
        )

        assert result.status in ("ok", "partial")
        assert len(result.steps) >= 2  # builder + judge
        assert result.steps[0].role == "builder"
        assert result.steps[1].role == "judge"
        assert result.final_content != ""

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_execute_pipeline_success_concurrent(self, mock_dispatch):
        """Test successful concurrent pipeline execution (code_generation intent)."""
        mock_dispatch.return_value = {
            "content": "Generated content",
            "finish_reason": "stop",
            "prompt_tokens": 100,
            "completion_tokens": 200,
        }

        result = await execute_pipeline(
            intent="code_generation",
            messages=[{"role": "user", "content": "Write a hello world function"}],
        )

        assert result.status in ("ok", "partial")
        step_roles = [s.role for s in result.steps]
        # Concurrent: 2 lanes + synthesizer + judge
        assert "builder:impl" in step_roles
        assert "builder:tests" in step_roles
        assert "synthesizer" in step_roles
        assert "judge" in step_roles
        assert result.final_content != ""

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_execute_pipeline_builder_failure(self, mock_dispatch):
        """If builder fails, pipeline should return error status."""
        mock_dispatch.side_effect = Exception("Model unavailable")

        result = await execute_pipeline(
            intent="debugging",
            messages=[{"role": "user", "content": "Debug this error"}],
        )

        assert result.status == "error"
        assert result.steps[0].status == "error"

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_execute_pipeline_judge_failure_returns_partial(self, mock_dispatch):
        """If judge fails, pipeline should return builder output with partial status."""
        call_count = 0

        async def mock_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # BLAST may call dispatch_raw first; builder is the next-to-last
            # call, judge is the last.  Succeed for everything except the
            # judge (last step after builder).
            if call_count <= 2:
                return {
                    "content": "Builder output",
                    "finish_reason": "stop",
                    "prompt_tokens": 50,
                    "completion_tokens": 100,
                }
            raise Exception("Judge model failed")

        mock_dispatch.side_effect = mock_fn

        result = await execute_pipeline(
            intent="code_generation",
            messages=[{"role": "user", "content": "Write something"}],
        )

        assert result.status == "partial"
        assert "Builder output" in result.final_content
