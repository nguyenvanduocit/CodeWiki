"""
Claude Agent SDK adapter for CodeWiki.

Provides drop-in replacements for LLM calls and module processing
using Claude Code CLI via the claude-agent-sdk package.
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Any, Optional

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from codewiki.src.config import Config, MODULE_TREE_FILENAME
from codewiki.src.be.prompt_template import (
    format_system_prompt,
    format_leaf_system_prompt,
    format_component_metrics,
    format_debug_notebook_prompt,
    format_monitoring_notebook_prompt,
    STRUCTURE_ANALYSIS_PROMPT,
    FLOW_ANALYSIS_PROMPT,
    API_ANALYSIS_PROMPT,
    COMPOSER_PROMPT,
    VERIFIER_PROMPT,
    CLAUDE_CODE_TOOLS_SECTION,
)
from codewiki.src.be.utils import is_complex_module
from codewiki.src.utils import file_manager

logger = logging.getLogger(__name__)

MAX_VERIFICATION_ATTEMPTS = 3


async def agent_sdk_call_llm(
    prompt: str,
    config: Config,
    model: Optional[str] = None,
) -> str:
    """
    Drop-in replacement for call_llm() using Claude Agent SDK.

    Uses query() with max_turns=1 and no tools — pure text completion.
    Used for clustering, overview generation, and other simple LLM calls.
    """
    options = ClaudeAgentOptions(
        max_turns=1,
        permission_mode="default",
        model=model or config.main_model or "opus",
        system_prompt=("You are an AI assistant helping with code documentation. "
                       "Respond directly to the prompt without using any tools."),
    )

    text_parts: list[str] = []
    model_name = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            if model_name is None:
                model_name = message.model
                logger.info(f"Agent SDK ({model_name}): call_llm started")
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            if message.is_error:
                raise ValueError(f"Agent SDK ({model_name or 'unknown'}) error: {message.result}")
            logger.info(f"Agent SDK ({model_name or 'unknown'}): call_llm completed "
                        f"(cost=${message.total_cost_usd or 0:.4f})")

    result = "".join(text_parts)
    if not result:
        raise ValueError("Claude Agent SDK returned no content")

    return result


def _parse_verifier_json(raw: str) -> dict:
    """Parse verifier response as JSON, handling markdown fencing."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        first_newline = text.index("\n")
        last_fence = text.rfind("```")
        text = text[first_newline + 1:last_fence].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse verifier JSON, raw output: {text[:500]}")
        return {"score": 0, "needs_revision": False, "tasks": []}


