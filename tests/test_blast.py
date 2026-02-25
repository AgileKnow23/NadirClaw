"""Tests for the BLAST prompt optimizer."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from nadirclaw.blast import (
    BLASTOptimizer,
    BlastResult,
    build_execution_plan,
    get_blast_optimizer,
    get_concurrent_phases,
    _INTENT_CONTEXT,
    _BASE_INSTRUCTIONS,
    _ROLE_DESCRIPTIONS,
    _CONCURRENT_PHASES,
)


class TestBlastResult:
    def test_defaults(self):
        result = BlastResult(
            original_prompt="test",
            enhanced_prompt="test enhanced",
            intent="code_generation",
        )
        assert result.original_prompt == "test"
        assert result.enhanced_prompt == "test enhanced"
        assert result.intent == "code_generation"
        assert result.sections == {}
        assert result.latency_ms == 0
        assert result.used_llm is False

    def test_with_sections(self):
        sections = {
            "blueprint": "Build a REST API",
            "link": "FastAPI, PostgreSQL",
            "architect": "3-layer architecture",
            "style": "PEP 8, type hints",
            "trigger": "Implement, test, deploy",
        }
        result = BlastResult(
            original_prompt="test",
            enhanced_prompt="enhanced",
            intent="code_generation",
            sections=sections,
            latency_ms=150,
            used_llm=True,
        )
        assert result.sections["blueprint"] == "Build a REST API"
        assert result.latency_ms == 150
        assert result.used_llm is True


class TestIntentTemplates:
    def test_all_seven_intents_have_context(self):
        expected = [
            "code_generation", "code_review", "architecture",
            "debugging", "security_analysis", "documentation", "general_qa",
        ]
        for intent in expected:
            assert intent in _INTENT_CONTEXT, f"Missing BLAST context for {intent}"

    def test_context_mentions_blast_sections(self):
        for intent, context in _INTENT_CONTEXT.items():
            assert "Blueprint" in context, f"{intent} context missing Blueprint"
            assert "Link" in context, f"{intent} context missing Link"
            assert "Architect" in context, f"{intent} context missing Architect"
            assert "Style" in context, f"{intent} context missing Style"
            assert "Trigger" in context, f"{intent} context missing Trigger"

    def test_base_instructions_has_sections(self):
        assert "## Blueprint" in _BASE_INSTRUCTIONS
        assert "## Link" in _BASE_INSTRUCTIONS
        assert "## Architect" in _BASE_INSTRUCTIONS
        assert "## Style" in _BASE_INSTRUCTIONS
        assert "## Trigger" in _BASE_INSTRUCTIONS


class TestBLASTOptimizer:
    def setup_method(self):
        self.optimizer = BLASTOptimizer()

    def test_parse_sections_complete(self):
        text = (
            "## Blueprint\nBuild user auth system\n"
            "## Link\nFastAPI, JWT, PostgreSQL\n"
            "## Architect\n1. Create models 2. Add routes\n"
            "## Style\nRESTful, PEP 8\n"
            "## Trigger\nImplement, write tests, verify"
        )
        sections = self.optimizer._parse_sections(text)
        assert sections["blueprint"] == "Build user auth system"
        assert "FastAPI" in sections["link"]
        assert sections["architect"].startswith("1.")
        assert "RESTful" in sections["style"]
        assert "Implement" in sections["trigger"]

    def test_parse_sections_missing_fills_na(self):
        text = "## Blueprint\nSome task\n## Trigger\nDo it"
        sections = self.optimizer._parse_sections(text)
        assert sections["blueprint"] == "Some task"
        assert sections["trigger"] == "Do it"
        assert sections["link"] == "N/A"
        assert sections["architect"] == "N/A"
        assert sections["style"] == "N/A"

    def test_parse_sections_empty(self):
        sections = self.optimizer._parse_sections("")
        for key in ("blueprint", "link", "architect", "style", "trigger"):
            assert sections[key] == "N/A"

    def test_template_fallback(self):
        enhanced, sections = self.optimizer._template_fallback(
            "Write a REST API", "code_generation"
        )
        assert "Write a REST API" in enhanced
        assert "BLAST Analysis" in enhanced
        assert "code_generation" in sections["blueprint"]

    def test_format_enhanced(self):
        sections = {
            "blueprint": "Task desc",
            "link": "Deps",
            "architect": "Plan",
            "style": "Clean",
            "trigger": "Ship",
        }
        result = BLASTOptimizer._format_enhanced("Original prompt", sections)
        assert result.startswith("Original prompt")
        assert "BLAST Analysis" in result
        assert "**Blueprint:** Task desc" in result
        assert "**Trigger:** Ship" in result


@pytest.mark.asyncio
class TestBLASTOptimizerLLM:
    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_optimize_with_llm(self, mock_dispatch):
        """Test BLAST optimization using a mocked local LLM call."""
        mock_dispatch.return_value = {
            "content": (
                "## Blueprint\nBuild a secure REST API for user authentication\n"
                "## Link\nFastAPI, bcrypt, JWT, PostgreSQL\n"
                "## Architect\n1. Define user model 2. Create auth endpoints 3. Add middleware\n"
                "## Style\nRESTful conventions, type hints, 90%+ test coverage\n"
                "## Trigger\nImplement auth flow, write integration tests, verify with curl"
            ),
            "prompt_tokens": 100,
            "completion_tokens": 80,
        }

        optimizer = BLASTOptimizer()
        result = await optimizer.optimize("Write a REST API for user auth", "code_generation")

        assert result.used_llm is True
        assert result.intent == "code_generation"
        assert "Write a REST API for user auth" in result.enhanced_prompt
        assert "BLAST Analysis" in result.enhanced_prompt
        assert result.sections["blueprint"] == "Build a secure REST API for user authentication"
        assert "FastAPI" in result.sections["link"]

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_optimize_falls_back_on_llm_error(self, mock_dispatch):
        """If LLM fails, should fall back to template."""
        mock_dispatch.side_effect = Exception("Model unavailable")

        optimizer = BLASTOptimizer()
        result = await optimizer.optimize("Debug this error", "debugging")

        assert result.used_llm is False
        assert result.intent == "debugging"
        assert "Debug this error" in result.enhanced_prompt
        assert result.sections["blueprint"] != "N/A"

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_optimize_all_intents(self, mock_dispatch):
        """Ensure all 7 intents can be optimized without error."""
        mock_dispatch.return_value = {
            "content": (
                "## Blueprint\nTask\n## Link\nDeps\n"
                "## Architect\nPlan\n## Style\nClean\n## Trigger\nShip"
            ),
            "prompt_tokens": 50,
            "completion_tokens": 30,
        }

        optimizer = BLASTOptimizer()
        intents = [
            "code_generation", "code_review", "architecture",
            "debugging", "security_analysis", "documentation", "general_qa",
        ]
        for intent in intents:
            result = await optimizer.optimize("Test prompt", intent)
            assert result.intent == intent
            assert result.used_llm is True
            assert "blueprint" in result.sections

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_optimize_unknown_intent_uses_general_qa(self, mock_dispatch):
        """Unknown intents should fall back to general_qa context."""
        mock_dispatch.return_value = {
            "content": "## Blueprint\nTask\n## Link\nN/A\n## Architect\nN/A\n## Style\nN/A\n## Trigger\nDo it",
            "prompt_tokens": 20,
            "completion_tokens": 15,
        }

        optimizer = BLASTOptimizer()
        result = await optimizer.optimize("Random question", "unknown_intent")
        assert result.intent == "unknown_intent"
        assert result.used_llm is True


class TestBlastSettings:
    def test_blast_enabled_default(self):
        from nadirclaw.settings import settings
        # Default should be True
        assert isinstance(settings.BLAST_ENABLED, bool)

    def test_blast_skip_simple_default(self):
        from nadirclaw.settings import settings
        assert isinstance(settings.BLAST_SKIP_SIMPLE, bool)

    def test_blast_model_default(self):
        from nadirclaw.settings import settings
        # Should default to SIMPLE_MODEL
        assert settings.BLAST_MODEL == settings.SIMPLE_MODEL


class TestBlastSingleton:
    def test_get_blast_optimizer_returns_same_instance(self):
        import nadirclaw.blast as blast_mod
        blast_mod._optimizer = None  # Reset
        opt1 = get_blast_optimizer()
        opt2 = get_blast_optimizer()
        assert opt1 is opt2

    def test_get_blast_optimizer_is_blast_optimizer(self):
        import nadirclaw.blast as blast_mod
        blast_mod._optimizer = None
        opt = get_blast_optimizer()
        assert isinstance(opt, BLASTOptimizer)


@pytest.mark.asyncio
class TestBlastPipelineIntegration:
    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_blast_skip_simple_qa(self, mock_dispatch):
        """BLAST should be skipped for simple_qa when BLAST_SKIP_SIMPLE is True."""
        import os
        os.environ["NADIRCLAW_BLAST_SKIP_SIMPLE"] = "true"
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"

        # Mock dispatch for pipeline steps
        mock_dispatch.return_value = {
            "content": "Response content",
            "finish_reason": "stop",
            "prompt_tokens": 50,
            "completion_tokens": 100,
        }

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="simple_qa",
            messages=[{"role": "user", "content": "What is 2+2?"}],
        )

        assert result.blast_applied is False

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_blast_disabled_skips(self, mock_dispatch):
        """BLAST should be skipped entirely when BLAST_ENABLED is False."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "false"

        mock_dispatch.return_value = {
            "content": "Response content",
            "finish_reason": "stop",
            "prompt_tokens": 50,
            "completion_tokens": 100,
        }

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="code_generation",
            messages=[{"role": "user", "content": "Write hello world"}],
        )

        assert result.blast_applied is False

        # Clean up
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"


