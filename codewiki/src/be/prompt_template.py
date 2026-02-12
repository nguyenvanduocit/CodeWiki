SYSTEM_PROMPT = """
<ROLE>
You are an AI documentation assistant. Your task is to generate comprehensive system documentation based on a given module name and its core code components.
</ROLE>

<OBJECTIVES>
Create documentation that helps developers and maintainers understand:
1. The module's purpose and core functionality
2. Architecture and component relationships
3. How the module fits into the overall system
4. Use the provided analysis metrics (PageRank, hub detection, complexity, TF-IDF keywords) to prioritize documentation depth — architecturally critical components deserve more detailed coverage
</OBJECTIVES>

<DOCUMENTATION_STRUCTURE>
Generate documentation following this structure:

1. **Main Documentation File** (`{module_name}.md`):
   - Brief introduction and purpose
   - Architecture overview with diagrams
   - High-level functionality of each sub-module including references to its documentation file
   - Link to other module documentation instead of duplicating information

2. **Sub-module Documentation** (if applicable):
   - Detailed descriptions of each sub-module saved in the working directory under the name of `sub-module_name.md`
   - Core components and their responsibilities

3. **Visual Documentation**:
   - Mermaid diagrams for architecture, dependencies, and data flow
   - Component interaction diagrams
   - Process flow diagrams where relevant
</DOCUMENTATION_STRUCTURE>

<WORKFLOW>
1. Analyze the provided code components and module structure, explore the not given dependencies between the components if needed
2. Create the main `{module_name}.md` file with overview and architecture in working directory
3. Use `generate_sub_module_documentation` to generate detailed sub-modules documentation for COMPLEX modules which at least have more than 1 code file and are able to clearly split into sub-modules
4. Include relevant Mermaid diagrams throughout the documentation
5. After all sub-modules are documented, adjust `{module_name}.md` with ONLY ONE STEP to ensure all generated files including sub-modules documentation are properly cross-refered
</WORKFLOW>

<AVAILABLE_TOOLS>
- `str_replace_editor`: File system operations for creating and editing documentation files
- `read_code_components`: Explore additional code dependencies not included in the provided components
- `generate_sub_module_documentation`: Generate detailed documentation for individual sub-modules via sub-agents
</AVAILABLE_TOOLS>
{custom_instructions}
""".strip()

LEAF_SYSTEM_PROMPT = """
<ROLE>
You are an AI documentation assistant. Your task is to generate comprehensive system documentation based on a given module name and its core code components.
</ROLE>

<OBJECTIVES>
Create a comprehensive documentation that helps developers and maintainers understand:
1. The module's purpose and core functionality
2. Architecture and component relationships
3. How the module fits into the overall system
4. Use the provided analysis metrics (PageRank, hub detection, complexity, TF-IDF keywords) to prioritize documentation depth — architecturally critical components deserve more detailed coverage
</OBJECTIVES>

<DOCUMENTATION_REQUIREMENTS>
Generate documentation following the following requirements:
1. Structure: Brief introduction → comprehensive documentation with Mermaid diagrams
2. Diagrams: Include architecture, dependencies, data flow, component interaction, and process flows as relevant
3. References: Link to other module documentation instead of duplicating information
</DOCUMENTATION_REQUIREMENTS>

<WORKFLOW>
1. Analyze provided code components and module structure
2. Explore dependencies between components if needed
3. Generate complete {module_name}.md documentation file
</WORKFLOW>

<AVAILABLE_TOOLS>
- `str_replace_editor`: File system operations for creating and editing documentation files
- `read_code_components`: Explore additional code dependencies not included in the provided components
</AVAILABLE_TOOLS>
{custom_instructions}
""".strip()

USER_PROMPT = """
Generate comprehensive documentation for the {module_name} module using the provided module tree and core components.

<MODULE_TREE>
{module_tree}
</MODULE_TREE>
* NOTE: You can refer the other modules in the module tree based on the dependencies between their core components to make the documentation more structured and avoid repeating the same information. Know that all documentation files are saved in the same folder not structured as module tree. e.g. [alt text]([ref_module_name].md)

<CORE_COMPONENT_CODES>
{formatted_core_component_codes}
</CORE_COMPONENT_CODES>
{analysis_metrics}
""".strip()