async def _verify_and_fix_loop(
    module_name: str,
    docs_path: str,
    component_list: str,
    config: Config,
    model: str | None = None,
    max_attempts: int = MAX_VERIFICATION_ATTEMPTS,
) -> None:
    """
    Verify documentation quality and iteratively fix issues.

    Runs the verifier up to max_attempts times. If the verifier returns
    a task list with needs_revision=True, a revision agent is dispatched
    to fix the issues. The loop continues until the verifier approves
    or max_attempts is exhausted.
    """
    for attempt in range(1, max_attempts + 1):
        if not os.path.exists(docs_path):
            logger.warning(f"Docs file not found at {docs_path}, skipping verification")
            return

        doc_content = file_manager.load_text(docs_path)
        if not doc_content:
            logger.warning(f"Empty docs at {docs_path}, skipping verification")
            return

        logger.info(f"Verification attempt {attempt}/{max_attempts} for {module_name}")

        try:
            verifier_prompt = VERIFIER_PROMPT.format(
                module_name=module_name,
                documentation_content=doc_content[:50000],
                component_list=component_list,
            )
            raw_response = await agent_sdk_call_llm(verifier_prompt, config)
            result = _parse_verifier_json(raw_response)
        except Exception as e:
            logger.warning(f"Verification call failed for {module_name}: {e}")
            return

        score = result.get("score", 0)
        needs_revision = result.get("needs_revision", False)
        tasks = result.get("tasks", [])

        logger.info(f"Verification score for {module_name}: {score}/100 "
                     f"(needs_revision={needs_revision}, tasks={len(tasks)})")

        if not needs_revision or not tasks:
            logger.info(f"Verifier approved documentation for {module_name}")
            return

        # Format task list for the revision agent
        task_lines = []
        for i, task in enumerate(tasks, 1):
            t_type = task.get("type", "UNKNOWN")
            t_section = task.get("section", "")
            t_desc = task.get("description", "")
            task_lines.append(f"{i}. [{t_type}] Section: \"{t_section}\" — {t_desc}")
        task_list_text = "\n".join(task_lines)

        logger.info(f"Dispatching revision agent for {module_name} "
                     f"(attempt {attempt}/{max_attempts}, {len(tasks)} tasks)")

        # Dispatch revision agent with structured task list
        revision_system = f"""You are a documentation editor. Revise the documentation for **{module_name}** based on the verifier's task list.

{CLAUDE_CODE_TOOLS_SECTION}

Read the existing documentation, fix each task listed below, and write the improved version back to the same file. Verify claims against actual source code."""

        revision_user = f"""Revise the documentation at {os.path.abspath(docs_path)}

<TASK_LIST>
{task_list_text}
</TASK_LIST>

<REPOSITORY_PATH>
{os.path.abspath(config.repo_path)}
</REPOSITORY_PATH>

Fix every task in the list. For each task:
- TRUTHFULNESS: verify component names against source code, remove or correct hallucinated names
- EVIDENCE: add `path/file.ext:line_number` references to the specified sections
- COMPLETENESS: add the missing sections with proper content
- QUALITY: rewrite the quoted text following the style rules

After fixing all tasks, save the updated documentation to the same file path.
"""

        revision_options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=os.path.abspath(config.repo_path),
            system_prompt=revision_system,
            model=model or config.main_model or "opus",
        )

        try:
            async for message in query(prompt=revision_user, options=revision_options):
                if isinstance(message, ResultMessage):
                    if message.is_error:
                        logger.warning(f"Revision agent error for {module_name}: {message.result}")
                    else:
                        logger.info(f"Revision completed for {module_name} "
                                    f"(attempt {attempt}, cost=${message.total_cost_usd or 0:.4f})")
        except Exception as e:
            logger.warning(f"Revision agent failed for {module_name}: {e}")
            return

    logger.warning(f"Verification loop exhausted for {module_name} after {max_attempts} attempts")