class TestExecutionPlan:
    def test_build_plan_code_generation_concurrent(self):
        """code_generation uses concurrent phases: blast → parallel lanes → synthesizer → judge → compressor."""
        sections = {
            "blueprint": "Build a REST API for user auth with JWT tokens",
            "link": "FastAPI, bcrypt, JWT",
            "architect": "3-layer design",
            "style": "PEP 8",
            "trigger": "Implement and test",
        }
        config = {
            "builder": "ollama/qwen3-coder:30b",
            "judge": "ollama/deepseek-r1:8b",
            "compressor": "ollama/qwen2.5:3b",
        }
        plan = build_execution_plan(
            intent="code_generation",
            sections=sections,
            pipeline_config=config,
            blast_model="ollama/qwen3:8b",
            used_llm=True,
        )

        assert plan["intent"] == "code_generation"
        assert plan["concurrent"] is True
        assert plan["total_phases"] >= 3  # blast, concurrent build, synthesis, ...
        assert plan["summary"].startswith("Build a REST API")

        # Step 0: BLAST optimizer
        assert plan["steps"][0]["agent"] == "blast_optimizer"
        assert plan["steps"][0]["model"] == "ollama/qwen3:8b"
        assert plan["steps"][0]["used_llm"] is True
        assert plan["steps"][0]["phase"] == 0

        # Steps 1-2: Parallel builder lanes
        agents = [s["agent"] for s in plan["steps"]]
        assert "builder:impl" in agents
        assert "builder:tests" in agents

        # Synthesizer present
        assert "synthesizer" in agents

        # Judge and compressor present
        assert "judge" in agents
        assert "compressor" in agents

        # Total agents: blast + 2 lanes + synthesizer + judge + compressor = 6
        assert plan["total_agents"] == 6

    def test_build_plan_debugging_sequential_no_compressor(self):
        """Debugging uses sequential execution and skips the compressor step."""
        config = {
            "builder": "ollama/deepseek-r1:8b",
            "judge": "ollama/qwen3-coder:30b",
            "compressor": None,
        }
        plan = build_execution_plan(
            intent="debugging",
            sections={"blueprint": "Fix crash on startup"},
            pipeline_config=config,
            blast_model="ollama/qwen3:8b",
            used_llm=False,
        )

        assert plan["concurrent"] is False
        assert plan["total_agents"] == 3  # blast + builder + judge (no compressor)
        agents = [s["agent"] for s in plan["steps"]]
        assert "compressor" not in agents
        assert plan["steps"][0]["used_llm"] is False

    def test_build_plan_all_intents(self):
        """Every intent should produce a valid plan."""
        config = {
            "builder": "model-a",
            "judge": "model-b",
            "compressor": "model-c",
        }
        for intent in _ROLE_DESCRIPTIONS:
            plan = build_execution_plan(
                intent=intent,
                sections={"blueprint": "Test"},
                pipeline_config=config,
                blast_model="model-blast",
                used_llm=True,
            )
            assert plan["intent"] == intent
            assert plan["total_agents"] >= 2  # at least blast + builder
            assert plan["steps"][0]["agent"] == "blast_optimizer"
            assert "concurrent" in plan
            assert "total_phases" in plan
            assert "phases" in plan

    def test_build_plan_summary_truncated(self):
        """Long blueprints should be truncated in the summary."""
        long_blueprint = "x" * 200
        plan = build_execution_plan(
            intent="general_qa",
            sections={"blueprint": long_blueprint},
            pipeline_config={"builder": "m1", "judge": "m2", "compressor": None},
            blast_model="m0",
            used_llm=True,
        )
        assert len(plan["summary"]) <= 153  # 150 + "..."
        assert plan["summary"].endswith("...")

    def test_build_plan_actions_are_descriptive(self):
        """Each step should have a meaningful action description."""
        config = {
            "builder": "model-a",
            "judge": "model-b",
            "compressor": "model-c",
        }
        plan = build_execution_plan(
            intent="code_generation",
            sections={"blueprint": "Build API"},
            pipeline_config=config,
            blast_model="model-blast",
            used_llm=True,
        )
        for step in plan["steps"]:
            assert len(step["action"]) > 10, f"Step {step['agent']} has empty action"

    def test_role_descriptions_cover_all_intents(self):
        """Every intent in _INTENT_CONTEXT should have role descriptions."""
        for intent in _INTENT_CONTEXT:
            assert intent in _ROLE_DESCRIPTIONS, f"Missing role descriptions for {intent}"

    def test_concurrent_intents_have_parallel_phase(self):
        """Concurrent intents should have at least one parallel phase."""
        config = {"builder": "m-a", "judge": "m-b", "compressor": "m-c"}
        for intent in _CONCURRENT_PHASES:
            plan = build_execution_plan(
                intent=intent,
                sections={"blueprint": "Test"},
                pipeline_config=config,
                blast_model="m-blast",
                used_llm=True,
            )
            parallel_phases = [p for p in plan["phases"] if p["parallel"]]
            assert len(parallel_phases) >= 1, f"{intent} has no parallel phases"

    def test_sequential_intents_no_parallel_phases(self):
        """Sequential intents should have no parallel phases."""
        config = {"builder": "m-a", "judge": "m-b", "compressor": None}
        for intent in ("debugging", "documentation", "general_qa"):
            plan = build_execution_plan(
                intent=intent,
                sections={"blueprint": "Test"},
                pipeline_config=config,
                blast_model="m-blast",
                used_llm=True,
            )
            parallel_phases = [p for p in plan["phases"] if p["parallel"]]
            assert len(parallel_phases) == 0, f"{intent} should not have parallel phases"

    def test_get_concurrent_phases(self):
        """get_concurrent_phases returns config for concurrent intents, None otherwise."""
        assert get_concurrent_phases("code_generation") is not None
        assert get_concurrent_phases("architecture") is not None
        assert get_concurrent_phases("security_analysis") is not None
        assert get_concurrent_phases("code_review") is not None
        assert get_concurrent_phases("debugging") is None
        assert get_concurrent_phases("documentation") is None
        assert get_concurrent_phases("general_qa") is None