REPO_OVERVIEW_PROMPT = """
You are an AI documentation assistant. Your task is to generate a brief overview of the {repo_name} repository.

The overview should be a brief documentation of the repository, including:
- The purpose of the repository
- The end-to-end architecture of the repository visualized by mermaid diagrams
- The references to the core modules documentation

Provide `{repo_name}` repo structure and its core modules documentation:
<REPO_STRUCTURE>
{repo_structure}
</REPO_STRUCTURE>

Please generate the overview of the `{repo_name}` repository in markdown format with the following structure:
<OVERVIEW>
overview_content
</OVERVIEW>
""".strip()

MODULE_OVERVIEW_PROMPT = """
You are an AI documentation assistant. Your task is to generate a brief overview of `{module_name}` module.

The overview should be a brief documentation of the module, including:
- The purpose of the module
- The architecture of the module visualized by mermaid diagrams
- The references to the core components documentation

Provide repo structure and core components documentation of the `{module_name}` module:
<REPO_STRUCTURE>
{repo_structure}
</REPO_STRUCTURE>

Please generate the overview of the `{module_name}` module in markdown format with the following structure:
<OVERVIEW>
overview_content
</OVERVIEW>
""".strip()

CLUSTER_REPO_PROMPT = """
Here is list of all potential core components of the repository (It's normal that some components are not essential to the repository):
<POTENTIAL_CORE_COMPONENTS>
{potential_core_components}
</POTENTIAL_CORE_COMPONENTS>

Please group the components into groups such that each group is a set of components that are closely related to each other and together they form a module. DO NOT include components that are not essential to the repository.
Note: Algorithm-detected community groupings may be provided as comments in the component list. You may use them as a starting point but are free to adjust groupings based on your analysis.
Firstly reason about the components and then group them and return the result in the following format:
<GROUPED_COMPONENTS>
{{
    "module_name_1": {{
        "path": <path_to_the_module_1>, # the path to the module can be file or directory
        "components": [
            <component_name_1>,
            <component_name_2>,
            ...
        ]
    }},
    "module_name_2": {{
        "path": <path_to_the_module_2>,
        "components": [
            <component_name_1>,
            <component_name_2>,
            ...
        ]
    }},
    ...
}}
</GROUPED_COMPONENTS>
""".strip()

CLUSTER_MODULE_PROMPT = """
Here is the module tree of a repository:

<MODULE_TREE>
{module_tree}
</MODULE_TREE>

Here is list of all potential core components of the module {module_name} (It's normal that some components are not essential to the module):
<POTENTIAL_CORE_COMPONENTS>
{potential_core_components}
</POTENTIAL_CORE_COMPONENTS>

Please group the components into groups such that each group is a set of components that are closely related to each other and together they form a smaller module. DO NOT include components that are not essential to the module.
Note: Algorithm-detected community groupings may be provided as comments in the component list. You may use them as a starting point but are free to adjust groupings based on your analysis.

Firstly reason based on given context and then group them and return the result in the following format:
<GROUPED_COMPONENTS>
{{
    "module_name_1": {{
        "path": <path_to_the_module_1>, # the path to the module can be file or directory
        "components": [
            <component_name_1>,
            <component_name_2>,
            ...
        ]
    }},
    "module_name_2": {{
        "path": <path_to_the_module_2>,
        "components": [
            <component_name_1>,
            <component_name_2>,
            ...
        ]
    }},
    ...
}}
</GROUPED_COMPONENTS>
""".strip()

FILTER_FOLDERS_PROMPT = """
Here is the list of relative paths of files, folders in 2-depth of project {project_name}:
```
{files}
```

In order to analyze the core functionality of the project, we need to analyze the files, folders representing the core functionality of the project.

Please shortlist the files, folders representing the core functionality and ignore the files, folders that are not essential to the core functionality of the project (e.g. test files, documentation files, etc.) from the list above.

Reasoning at first, then return the list of relative paths in JSON format.
"""

