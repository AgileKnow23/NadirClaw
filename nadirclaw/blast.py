"""BLAST Prompt Optimizer for NadirClaw.

Restructures user prompts using the BLAST methodology before they enter
the pipeline Builder step.  Uses a local LLM (via dispatch_raw) to
intelligently decompose the prompt into five sections:

  B — Blueprint:  desired outcome, success criteria, constraints
  L — Link:       integrations (APIs, databases, services, dependencies)
  A — Architect:  plan of approach (steps, components, architecture)
  S — Style:      quality attributes (conventions, patterns, elegance)
  T — Trigger:    execution instructions (what to build, how to verify)

Each intent has a tailored system prompt so the local model focuses on
the most relevant aspects for the task at hand.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nadirclaw.blast")

# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class BlastResult:
    """Result of BLAST prompt optimization."""
    original_prompt: str
    enhanced_prompt: str
    intent: str
    sections: Dict[str, str] = field(default_factory=dict)
    latency_ms: int = 0
    used_llm: bool = False
    execution_plan: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Role descriptions — what each pipeline agent does per intent
# ---------------------------------------------------------------------------

_ROLE_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "code_generation": {
        "builder": "Generate production-quality code based on the BLAST-structured requirements",
        "judge": "Review code for correctness, edge cases, performance, and security",
        "compressor": "Extract coding patterns and decisions for long-term memory",
    },
    "code_review": {
        "builder": "Perform deep review: correctness, performance, security, readability",
        "judge": "Verify review accuracy and check for missed issues",
        "compressor": "Store review patterns and recurring feedback themes",
    },
    "architecture": {
        "builder": "Design system architecture with component diagrams and trade-off analysis",
        "judge": "Validate design against scalability, fault-tolerance, and cost requirements",
        "compressor": "Capture architectural decisions and rationale for future reference",
    },
    "debugging": {
        "builder": "Analyze root cause using hypothesis-driven debugging approach",
        "judge": "Verify diagnosis and ensure fix doesn't introduce regressions",
        "compressor": None,  # skipped
    },
    "security_analysis": {
        "builder": "Audit for vulnerabilities using OWASP methodology and threat modeling",
        "judge": "Cross-check findings and validate severity classifications",
        "compressor": "Store security patterns and remediation strategies",
    },
    "documentation": {
        "builder": "Write clear, audience-appropriate documentation with examples",
        "judge": "Review for accuracy, completeness, and readability",
        "compressor": None,
    },
    "general_qa": {
        "builder": "Answer with structured reasoning and supporting evidence",
        "judge": "Fact-check response and identify gaps or inaccuracies",
        "compressor": None,
    },
}


# ---------------------------------------------------------------------------
# Concurrent phase definitions — which intents benefit from parallelism
# ---------------------------------------------------------------------------
# Each entry defines parallel "lanes" in Phase 1 (build), with a synthesizer
# in Phase 2 that merges them.  Intents not listed here use the standard
# sequential pipeline.  Each lane gets a different model + focus area so
# they don't conflict.
#
# Keys:
#   "lanes"  — list of parallel builder tasks (role_suffix, config_key, action)
#   "synth"  — action description for the synthesis step
# ---------------------------------------------------------------------------

@dataclass
class PhaseLane:
    """One parallel lane in the build phase."""
    role_suffix: str      # e.g. "impl" → agent name becomes "builder:impl"
    config_key: str       # which pipeline config model to use ("builder" or "judge" etc.)
    action: str           # what this lane does


@dataclass
class ConcurrentPhases:
    """Phase definition for an intent that supports parallel execution."""
    lanes: List[PhaseLane]
    synth_action: str     # what the synthesizer does after lanes complete


_CONCURRENT_PHASES: Dict[str, ConcurrentPhases] = {
    "code_generation": ConcurrentPhases(
        lanes=[
            PhaseLane("impl", "builder", "Write the main implementation code"),
            PhaseLane("tests", "judge", "Write comprehensive tests and edge-case analysis"),
        ],
        synth_action="Merge implementation and tests into a coherent, ship-ready response",
    ),
    "architecture": ConcurrentPhases(
        lanes=[
            PhaseLane("design", "builder", "Design high-level architecture, component diagram, and data flow"),
            PhaseLane("tradeoffs", "judge", "Analyze trade-offs, failure modes, scalability limits, and alternatives"),
        ],
        synth_action="Synthesize design and trade-off analysis into a unified architecture document",
    ),
    "security_analysis": ConcurrentPhases(
        lanes=[
            PhaseLane("owasp", "builder", "Audit against OWASP Top 10 and common vulnerability patterns"),
            PhaseLane("threat", "judge", "Perform threat modeling: attack surfaces, trust boundaries, data flows"),
        ],
        synth_action="Merge OWASP audit and threat model into a prioritized security report",
    ),
    "code_review": ConcurrentPhases(
        lanes=[
            PhaseLane("logic", "builder", "Review correctness, logic errors, edge cases, and performance"),
            PhaseLane("quality", "judge", "Review code style, readability, security, and maintainability"),
        ],
        synth_action="Combine logic and quality reviews into a unified review with prioritized findings",
    ),
}


def get_concurrent_phases(intent: str) -> Optional[ConcurrentPhases]:
    """Return concurrent phase config for an intent, or None for sequential."""
    return _CONCURRENT_PHASES.get(intent)


def _short(model: str) -> str:
    return model.split("/")[-1] if "/" in model else model


def build_execution_plan(
    intent: str,
    sections: Dict[str, str],
    pipeline_config: Dict[str, Optional[str]],
    blast_model: str,
    used_llm: bool,
) -> Dict[str, Any]:
    """Build a high-level execution plan showing the BLAST summary,
    which models handle each step, and what each agent will do.

    For intents that support concurrent execution, the plan shows phases
    with parallel lanes.  Otherwise it shows the standard sequential chain.

    Returns a structured dict suitable for JSON serialization.
    """
    blueprint = sections.get("blueprint", "N/A")
    summary = blueprint[:150] + ("..." if len(blueprint) > 150 else "")

    concurrent = get_concurrent_phases(intent)

    # ── Build phases ────────────────────────────────────────────────
    phases: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []  # flat list for backward compat
    step_counter = 0

    # Phase 0: BLAST
    blast_step = {
        "step": step_counter,
        "agent": "blast_optimizer",
        "model": blast_model,
        "model_short": _short(blast_model),
        "action": "Decompose prompt into BLAST framework (Blueprint → Link → Architect → Style → Trigger)",
        "used_llm": used_llm,
        "phase": 0,
    }
    steps.append(blast_step)
    phases.append({
        "phase": 0,
        "name": "BLAST Analysis",
        "parallel": False,
        "steps": [blast_step],
    })
    step_counter += 1

    if concurrent:
        # ── Phased concurrent execution ──
        # Phase 1: Parallel builder lanes
        lane_steps = []
        for lane in concurrent.lanes:
            model = pipeline_config.get(lane.config_key, pipeline_config.get("builder", ""))
            s = {
                "step": step_counter,
                "agent": f"builder:{lane.role_suffix}",
                "model": model,
                "model_short": _short(model) if model else "?",
                "action": lane.action,
                "phase": 1,
            }
            lane_steps.append(s)
            steps.append(s)
            step_counter += 1

        phases.append({
            "phase": 1,
            "name": "Concurrent Build",
            "parallel": True,
            "steps": lane_steps,
        })

        # Phase 2: Synthesizer
        synth_model = pipeline_config.get("builder", "")
        synth_step = {
            "step": step_counter,
            "agent": "synthesizer",
            "model": synth_model,
            "model_short": _short(synth_model) if synth_model else "?",
            "action": concurrent.synth_action,
            "phase": 2,
        }
        steps.append(synth_step)
        phases.append({
            "phase": 2,
            "name": "Synthesis",
            "parallel": False,
            "steps": [synth_step],
        })
        step_counter += 1

        # Phase 3: Judge reviews the synthesized output
        judge_model = pipeline_config.get("judge")
        if judge_model:
            role_descs = _ROLE_DESCRIPTIONS.get(intent, _ROLE_DESCRIPTIONS["general_qa"])
            j_step = {
                "step": step_counter,
                "agent": "judge",
                "model": judge_model,
                "model_short": _short(judge_model),
                "action": role_descs.get("judge", "Review synthesized output"),
                "phase": 3,
            }
            steps.append(j_step)
            phases.append({
                "phase": 3,
                "name": "Review",
                "parallel": False,
                "steps": [j_step],
            })
            step_counter += 1

        # Phase 4: Compressor (if applicable)
        comp_model = pipeline_config.get("compressor")
        if comp_model:
            role_descs = _ROLE_DESCRIPTIONS.get(intent, _ROLE_DESCRIPTIONS["general_qa"])
            c_desc = role_descs.get("compressor")
            if c_desc:
                c_step = {
                    "step": step_counter,
                    "agent": "compressor",
                    "model": comp_model,
                    "model_short": _short(comp_model),
                    "action": c_desc,
                    "phase": 4,
                }
                steps.append(c_step)
                phases.append({
                    "phase": 4,
                    "name": "Memory",
                    "parallel": False,
                    "steps": [c_step],
                })
    else:
        # ── Standard sequential execution ──
        role_descs = _ROLE_DESCRIPTIONS.get(intent, _ROLE_DESCRIPTIONS["general_qa"])
        for role in ("builder", "judge", "compressor"):
            model = pipeline_config.get(role)
            if model is None:
                continue
            desc = role_descs.get(role, f"Execute {role} step")
            if desc is None:
                continue
            s = {
                "step": step_counter,
                "agent": role,
                "model": model,
                "model_short": _short(model),
                "action": desc,
                "phase": step_counter,
            }
            steps.append(s)
            phases.append({
                "phase": step_counter,
                "name": role.capitalize(),
                "parallel": False,
                "steps": [s],
            })
            step_counter += 1

    return {
        "summary": summary,
        "intent": intent,
        "concurrent": concurrent is not None,
        "total_agents": len(steps),
        "total_phases": len(phases),
        "phases": phases,
        "steps": steps,  # flat list for backward compat
    }


# ---------------------------------------------------------------------------
# Intent-specific system prompts for the local LLM
# ---------------------------------------------------------------------------

_BASE_INSTRUCTIONS = """\
You are a prompt structuring assistant. Your ONLY job is to restructure the \
user's request into the BLAST framework sections below. Do NOT answer the \
request — only restructure it.