async def agent_sdk_process_module(
    module_name: str,
    components: Dict[str, Any],
    core_component_ids: List[str],
    module_path: List[str],
    working_dir: str,
    config: Config,
    model: Optional[str] = None,
    dependency_context: str = "",
) -> Dict[str, Any]:
    """
    Process a module using Claude Agent SDK.

    Claude Code gets full tool access (Read, Write, Edit, Grep, Glob) and
    reads source files directly from the repo, then writes docs to working_dir.
    """
    module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
    module_tree = file_manager.load_json(module_tree_path) or {}

    # Skip if docs already exist
    docs_path = os.path.join(working_dir, f"{module_name}.md")
    if os.path.exists(docs_path):
        logger.info(f"Module docs already exists at {docs_path}")
        return module_tree

    # Build component info for the prompt
    component_lines = []
    for comp_id in core_component_ids:
        if comp_id not in components:
            continue
        comp = components[comp_id]
        component_lines.append(f"- {comp_id} (file: {comp.relative_path})")

    component_list = "\n".join(component_lines) if component_lines else "(no components)"

    # Build system prompt using format functions (handles tool section automatically)
    custom_instructions = config.get_prompt_addition() if config else ""
    if is_complex_module(components, core_component_ids):
        system_prompt = format_system_prompt(module_name, custom_instructions, use_claude_code_tools=True)
    else:
        system_prompt = format_leaf_system_prompt(module_name, custom_instructions, use_claude_code_tools=True)

    # Format module tree for context
    module_tree_text = json.dumps(module_tree, indent=2)

    # Build analysis metrics for the prompt
    metrics_text = format_component_metrics(core_component_ids, components)
    analysis_metrics_section = ""
    if metrics_text:
        analysis_metrics_section = f"""
<ANALYSIS_METRICS>
{metrics_text}
</ANALYSIS_METRICS>
"""

    dependency_context_section = ""
    if dependency_context:
        dependency_context_section = f"\n{dependency_context}\n"

    user_prompt = f"""Generate comprehensive, evidence-based documentation for the **{module_name}** module.

<REPOSITORY_PATH>
{os.path.abspath(config.repo_path)}
</REPOSITORY_PATH>

<OUTPUT_DIRECTORY>
{os.path.abspath(working_dir)}
</OUTPUT_DIRECTORY>

<MODULE_TREE>
{module_tree_text}
</MODULE_TREE>

<CORE_COMPONENTS>
{component_list}
</CORE_COMPONENTS>
{analysis_metrics_section}{dependency_context_section}
<GENERATED_ARTIFACTS>
Pre-computed analysis artifacts are available in the output directory:
- `codebase_map.json`: Dependency graph summary with per-component metrics (PageRank, betweenness, complexity, community IDs), circular dependencies, temporal couplings, and architectural violations
- `temp/dependency_graphs/`: Full dependency graph JSON with complete component data
- `module_tree.json`: Hierarchical module decomposition tree
</GENERATED_ARTIFACTS>

Instructions:
1. Read `codebase_map.json` in the output directory FIRST to understand architectural metrics and hub components
2. Read the source files for all core components listed above
3. Verify every claim against actual code — if a name suggests X but code does Y, document Y
4. Create `{module_name}.md` in the output directory with comprehensive documentation
5. Include evidence: reference components as `path/file.ext:line_number` throughout the prose
6. Include Mermaid diagrams: at minimum an architecture diagram and one sequence/flow diagram
7. Include sections for: design rationale, failure modes, change impact/blast radius
8. All documentation files should be saved in: {os.path.abspath(working_dir)}
9. Reference other modules by linking to `[module_name](module_name.md)` — all docs are in the same flat directory
"""

    logger.info(f"Agent SDK processing module: {module_name}")

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        cwd=os.path.abspath(config.repo_path),
        system_prompt=system_prompt,
        model=model or config.main_model or "opus",
    )

    model_name = None
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            if model_name is None:
                model_name = message.model
                logger.info(f"Agent SDK ({model_name}): processing {module_name}")
            for block in message.content:
                if isinstance(block, TextBlock):
                    logger.debug(f"Agent ({model_name}): {block.text[:200]}...")
        elif isinstance(message, ResultMessage):
            if message.is_error:
                logger.error(f"Agent SDK ({model_name or 'unknown'}) error for {module_name}: {message.result}")
                raise RuntimeError(f"Agent SDK failed for module {module_name}: {message.result}")
            logger.info(f"Agent SDK ({model_name or 'unknown'}): completed {module_name} "
                        f"(turns={message.num_turns}, cost=${message.total_cost_usd or 0:.4f})")

    # Verify and fix loop for standard mode
    docs_path = os.path.join(working_dir, f"{module_name}.md")
    await _verify_and_fix_loop(
        module_name=module_name,
        docs_path=docs_path,
        component_list=component_list,
        config=config,
        model=model,
    )

    # Reload module tree (Claude Code may not have modified it, but docs should exist now)
    if os.path.exists(module_tree_path):
        module_tree = file_manager.load_json(module_tree_path) or {}

    return module_tree


async def _run_analysis_agent(
    agent_name: str,
    prompt_template: str,
    module_name: str,
    repo_path: str,
    output_path: str,
    component_list: str,
    config: Config,
    dependency_context: str = "",
) -> str:
    """Run a single analysis agent and return the output file path."""
    system_prompt = prompt_template.format(
        module_name=module_name,
        output_path=output_path,
        tools_section=CLAUDE_CODE_TOOLS_SECTION,
    )

    dep_context_section = ""
    if dependency_context:
        dep_context_section = f"\n{dependency_context}\n"

    user_prompt = f"""Analyze the **{module_name}** module.

<REPOSITORY_PATH>
{os.path.abspath(repo_path)}
</REPOSITORY_PATH>

<CORE_COMPONENTS>
{component_list}
</CORE_COMPONENTS>
{dep_context_section}
Write your analysis to: {os.path.abspath(output_path)}
"""

    logger.info(f"Deep analysis: running {agent_name} for {module_name}")

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        cwd=os.path.abspath(repo_path),
        system_prompt=system_prompt,
        model=config.main_model or "opus",
    )

    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    logger.debug(f"{agent_name}: {block.text[:150]}...")
        elif isinstance(message, ResultMessage):
            if message.is_error:
                logger.error(f"{agent_name} error for {module_name}: {message.result}")
                raise RuntimeError(f"{agent_name} failed: {message.result}")
            logger.info(f"{agent_name} completed for {module_name} (turns={message.num_turns}, cost=${message.total_cost_usd or 0:.4f})")

    return output_path