from typing import Dict, Any, List
from codewiki.src.utils import file_manager
from codewiki.src.be.dependency_analyzer.models.core import Node

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".md": "markdown",
    ".sh": "bash",
    ".json": "json",
    ".yaml": "yaml",
    ".java": "java",
    ".js": "javascript",
    ".ts": "typescript",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".tsx": "typescript",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".cs": "csharp",
    ".php": "php",
    ".phtml": "php",
    ".inc": "php",
    ".go": "go"
}


def _format_module_tree_lines(module_tree: dict, module_name: str = None, indent: int = 0) -> List[str]:
    """Format module tree as indented lines."""
    lines = []
    for key, value in module_tree.items():
        if key == module_name:
            lines.append(f"{'  ' * indent}{key} (current module)")
        else:
            lines.append(f"{'  ' * indent}{key}")

        components = value.get('components', [])
        if components:
            lines.append(f"{'  ' * (indent + 1)} Core components: {', '.join(components)}")

        children = value.get("children", {})
        if isinstance(children, dict) and len(children) > 0:
            lines.append(f"{'  ' * (indent + 1)} Children:")
            lines.extend(_format_module_tree_lines(children, module_name, indent + 2))
    return lines


def format_component_metrics(component_ids: list[str], components: Dict[str, Any]) -> str:
    """Format component metrics for prompt injection."""
    # Check if any component has non-default metrics
    has_metrics = any(
        components[cid].pagerank > 0 or components[cid].is_hub or components[cid].complexity_score > 0
        for cid in component_ids if cid in components and hasattr(components[cid], 'pagerank')
    )
    if not has_metrics:
        return ""

    sections = []

    # Hub components
    hubs = [
        cid for cid in component_ids
        if cid in components and components[cid].is_hub
    ]
    if hubs:
        lines = ["## Architecturally Critical Components (Hubs)"]
        for cid in hubs:
            node = components[cid]
            lines.append(
                f"- **{node.name}** (PageRank: {node.pagerank:.4f}, "
                f"fan-in: {node.fan_in}, fan-out: {node.fan_out}) — "
                f"high connectivity, document thoroughly"
            )
        sections.append("\n".join(lines))

    # Community structure
    communities = {}
    for cid in component_ids:
        if cid in components and components[cid].community_id >= 0:
            comm_id = components[cid].community_id
            if comm_id not in communities:
                communities[comm_id] = []
            communities[comm_id].append(components[cid].name)
    if len(communities) > 1:
        lines = ["## Internal Community Structure"]
        for comm_id, members in sorted(communities.items()):
            lines.append(f"- Community {comm_id}: {', '.join(members)}")
        sections.append("\n".join(lines))

    # High complexity components
    complex_comps = [
        cid for cid in component_ids
        if cid in components and components[cid].complexity_score > 60
    ]
    if complex_comps:
        lines = ["## High Complexity Components"]
        for cid in complex_comps:
            node = components[cid]
            lines.append(
                f"- **{node.name}** (complexity: {node.complexity_score:.1f}/100) — "
                f"document logic flow carefully"
            )
        sections.append("\n".join(lines))

    # Key concepts (aggregated TF-IDF)
    keyword_scores = {}
    for cid in component_ids:
        if cid in components:
            for kw, score in components[cid].tfidf_keywords:
                keyword_scores[kw] = keyword_scores.get(kw, 0) + score
    if keyword_scores:
        top_keywords = sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True)[:15]
        lines = ["## Key Concepts"]
        lines.append("Top keywords: " + ", ".join(kw for kw, _ in top_keywords))
        sections.append("\n".join(lines))

    # Unstable components
    unstable = [
        cid for cid in component_ids
        if cid in components and components[cid].instability > 0.7
    ]
    if unstable:
        lines = ["## Unstable Components (high coupling)"]
        for cid in unstable:
            node = components[cid]
            lines.append(
                f"- **{node.name}** (instability: {node.instability:.2f}) — "
                f"depends heavily on other modules"
            )
        sections.append("\n".join(lines))

    return "\n\n".join(sections) if sections else ""


