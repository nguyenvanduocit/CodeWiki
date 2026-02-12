"""
Claude Agent SDK adapter for CodeWiki.

Provides drop-in replacements for LLM calls and module processing
using Claude Code CLI via the claude-agent-sdk package.
"""

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
    LEAF_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    EXTENSION_TO_LANGUAGE,
)
from codewiki.src.be.cluster_modules import (
    cluster_modules as _original_cluster_modules,
    format_potential_core_components,
)
from codewiki.src.be.llm_services import call_llm as _original_call_llm
from codewiki.src.be.prompt_template import format_cluster_prompt
from codewiki.src.be.utils import count_tokens, is_complex_module
from codewiki.src.utils import file_manager

logger = logging.getLogger(__name__)


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
        model=model or "sonnet",
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


async def agent_sdk_process_module(
    module_name: str,
    components: Dict[str, Any],
    core_component_ids: List[str],
    module_path: List[str],
    working_dir: str,
    config: Config,
) -> Dict[str, Any]:
    """
    Replacement for AgentOrchestrator.process_module() using Claude Agent SDK.

    Claude Code gets full tool access (Read, Write, Edit, Grep, Glob) and
    reads source files directly from the repo, then writes docs to working_dir.
    """
    module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
    module_tree = file_manager.load_json(module_tree_path)

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

    # Build system prompt
    custom_instructions = config.get_prompt_addition() if config else ""
    custom_section = ""
    if custom_instructions:
        custom_section = f"\n\n<CUSTOM_INSTRUCTIONS>\n{custom_instructions}\n</CUSTOM_INSTRUCTIONS>"

    if is_complex_module(components, core_component_ids):
        system_prompt_template = SYSTEM_PROMPT
    else:
        system_prompt_template = LEAF_SYSTEM_PROMPT

    # Adapt system prompt: replace tool references since Claude Code has its own tools
    system_prompt = system_prompt_template.format(
        module_name=module_name,
        custom_instructions=custom_section,
    )
    # Replace tool-specific sections with Claude Code instructions.
    # Both complex (3-tool) and leaf (2-tool) prompts share the same replacement.
    claude_code_tools = (
        "<AVAILABLE_TOOLS>\n"
        "You have full access to Read, Write, Edit, Grep, and Glob tools.\n"
        "- Use Read to examine source code files\n"
        "- Use Write to create documentation files\n"
        "- Use Edit to modify documentation files\n"
        "- Use Grep/Glob to explore the codebase\n"
        "</AVAILABLE_TOOLS>"
    )
    for old_tools in [
        "<AVAILABLE_TOOLS>\n"
        "- `str_replace_editor`: File system operations for creating and editing documentation files\n"
        "- `read_code_components`: Explore additional code dependencies not included in the provided components\n"
        "- `generate_sub_module_documentation`: Generate detailed documentation for individual sub-modules via sub-agents\n"
        "</AVAILABLE_TOOLS>",
        "<AVAILABLE_TOOLS>\n"
        "- `str_replace_editor`: File system operations for creating and editing documentation files\n"
        "- `read_code_components`: Explore additional code dependencies not included in the provided components\n"
        "</AVAILABLE_TOOLS>",
    ]:
        system_prompt = system_prompt.replace(old_tools, claude_code_tools)

    # Format module tree for context
    module_tree_text = json.dumps(module_tree, indent=2)

    user_prompt = f"""Generate comprehensive documentation for the **{module_name}** module.

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

Instructions:
1. Read the source files for the core components listed above
2. Analyze the code structure, dependencies, and functionality
3. Create `{module_name}.md` in the output directory with comprehensive documentation
4. Include Mermaid diagrams for architecture, dependencies, and data flow
5. All documentation files should be saved in: {os.path.abspath(working_dir)}
6. Reference other modules by linking to `[module_name](module_name.md)` — all docs are in the same flat directory
"""

    logger.info(f"Agent SDK processing module: {module_name}")

    options = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        cwd=os.path.abspath(config.repo_path),
        system_prompt=system_prompt,
        model="sonnet",
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

    # Reload module tree (Claude Code may not have modified it, but docs should exist now)
    if os.path.exists(module_tree_path):
        module_tree = file_manager.load_json(module_tree_path)

    return module_tree


async def agent_sdk_cluster(
    leaf_nodes: List[str],
    components: Dict[str, Any],
    config: Config,
    current_module_tree: dict[str, Any] = {},
    current_module_name: str = None,
    current_module_path: List[str] = [],
) -> Dict[str, Any]:
    """
    Replacement for cluster_modules() using Claude Agent SDK for LLM calls.

    Reuses the existing clustering logic (parsing, recursion, module tree building)
    but replaces call_llm with agent_sdk_call_llm.
    """
    potential_core_components, potential_core_components_with_code = (
        format_potential_core_components(leaf_nodes, components)
    )

    if count_tokens(potential_core_components_with_code) <= config.max_token_per_module:
        logger.debug(
            f"Skipping clustering for {current_module_name} because the potential core "
            f"components are too few: {count_tokens(potential_core_components_with_code)} tokens"
        )
        return {}

    prompt = format_cluster_prompt(potential_core_components, current_module_tree, current_module_name)
    response = await agent_sdk_call_llm(prompt, config, model="sonnet")

    # Parse the response (same logic as cluster_modules)
    try:
        if "<GROUPED_COMPONENTS>" not in response or "</GROUPED_COMPONENTS>" not in response:
            logger.error(f"Invalid LLM response format - missing component tags: {response[:200]}...")
            return {}

        response_content = response.split("<GROUPED_COMPONENTS>")[1].split("</GROUPED_COMPONENTS>")[0]
        module_tree = json.loads(response_content)

        if not isinstance(module_tree, dict):
            logger.error(f"Invalid module tree format - expected dict, got {type(module_tree)}")
            return {}

    except Exception as e:
        logger.error(f"Failed to parse LLM response: {e}. Response: {response[:200]}...")
        return {}

    if len(module_tree) <= 1:
        logger.debug(
            f"Skipping clustering for {current_module_name} because the module tree "
            f"is too small: {len(module_tree)} modules"
        )
        return {}

    if current_module_tree == {}:
        current_module_tree = module_tree
    else:
        value = current_module_tree
        for key in current_module_path:
            value = value[key]["children"]
        for mod_name, mod_info in module_tree.items():
            del mod_info["path"]
            value[mod_name] = mod_info

    for mod_name, mod_info in module_tree.items():
        sub_leaf_nodes = mod_info.get("components", [])

        valid_sub_leaf_nodes = [n for n in sub_leaf_nodes if n in components]
        for n in sub_leaf_nodes:
            if n not in components:
                logger.warning(
                    f"Skipping invalid sub leaf node '{n}' in module '{mod_name}' - not found in components"
                )

        current_module_path.append(mod_name)
        mod_info["children"] = {}
        mod_info["children"] = await agent_sdk_cluster(
            valid_sub_leaf_nodes, components, config,
            current_module_tree, mod_name, current_module_path,
        )
        current_module_path.pop()

    return module_tree