async def agent_sdk_process_module_deep(
    module_name: str,
    components: Dict[str, Any],
    core_component_ids: List[str],
    module_path: List[str],
    working_dir: str,
    config: Config,
    model: Optional[str] = None,
    dependency_context: str = "",
) -> Dict[str, Any]:
    """
    Process a module using deep multi-agent analysis pipeline.

    Pipeline: 3 parallel analysis agents -> Composer -> Verifier -> (optional revision)
    """
    module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
    module_tree = file_manager.load_json(module_tree_path) or {}

    # Skip if docs already exist
    docs_path = os.path.join(working_dir, f"{module_name}.md")
    if os.path.exists(docs_path):
        logger.info(f"Module docs already exist at {docs_path}")
        return module_tree

    # Build component list
    component_lines = []
    for comp_id in core_component_ids:
        if comp_id not in components:
            continue
        comp = components[comp_id]
        component_lines.append(f"- {comp_id} (file: {comp.relative_path})")
    component_list = "\n".join(component_lines) if component_lines else "(no components)"

    # Create temp directory for analysis files
    analysis_dir = os.path.join(working_dir, "temp", f"_analysis_{module_name}")
    os.makedirs(analysis_dir, exist_ok=True)

    structure_path = os.path.join(analysis_dir, "structure_analysis.md")
    flow_path = os.path.join(analysis_dir, "flow_analysis.md")
    api_path = os.path.join(analysis_dir, "api_analysis.md")

    # Phase 1: Run 3 analysis agents concurrently
    logger.info(f"Deep analysis Phase 1: running 3 analysis agents for {module_name}")

    analysis_tasks = [
        _run_analysis_agent(
            "StructureAnalyst", STRUCTURE_ANALYSIS_PROMPT,
            module_name, config.repo_path, structure_path, component_list, config,
            dependency_context=dependency_context,
        ),
        _run_analysis_agent(
            "FlowAnalyst", FLOW_ANALYSIS_PROMPT,
            module_name, config.repo_path, flow_path, component_list, config,
            dependency_context=dependency_context,
        ),
        _run_analysis_agent(
            "APIAnalyst", API_ANALYSIS_PROMPT,
            module_name, config.repo_path, api_path, component_list, config,
            dependency_context=dependency_context,
        ),
    ]

    results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

    # Log any failures but continue with available analyses
    for i, result in enumerate(results):
        agent_names = ["StructureAnalyst", "FlowAnalyst", "APIAnalyst"]
        if isinstance(result, Exception):
            logger.error(f"{agent_names[i]} failed for {module_name}: {result}")

    successful = sum(1 for r in results if not isinstance(r, Exception))
    if successful == 0:
        logger.warning(f"All 3 analysis agents failed for {module_name}. Composer will work from source code only.")

    # Phase 2: Composer agent synthesizes analysis into final doc
    logger.info(f"Deep analysis Phase 2: composing documentation for {module_name}")

    composer_system = COMPOSER_PROMPT.format(
        module_name=module_name,
        output_path=docs_path,
        tools_section=CLAUDE_CODE_TOOLS_SECTION,
    )

    # Build list of available analysis files
    available_analyses = []
    for path, name in [(structure_path, "Structure"), (flow_path, "Flow"), (api_path, "API")]:
        if os.path.exists(path):
            available_analyses.append(f"- {name} analysis: {os.path.abspath(path)}")
    analyses_list = "\n".join(available_analyses) if available_analyses else "(no analyses available)"

    # Build analysis metrics
    metrics_text = format_component_metrics(core_component_ids, components)
    metrics_section = ""
    if metrics_text:
        metrics_section = f"\n<ANALYSIS_METRICS>\n{metrics_text}\n</ANALYSIS_METRICS>\n"

    module_tree_text = json.dumps(module_tree, indent=2)

    dependency_context_section = ""
    if dependency_context:
        dependency_context_section = f"\n{dependency_context}\n"

    composer_user = f"""Synthesize comprehensive documentation for the **{module_name}** module.

<REPOSITORY_PATH>
{os.path.abspath(config.repo_path)}
</REPOSITORY_PATH>

<OUTPUT_PATH>
{os.path.abspath(docs_path)}
</OUTPUT_PATH>

<MODULE_TREE>
{module_tree_text}
</MODULE_TREE>

<ANALYSIS_FILES>
{analyses_list}
</ANALYSIS_FILES>

<CORE_COMPONENTS>
{component_list}
</CORE_COMPONENTS>
{metrics_section}{dependency_context_section}
Instructions:
1. Read ALL analysis files listed above
2. Read the source code for core components to verify claims
3. Synthesize into one comprehensive `{module_name}.md` at the output path
4. Do NOT concatenate — create a coherent narrative
5. Resolve conflicts between analyses by reading source code
6. Include evidence (`path:line`) for every factual claim
7. Reference other modules: `[module_name](module_name.md)`
"""

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        cwd=os.path.abspath(config.repo_path),
        system_prompt=composer_system,
        model=model or config.main_model or "opus",
    )

    async for message in query(prompt=composer_user, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    logger.debug(f"Composer: {block.text[:150]}...")
        elif isinstance(message, ResultMessage):
            if message.is_error:
                logger.error(f"Composer error for {module_name}: {message.result}")
                raise RuntimeError(f"Composer failed for {module_name}: {message.result}")
            logger.info(f"Composer completed for {module_name} (turns={message.num_turns}, cost=${message.total_cost_usd or 0:.4f})")

    # Phase 3: Verify and fix loop (shared with standard mode)
    logger.info(f"Deep analysis Phase 3: verifying documentation for {module_name}")
    await _verify_and_fix_loop(
        module_name=module_name,
        docs_path=docs_path,
        component_list=component_list,
        config=config,
        model=model,
    )

    # Cleanup analysis temp files (optional, keep for debugging)
    # shutil.rmtree(analysis_dir, ignore_errors=True)

    # Reload module tree
    if os.path.exists(module_tree_path):
        module_tree = file_manager.load_json(module_tree_path) or {}

    return module_tree


_NOTEBOOK_CONFIGS = {
    "debug": {
        "suffix": "debug",
        "label": "DebugNotebook",
        "format_prompt_fn": format_debug_notebook_prompt,
        "doc_description": "a debug investigation runbook",
        "instructions": [
            "1. Read the architecture doc (if available) to understand the module's responsibilities",
            "2. Read source files for core components to identify actual error paths and failure conditions",
            "3. Read `codebase_map.json` in {working_dir} to identify hub components",
            "4. Create `{module_name}_{suffix}.md` at the output path with all required sections",
            "5. Every failure mode must reference actual code paths with `path:line` evidence",
            "6. Reference other modules by linking to `[module_name](module_name.md)`",
        ],
    },
    "monitoring": {
        "suffix": "monitoring",
        "label": "MonitoringNotebook",
        "format_prompt_fn": format_monitoring_notebook_prompt,
        "doc_description": "a monitoring specification notebook",
        "instructions": [
            "1. Read the architecture doc (if available) to understand the module's responsibilities and I/O patterns",
            "2. Read source files for core components to identify actual operations, timeouts, and error conditions",
            "3. Read `codebase_map.json` in {working_dir} to identify this module's role and dependencies",
            "4. Create `{module_name}_{suffix}.md` at the output path with all required sections",
            "5. Use `{module_name}_` as the metric name prefix for consistency",
            "6. All alert thresholds must be justified by actual code behavior (timeouts, retry counts, etc.)",
            "7. Reference other modules by linking to `[module_name](module_name.md)`",
        ],
    },
}


async def _agent_sdk_process_notebook(
    notebook_type: str,
    module_name: str,
    components: Dict[str, Any],
    core_component_ids: List[str],
    module_path: List[str],
    working_dir: str,
    config: Config,
    model: Optional[str] = None,
    dependency_context: str = "",
    no_cache: bool = False,
) -> None:
    """Internal helper: generate a notebook (debug or monitoring) for a module."""
    nb = _NOTEBOOK_CONFIGS[notebook_type]
    suffix = nb["suffix"]
    label = nb["label"]

    output_path = os.path.join(working_dir, f"{module_name}_{suffix}.md")

    if not no_cache and os.path.exists(output_path):
        logger.info(f"{label} already exists at {output_path}")
        return

    system_prompt = nb["format_prompt_fn"](
        module_name=module_name,
        tools_section=CLAUDE_CODE_TOOLS_SECTION,
        dependency_context=dependency_context,
    )

    # Build component list for context
    component_lines = []
    for comp_id in core_component_ids:
        if comp_id not in components:
            continue
        comp = components[comp_id]
        component_lines.append(f"- {comp_id} (file: {comp.relative_path})")
    component_list = "\n".join(component_lines) if component_lines else "(no components)"

    arch_doc_path = os.path.join(working_dir, f"{module_name}.md")
    arch_doc_note = (
        f"Architecture doc available at: {os.path.abspath(arch_doc_path)}"
        if os.path.exists(arch_doc_path)
        else "No architecture doc available yet — read source files directly."
    )

    dep_context_section = f"\n{dependency_context}\n" if dependency_context else ""

    instructions = "\n".join(
        line.format(module_name=module_name, suffix=suffix, working_dir=os.path.abspath(working_dir))
        for line in nb["instructions"]
    )

    user_prompt = f"""Generate {nb["doc_description"]} for the **{module_name}** module.

<REPOSITORY_PATH>
{os.path.abspath(config.repo_path)}
</REPOSITORY_PATH>

<OUTPUT_PATH>
{os.path.abspath(output_path)}
</OUTPUT_PATH>

<ARCHITECTURE_CONTEXT>
{arch_doc_note}
</ARCHITECTURE_CONTEXT>

<CORE_COMPONENTS>
{component_list}
</CORE_COMPONENTS>
{dep_context_section}
Instructions:
{instructions}
"""

    logger.info(f"Agent SDK generating {suffix} notebook: {module_name}")

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        cwd=os.path.abspath(config.repo_path),
        system_prompt=system_prompt,
        model=model or config.main_model or "opus",
    )

    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    logger.debug(f"{label} ({module_name}): {block.text[:150]}...")
        elif isinstance(message, ResultMessage):
            if message.is_error:
                logger.error(f"{label} agent error for {module_name}: {message.result}")
                raise RuntimeError(f"{label} agent failed for {module_name}: {message.result}")
            logger.info(f"{label} completed for {module_name} "
                        f"(turns={message.num_turns}, cost=${message.total_cost_usd or 0:.4f})")


async def agent_sdk_process_debug_notebook(
    module_name: str,
    components: Dict[str, Any],
    core_component_ids: List[str],
    module_path: List[str],
    working_dir: str,
    config: Config,
    model: Optional[str] = None,
    dependency_context: str = "",
    no_cache: bool = False,
) -> None:
    """Generate a debug investigation runbook for a module."""
    await _agent_sdk_process_notebook(
        "debug", module_name, components, core_component_ids,
        module_path, working_dir, config, model=model,
        dependency_context=dependency_context, no_cache=no_cache,
    )


async def agent_sdk_process_monitoring_notebook(
    module_name: str,
    components: Dict[str, Any],
    core_component_ids: List[str],
    module_path: List[str],
    working_dir: str,
    config: Config,
    model: Optional[str] = None,
    dependency_context: str = "",
    no_cache: bool = False,
) -> None:
    """Generate a monitoring specification notebook for a module."""
    await _agent_sdk_process_notebook(
        "monitoring", module_name, components, core_component_ids,
        module_path, working_dir, config, model=model,
        dependency_context=dependency_context, no_cache=no_cache,
    )