@pytest.mark.asyncio
class TestExecutionPlanInPipeline:
    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_pipeline_includes_execution_plan(self, mock_dispatch):
        """Pipeline result should include the execution plan."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"

        mock_dispatch.return_value = {
            "content": "## Blueprint\nTask\n## Link\nN/A\n## Architect\nPlan\n## Style\nClean\n## Trigger\nShip",
            "prompt_tokens": 50,
            "completion_tokens": 100,
        }

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="code_generation",
            messages=[{"role": "user", "content": "Write a REST API"}],
        )

        assert result.execution_plan is not None
        assert result.execution_plan["intent"] == "code_generation"
        assert result.execution_plan["total_agents"] >= 3
        assert result.execution_plan["steps"][0]["agent"] == "blast_optimizer"

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_pipeline_plan_even_without_blast(self, mock_dispatch):
        """Execution plan is built even when BLAST is disabled."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "false"

        mock_dispatch.return_value = {
            "content": "Response",
            "prompt_tokens": 50,
            "completion_tokens": 100,
        }

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="debugging",
            messages=[{"role": "user", "content": "Fix this bug"}],
        )

        assert result.execution_plan is not None
        assert result.execution_plan["intent"] == "debugging"
        # Even without BLAST, we still show the plan (blast step shows used_llm=False)
        assert result.execution_plan["steps"][0]["agent"] == "blast_optimizer"

        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"