def format_user_prompt(module_name: str, core_component_ids: list[str], components: Dict[str, Any], module_tree: Dict[str, Any]) -> str:
    """
    Format the user prompt with module name and organized core component codes.

    Args:
        module_name: Name of the module to document
        core_component_ids: List of component IDs to include
        components: Dictionary mapping component IDs to CodeComponent objects

    Returns:
        Formatted user prompt string
    """
    formatted_module_tree = "\n".join(_format_module_tree_lines(module_tree, module_name, 0))

    # Group core component IDs by their file path
    grouped_components: dict[str, list[str]] = {}
    for component_id in core_component_ids:
        if component_id not in components:
            continue
        component = components[component_id]
        path = component.relative_path
        if path not in grouped_components:
            grouped_components[path] = []
        grouped_components[path].append(component_id)

    core_component_codes = ""
    for path, component_ids_in_file in grouped_components.items():
        core_component_codes += f"# File: {path}\n\n"
        core_component_codes += f"## Core Components in this file:\n"
        
        for component_id in component_ids_in_file:
            core_component_codes += f"- {component_id}\n"
        
        ext = '.' + path.split('.')[-1] if '.' in path else ''
        lang = EXTENSION_TO_LANGUAGE.get(ext, 'text')
        core_component_codes += f"\n## File Content:\n```{lang}\n"
        
        # Read content of the file using the first component's file path
        try:
            core_component_codes += file_manager.load_text(components[component_ids_in_file[0]].file_path)
        except (FileNotFoundError, IOError) as e:
            core_component_codes += f"# Error reading file: {e}\n"
        
        core_component_codes += "```\n\n"
        
    metrics_text = format_component_metrics(core_component_ids, components)
    analysis_metrics = ""
    if metrics_text:
        analysis_metrics = f"""
<ANALYSIS_METRICS>
{metrics_text}
</ANALYSIS_METRICS>
* NOTE: Use these metrics to understand architectural significance. Hub components should get more detailed coverage. High complexity components need careful documentation of their logic flow.
"""

    return USER_PROMPT.format(module_name=module_name, formatted_core_component_codes=core_component_codes, module_tree=formatted_module_tree, analysis_metrics=analysis_metrics)


def format_cluster_prompt(potential_core_components: str, module_tree: Dict[str, Any] = None, module_name: str = None) -> str:
    """
    Format the cluster prompt with potential core components and module tree.
    """
    if not module_tree:
        return CLUSTER_REPO_PROMPT.format(potential_core_components=potential_core_components)

    formatted_module_tree = "\n".join(_format_module_tree_lines(module_tree, module_name, 0))
    return CLUSTER_MODULE_PROMPT.format(potential_core_components=potential_core_components, module_tree=formatted_module_tree, module_name=module_name)


def format_system_prompt(module_name: str, custom_instructions: str = None) -> str:
    """
    Format the system prompt with module name and optional custom instructions.
    
    Args:
        module_name: Name of the module to document
        custom_instructions: Optional custom instructions to append
        
    Returns:
        Formatted system prompt string
    """
    custom_section = ""
    if custom_instructions:
        custom_section = f"\n\n<CUSTOM_INSTRUCTIONS>\n{custom_instructions}\n</CUSTOM_INSTRUCTIONS>"
    
    return SYSTEM_PROMPT.format(module_name=module_name, custom_instructions=custom_section).strip()


def format_leaf_system_prompt(module_name: str, custom_instructions: str = None) -> str:
    """
    Format the leaf system prompt with module name and optional custom instructions.
    
    Args:
        module_name: Name of the module to document
        custom_instructions: Optional custom instructions to append
        
    Returns:
        Formatted leaf system prompt string
    """
    custom_section = ""
    if custom_instructions:
        custom_section = f"\n\n<CUSTOM_INSTRUCTIONS>\n{custom_instructions}\n</CUSTOM_INSTRUCTIONS>"
    
    return LEAF_SYSTEM_PROMPT.format(module_name=module_name, custom_instructions=custom_section).strip()