Output EXACTLY these five labeled sections (keep the labels):

## Blueprint
Define the desired outcome, success criteria, and constraints.

## Link
Identify integrations, APIs, databases, services, libraries, and dependencies.

## Architect
Plan the approach: steps, components, architecture decisions.

## Style
Define quality attributes: conventions, patterns, simplicity, elegance.

## Trigger
Execution instructions: what to build first, how to verify, definition of done.

Rules:
- Plan first, then act.  Verify before marking done.
- Demand elegance and simplicity — the minimum complexity for the task.
- Be concise — each section should be 1-4 bullet points.
- If a section is not applicable, write "N/A" for that section.
"""

_INTENT_CONTEXT: Dict[str, str] = {
    "code_generation": (
        "Context: This is a CODE GENERATION task.\n"
        "Blueprint: Focus on function signatures, expected behavior, edge cases.\n"
        "Link: Emphasize libraries, APIs, runtime dependencies.\n"
        "Architect: Detail implementation steps, data structures, algorithms.\n"
        "Style: Coding conventions, error handling patterns, test coverage.\n"
        "Trigger: Write code, add tests, verify all tests pass."
    ),
    "code_review": (
        "Context: This is a CODE REVIEW task.\n"
        "Blueprint: Focus on quality criteria, what constitutes a good review.\n"
        "Link: Related modules, dependencies being reviewed.\n"
        "Architect: Review checklist (correctness, performance, security, readability).\n"
        "Style: Team conventions, severity levels for feedback.\n"
        "Trigger: Review, annotate inline, suggest improvements."
    ),
    "architecture": (
        "Context: This is an ARCHITECTURE / SYSTEM DESIGN task.\n"
        "Blueprint: System requirements, scalability goals, non-functional requirements.\n"
        "Link: Services, databases, APIs, message queues, external systems.\n"
        "Architect: Component diagram, data flow, technology choices with trade-offs.\n"
        "Style: Design patterns, scalability patterns, fault-tolerance.\n"
        "Trigger: Design document, validate against requirements, get sign-off."
    ),
    "debugging": (
        "Context: This is a DEBUGGING task.\n"
        "Blueprint: Expected behavior vs actual behavior, reproduction steps.\n"
        "Link: Logs, stack traces, related components, environment details.\n"
        "Architect: Root cause hypotheses ranked by likelihood.\n"
        "Style: Minimal fix — change only what's necessary.\n"
        "Trigger: Diagnose root cause, apply fix, verify fix doesn't regress."
    ),
    "security_analysis": (
        "Context: This is a SECURITY ANALYSIS task.\n"
        "Blueprint: Threat model scope, assets to protect, trust boundaries.\n"
        "Link: Attack surfaces, authentication flows, external APIs, data stores.\n"
        "Architect: OWASP Top 10 checklist, vulnerability assessment approach.\n"
        "Style: Severity classification (Critical/High/Medium/Low), CVSS if applicable.\n"
        "Trigger: Audit, write findings report, recommend remediations."
    ),
    "documentation": (
        "Context: This is a DOCUMENTATION task.\n"
        "Blueprint: Audience, purpose, scope of the documentation.\n"
        "Link: Code references, API endpoints, configuration files to document.\n"
        "Architect: Document structure (sections, headings, examples).\n"
        "Style: Tone (technical vs conversational), format (markdown/docstring/wiki).\n"
        "Trigger: Write draft, review for accuracy, publish."
    ),
    "general_qa": (
        "Context: This is a GENERAL Q&A task.\n"
        "Blueprint: Question scope, what constitutes a complete answer.\n"
        "Link: Relevant context, references, related topics.\n"
        "Architect: Reasoning approach (step-by-step, comparison, pros/cons).\n"
        "Style: Depth level (brief overview vs deep dive), citation style.\n"
        "Trigger: Answer with evidence, cite sources where possible."
    ),
}


# ---------------------------------------------------------------------------
# BLAST Optimizer
# ---------------------------------------------------------------------------

class BLASTOptimizer:
    """Optimizes prompts using the BLAST framework via a local LLM."""

    def __init__(self):
        self._section_pattern = re.compile(
            r"##\s*(Blueprint|Link|Architect|Style|Trigger)\s*\n(.*?)(?=\n##\s*|$)",
            re.DOTALL | re.IGNORECASE,
        )

    async def optimize(self, prompt: str, intent: str) -> BlastResult:
        """Restructure a prompt into BLAST format using a local LLM.

        Falls back to a lightweight template wrapper if the LLM call fails.
        """
        start = time.time()

        # Build the system prompt
        context = _INTENT_CONTEXT.get(intent, _INTENT_CONTEXT["general_qa"])
        system_prompt = f"{_BASE_INSTRUCTIONS}\n{context}"

        try:
            enhanced, sections, used_llm = await self._call_llm(system_prompt, prompt)
        except Exception as e:
            logger.warning("BLAST LLM call failed, using template fallback: %s", e)
            enhanced, sections = self._template_fallback(prompt, intent)
            used_llm = False

        latency_ms = int((time.time() - start) * 1000)

        return BlastResult(
            original_prompt=prompt,
            enhanced_prompt=enhanced,
            intent=intent,
            sections=sections,
            latency_ms=latency_ms,
            used_llm=used_llm,
        )

    async def _call_llm(self, system_prompt: str, user_prompt: str):
        """Call a local LLM to restructure the prompt."""
        from nadirclaw.dispatch import dispatch_raw
        from nadirclaw.settings import settings

        model = settings.BLAST_MODEL
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await dispatch_raw(model, messages)
        raw_output = response.get("content", "")

        sections = self._parse_sections(raw_output)

        # Build the enhanced prompt: original prompt + BLAST structure
        enhanced = self._format_enhanced(user_prompt, sections)
        return enhanced, sections, True

    def _parse_sections(self, text: str) -> Dict[str, str]:
        """Parse BLAST sections from LLM output."""
        sections: Dict[str, str] = {}
        for match in self._section_pattern.finditer(text):
            label = match.group(1).strip().lower()
            content = match.group(2).strip()
            sections[label] = content

        # Ensure all five keys exist
        for key in ("blueprint", "link", "architect", "style", "trigger"):
            if key not in sections:
                sections[key] = "N/A"

        return sections

    def _template_fallback(self, prompt: str, intent: str) -> tuple:
        """Lightweight template fallback when LLM is unavailable."""
        context = _INTENT_CONTEXT.get(intent, _INTENT_CONTEXT["general_qa"])
        sections = {
            "blueprint": f"[User request — {intent}] {prompt[:200]}",
            "link": "Identify from context",
            "architect": "Determine optimal approach",
            "style": "Follow best practices, keep it simple",
            "trigger": "Implement, verify, ship",
        }
        enhanced = self._format_enhanced(prompt, sections)
        return enhanced, sections

    @staticmethod
    def _format_enhanced(original_prompt: str, sections: Dict[str, str]) -> str:
        """Combine original prompt with BLAST structure."""
        parts = [original_prompt, "", "---", "BLAST Analysis:", ""]
        for label in ("blueprint", "link", "architect", "style", "trigger"):
            header = label.capitalize()
            content = sections.get(label, "N/A")
            parts.append(f"**{header}:** {content}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_optimizer: Optional[BLASTOptimizer] = None


def get_blast_optimizer() -> BLASTOptimizer:
    """Return the singleton BLASTOptimizer instance."""
    global _optimizer
    if _optimizer is None:
        _optimizer = BLASTOptimizer()
    return _optimizer