class TestPlanBriefingInjection:
    """Verify that the execution plan briefing is actually injected into model prompts."""

    def _make_sequential_plan(self):
        """Build a sequential plan (debugging intent) for testing."""
        return {
            "intent": "debugging",
            "summary": "Fix the crash",
            "concurrent": False,
            "total_agents": 3,
            "total_phases": 3,
            "phases": [
                {"phase": 0, "name": "BLAST Analysis", "parallel": False,
                 "steps": [{"step": 0, "agent": "blast_optimizer", "model": "m0",
                            "model_short": "m0", "action": "Decompose prompt", "phase": 0}]},
                {"phase": 1, "name": "Builder", "parallel": False,
                 "steps": [{"step": 1, "agent": "builder", "model": "m1",
                            "model_short": "m1", "action": "Analyze root cause", "phase": 1}]},
                {"phase": 2, "name": "Judge", "parallel": False,
                 "steps": [{"step": 2, "agent": "judge", "model": "m2",
                            "model_short": "m2", "action": "Verify diagnosis", "phase": 2}]},
            ],
            "steps": [
                {"step": 0, "agent": "blast_optimizer", "model": "m0",
                 "model_short": "m0", "action": "Decompose prompt", "phase": 0},
                {"step": 1, "agent": "builder", "model": "m1",
                 "model_short": "m1", "action": "Analyze root cause", "phase": 1},
                {"step": 2, "agent": "judge", "model": "m2",
                 "model_short": "m2", "action": "Verify diagnosis", "phase": 2},
            ],
        }

    def _make_concurrent_plan(self):
        """Build a concurrent plan (code_generation intent) for testing."""
        return {
            "intent": "code_generation",
            "summary": "Build a REST API",
            "concurrent": True,
            "total_agents": 6,
            "total_phases": 5,
            "phases": [
                {"phase": 0, "name": "BLAST Analysis", "parallel": False,
                 "steps": [{"step": 0, "agent": "blast_optimizer", "model": "m0",
                            "model_short": "m0", "action": "Decompose prompt", "phase": 0}]},
                {"phase": 1, "name": "Concurrent Build", "parallel": True,
                 "steps": [
                     {"step": 1, "agent": "builder:impl", "model": "m1",
                      "model_short": "m1", "action": "Write implementation code", "phase": 1},
                     {"step": 2, "agent": "builder:tests", "model": "m2",
                      "model_short": "m2", "action": "Write tests", "phase": 1},
                 ]},
                {"phase": 2, "name": "Synthesis", "parallel": False,
                 "steps": [{"step": 3, "agent": "synthesizer", "model": "m1",
                            "model_short": "m1", "action": "Merge outputs", "phase": 2}]},
                {"phase": 3, "name": "Review", "parallel": False,
                 "steps": [{"step": 4, "agent": "judge", "model": "m2",
                            "model_short": "m2", "action": "Review code", "phase": 3}]},
                {"phase": 4, "name": "Memory", "parallel": False,
                 "steps": [{"step": 5, "agent": "compressor", "model": "m3",
                            "model_short": "m3", "action": "Extract patterns", "phase": 4}]},
            ],
            "steps": [
                {"step": 0, "agent": "blast_optimizer", "model": "m0",
                 "model_short": "m0", "action": "Decompose prompt", "phase": 0},
                {"step": 1, "agent": "builder:impl", "model": "m1",
                 "model_short": "m1", "action": "Write implementation code", "phase": 1},
                {"step": 2, "agent": "builder:tests", "model": "m2",
                 "model_short": "m2", "action": "Write tests", "phase": 1},
                {"step": 3, "agent": "synthesizer", "model": "m1",
                 "model_short": "m1", "action": "Merge outputs", "phase": 2},
                {"step": 4, "agent": "judge", "model": "m2",
                 "model_short": "m2", "action": "Review code", "phase": 3},
                {"step": 5, "agent": "compressor", "model": "m3",
                 "model_short": "m3", "action": "Extract patterns", "phase": 4},
            ],
        }

    def test_builder_prompt_sequential_briefing(self):
        from nadirclaw.pipeline import _build_builder_prompt

        plan = self._make_sequential_plan()
        prompt = _build_builder_prompt("Fix the crash", "debugging", plan)

        assert "PIPELINE BRIEFING" in prompt
        assert "Your role: builder" in prompt
        assert "Analyze root cause" in prompt
        assert "Sequential" in prompt
        assert "← YOU" in prompt
        assert "Fix the crash" in prompt

    def test_builder_prompt_concurrent_briefing(self):
        from nadirclaw.pipeline import _build_builder_prompt

        plan = self._make_concurrent_plan()
        prompt = _build_builder_prompt("Build API", "code_generation", plan)

        assert "PIPELINE BRIEFING" in prompt
        assert "Concurrent" in prompt
        assert "PARALLEL" in prompt
        assert "builder:impl" in prompt
        assert "builder:tests" in prompt
        assert "synthesizer" in prompt

    def test_judge_prompt_contains_briefing(self):
        from nadirclaw.pipeline import _build_judge_prompt

        plan = self._make_sequential_plan()
        prompt = _build_judge_prompt("Fix the crash", "builder response", "debugging", plan)

        assert "PIPELINE BRIEFING" in prompt
        assert "Your role: judge" in prompt
        assert "Verify diagnosis" in prompt
        assert "builder response" in prompt

    def test_compressor_prompt_contains_briefing(self):
        from nadirclaw.pipeline import _build_compressor_prompt

        plan = self._make_concurrent_plan()
        prompt = _build_compressor_prompt("Build API", "code here", "review here", "code_generation", plan)

        assert "PIPELINE BRIEFING" in prompt
        assert "Your role: compressor" in prompt
        assert "Extract" in prompt

    def test_lane_prompt_contains_briefing(self):
        from nadirclaw.pipeline import _build_lane_prompt

        plan = self._make_concurrent_plan()
        prompt = _build_lane_prompt(
            "Build API", "code_generation", "builder:impl", "Write implementation code", plan,
        )

        assert "PIPELINE BRIEFING" in prompt
        assert "Your role: builder:impl" in prompt
        assert "Write implementation code" in prompt
        assert "← YOU" in prompt
        assert "parallel pipeline" in prompt.lower()

    def test_synthesizer_prompt_contains_outputs(self):
        from nadirclaw.pipeline import _build_synthesizer_prompt

        plan = self._make_concurrent_plan()
        lane_outputs = {
            "builder:impl": "Implementation code here",
            "builder:tests": "Test code here",
        }
        prompt = _build_synthesizer_prompt(
            "Build API", lane_outputs, "code_generation", "Merge outputs", plan,
        )

        assert "PIPELINE BRIEFING" in prompt
        assert "Your role: synthesizer" in prompt
        assert "Implementation code here" in prompt
        assert "Test code here" in prompt
        assert "builder:impl" in prompt
        assert "builder:tests" in prompt

    def test_builder_prompt_intent_specific_action(self):
        """Different intents should produce different builder actions."""
        from nadirclaw.pipeline import _build_builder_prompt

        code_prompt = _build_builder_prompt("Write code", "code_generation", None)
        debug_prompt = _build_builder_prompt("Fix bug", "debugging", None)
        security_prompt = _build_builder_prompt("Audit app", "security_analysis", None)

        # Each should have a different assignment
        assert "production-quality code" in code_prompt
        assert "root cause" in debug_prompt
        assert "vulnerabilities" in security_prompt.lower() or "OWASP" in security_prompt

    def test_no_plan_still_has_intent_action(self):
        """Even without a plan, prompts should have intent-specific actions."""
        from nadirclaw.pipeline import _build_builder_prompt

        prompt = _build_builder_prompt("Design a system", "architecture", None)

        # No briefing block
        assert "PIPELINE BRIEFING" not in prompt
        # But still has the intent-specific action
        assert "architecture" in prompt.lower() or "component" in prompt.lower()


@pytest.mark.asyncio
class TestConcurrentPipelineExecution:
    """Test the concurrent execution path in pipeline.py."""

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_concurrent_code_generation(self, mock_dispatch):
        """code_generation should run parallel lanes, synthesize, then judge."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"

        call_count = 0
        call_roles = []

        async def mock_fn(model, messages):
            nonlocal call_count
            call_count += 1
            # Track prompts to verify roles
            prompt_text = messages[0]["content"] if messages else ""
            if "restructuring assistant" in prompt_text.lower():
                call_roles.append("blast")
            elif "parallel pipeline" in prompt_text.lower():
                call_roles.append("lane")
            elif "synthesis expert" in prompt_text.lower():
                call_roles.append("synthesizer")
            elif "reviewer" in prompt_text.lower():
                call_roles.append("judge")
            else:
                call_roles.append("unknown")

            return {
                "content": f"Response #{call_count}",
                "prompt_tokens": 50,
                "completion_tokens": 100,
            }

        mock_dispatch.side_effect = mock_fn

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="code_generation",
            messages=[{"role": "user", "content": "Build a REST API with auth"}],
        )

        assert result.status == "ok"
        assert result.blast_applied is True
        assert result.execution_plan["concurrent"] is True

        # Should have: BLAST + 2 lanes + synthesizer + judge = 5 dispatch calls
        # (compressor is fire-and-forget, happens after return)
        step_roles = [s.role for s in result.steps]
        assert "builder:impl" in step_roles
        assert "builder:tests" in step_roles
        assert "synthesizer" in step_roles
        assert "judge" in step_roles

        # Final content should come from synthesizer + judge
        assert result.final_content != ""

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_sequential_debugging(self, mock_dispatch):
        """debugging should NOT use concurrent execution."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"

        mock_dispatch.return_value = {
            "content": "Response",
            "prompt_tokens": 50,
            "completion_tokens": 100,
        }

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="debugging",
            messages=[{"role": "user", "content": "Fix the null pointer error"}],
        )

        assert result.status == "ok"
        assert result.execution_plan["concurrent"] is False

        step_roles = [s.role for s in result.steps]
        assert "builder" in step_roles
        assert "judge" in step_roles
        # No concurrent-specific roles
        assert "synthesizer" not in step_roles
        assert all(":" not in r for r in step_roles)  # no lane suffixes

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_concurrent_lane_failure_partial(self, mock_dispatch):
        """If one lane fails, synthesizer should still work with partial outputs."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"

        call_count = 0

        async def mock_fn(model, messages):
            nonlocal call_count
            call_count += 1
            prompt_text = messages[0]["content"] if messages else ""

            # Fail the second lane (tests lane)
            if call_count == 3:  # blast=1, lane1=2, lane2=3
                raise Exception("Model overloaded")

            return {
                "content": f"Response #{call_count}",
                "prompt_tokens": 50,
                "completion_tokens": 100,
            }

        mock_dispatch.side_effect = mock_fn

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="code_generation",
            messages=[{"role": "user", "content": "Build a REST API"}],
        )

        # Should still succeed (partial) even with one lane failing
        assert result.status in ("ok", "partial")
        assert result.final_content != ""

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_concurrent_all_lanes_fail(self, mock_dispatch):
        """If ALL lanes fail, result should be error."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"

        call_count = 0

        async def mock_fn(model, messages):
            nonlocal call_count
            call_count += 1

            # Let BLAST succeed, but fail both lanes
            if call_count == 1:
                return {
                    "content": "## Blueprint\nTask\n## Link\nN/A\n## Architect\nPlan\n## Style\nClean\n## Trigger\nShip",
                    "prompt_tokens": 50,
                    "completion_tokens": 100,
                }
            raise Exception("All models down")

        mock_dispatch.side_effect = mock_fn

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="code_generation",
            messages=[{"role": "user", "content": "Build something"}],
        )

        assert result.status == "error"
        assert "All concurrent builder lanes failed" in result.final_content

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_concurrent_synthesizer_failure_fallback(self, mock_dispatch):
        """If synthesizer fails, should concatenate lane outputs as fallback."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"

        call_count = 0

        async def mock_fn(model, messages):
            nonlocal call_count
            call_count += 1
            prompt_text = messages[0]["content"] if messages else ""

            # Let BLAST and lanes succeed, fail synthesizer
            if "synthesis expert" in prompt_text.lower():
                raise Exception("Synthesizer crashed")

            return {
                "content": f"Response #{call_count}",
                "prompt_tokens": 50,
                "completion_tokens": 100,
            }

        mock_dispatch.side_effect = mock_fn

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="code_generation",
            messages=[{"role": "user", "content": "Build a REST API"}],
        )

        # Should be partial because synthesizer failed
        assert result.status == "partial"
        assert result.final_content != ""

    @patch("nadirclaw.dispatch.dispatch_raw")
    async def test_concurrent_architecture_intent(self, mock_dispatch):
        """architecture intent should also use concurrent execution."""
        import os
        os.environ["NADIRCLAW_BLAST_ENABLED"] = "true"

        mock_dispatch.return_value = {
            "content": "Response",
            "prompt_tokens": 50,
            "completion_tokens": 100,
        }

        from nadirclaw.pipeline import execute_pipeline

        result = await execute_pipeline(
            intent="architecture",
            messages=[{"role": "user", "content": "Design a microservices system"}],
        )

        assert result.execution_plan["concurrent"] is True
        step_roles = [s.role for s in result.steps]
        assert "builder:design" in step_roles
        assert "builder:tradeoffs" in step_roles
        assert "synthesizer" in step_roles
