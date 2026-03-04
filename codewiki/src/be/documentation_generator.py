import json
import logging
import os
import re
import traceback
from collections import deque
from copy import deepcopy
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable

from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder
from codewiki.src.be.prompt_template import (
    REPO_OVERVIEW_PROMPT,
    MODULE_OVERVIEW_PROMPT,
    CODEBASE_MAP_PROMPT,
    format_summary_metrics_section,
    format_dependency_context,
)
from codewiki.src.be.cluster_modules import cluster_modules
from codewiki.src.be.claude_agent_sdk_adapter import agent_sdk_call_llm, agent_sdk_process_module, agent_sdk_process_module_deep
from codewiki.src.config import (
    Config,
    INITIAL_MODULE_TREE_FILENAME,
    METADATA_FILENAME,
    MODULE_TREE_FILENAME,
    OVERVIEW_FILENAME
)
from codewiki.src.utils import file_manager

logger = logging.getLogger(__name__)


class DocumentationGenerator:
    """Main documentation generation orchestrator."""

    def __init__(self, config: Config, commit_id: str = None):
        self.config = config
        self.commit_id = commit_id
        self.graph_builder = DependencyGraphBuilder(config)
        self.module_summaries: Dict[str, str] = {}

    # ── Metadata (pipeline state) ──

    def _load_metadata(self, working_dir: str) -> dict:
        """Load existing metadata.json or return empty dict."""
        path = os.path.join(working_dir, METADATA_FILENAME)
        if os.path.exists(path):
            try:
                return file_manager.load_json(path) or {}
            except Exception as e:
                logger.warning(f"Failed to load metadata, starting fresh: {e}")
        return {}

    def _save_metadata(self, working_dir: str, metadata: dict) -> None:
        """Atomically save metadata.json (write to tmp then rename)."""
        path = os.path.join(working_dir, METADATA_FILENAME)
        tmp_path = path + '.tmp'
        try:
            file_manager.save_json(metadata, tmp_path)
            os.replace(tmp_path, path)
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            logger.error(f"Failed to save metadata: {e}")
            raise

    def _init_metadata(self, metadata: dict, components: Dict[str, Any], num_leaf_nodes: int) -> dict:
        """Initialize or update metadata with generation info and pipeline state."""
        metadata["generation_info"] = {
            "timestamp": datetime.now().isoformat(),
            "main_model": self.config.main_model,
            "generator_version": "1.1.0",
            "repo_path": self.config.repo_path,
            "commit_id": self.commit_id
        }
        metadata["statistics"] = {
            "total_components": len(components),
            "leaf_nodes": num_leaf_nodes,
            "max_depth": self.config.max_depth
        }
        if "pipeline_state" not in metadata:
            metadata["pipeline_state"] = {
                "stages_completed": [],
                "modules": {}
            }
        return metadata

    def _mark_stage_completed(self, working_dir: str, metadata: dict, stage_name: str) -> None:
        """Mark a pipeline stage as completed and persist."""
        stages = metadata.setdefault("pipeline_state", {}).setdefault("stages_completed", [])
        if stage_name not in stages:
            stages.append(stage_name)
        self._save_metadata(working_dir, metadata)

    def _is_stage_completed(self, metadata: dict, stage_name: str) -> bool:
        return stage_name in metadata.get("pipeline_state", {}).get("stages_completed", [])

    def _mark_module_completed(self, working_dir: str, metadata: dict, module_key: str) -> None:
        """Mark a module as completed and persist immediately."""
        modules = metadata.setdefault("pipeline_state", {}).setdefault("modules", {})
        modules[module_key] = "completed"
        self._save_metadata(working_dir, metadata)

    def _get_completed_modules(self, metadata: dict) -> set:
        """Get set of module keys that are marked completed."""
        modules = metadata.get("pipeline_state", {}).get("modules", {})
        return {k for k, v in modules.items() if v == "completed"}

    def _finalize_metadata(self, working_dir: str, metadata: dict) -> None:
        """Finalize metadata with generated files list."""
        files = []
        try:
            for f in os.listdir(working_dir):
                if f.endswith(('.md', '.json', '.html')):
                    files.append(f)
        except Exception as e:
            logger.warning(f"Could not list generated files: {e}")
        metadata["files_generated"] = sorted(files)
        metadata["generation_info"]["completed_at"] = datetime.now().isoformat()
        self._save_metadata(working_dir, metadata)

    # ── LLM dispatch ──

    async def _process_module(self, module_name, components, component_ids,
                              module_path, working_dir, dependency_context: str = ""):
        """Dispatch module processing to Agent SDK."""
        if self.config.deep_analysis:
            return await agent_sdk_process_module_deep(
                module_name, components, component_ids,
                module_path, working_dir, self.config,
                dependency_context=dependency_context
            )
        return await agent_sdk_process_module(
            module_name, components, component_ids,
            module_path, working_dir, self.config,
            dependency_context=dependency_context
        )

    async def _call_llm(self, prompt):
        """Call LLM via Agent SDK."""
        return await agent_sdk_call_llm(prompt, self.config)

    # ── Module tree helpers ──

    def get_processing_order(self, module_tree: Dict[str, Any], parent_path: List[str] = None) -> List[tuple[List[str], str]]:
        """Get the processing order using topological sort (leaf modules first)."""
        if parent_path is None:
            parent_path = []
        processing_order = []

        def collect_modules(tree: Dict[str, Any], path: List[str]):
            for module_name, module_info in tree.items():
                current_path = path + [module_name]

                if module_info.get("children") and isinstance(module_info["children"], dict) and module_info["children"]:
                    collect_modules(module_info["children"], current_path)
                    processing_order.append((current_path, module_name))
                else:
                    processing_order.append((current_path, module_name))

        collect_modules(module_tree, parent_path)
        return processing_order

    def build_module_dependency_graph(self, module_tree: Dict[str, Any], components: Dict[str, Any]) -> Dict[str, set]:
        """Build module-level dependency graph from component dependencies.

        Returns dict mapping module_name -> set of module_names it depends on.
        """
        # Walk the module tree to build component_id -> module_name mapping
        component_to_module: Dict[str, str] = {}

        def map_components(tree: Dict[str, Any]):
            for module_name, module_info in tree.items():
                for comp_id in module_info.get("components", []):
                    component_to_module[comp_id] = module_name
                children = module_info.get("children")
                if children and isinstance(children, dict):
                    map_components(children)

        map_components(module_tree)

        # Build module-level dependency graph
        module_deps: Dict[str, set] = {}

        # Collect all module names first
        def collect_module_names(tree: Dict[str, Any]):
            for module_name, module_info in tree.items():
                module_deps.setdefault(module_name, set())
                children = module_info.get("children")
                if children and isinstance(children, dict):
                    collect_module_names(children)

        collect_module_names(module_tree)

        # For each component, check its depends_on and map to module-level edges
        for comp_id, module_name in component_to_module.items():
            if comp_id not in components:
                continue
            node = components[comp_id]
            for dep_id in node.depends_on:
                dep_module = component_to_module.get(dep_id)
                if dep_module and dep_module != module_name:
                    module_deps[module_name].add(dep_module)

        return module_deps

    def topological_sort_modules(self, module_tree: Dict[str, Any], components: Dict[str, Any]) -> List[tuple[List[str], str]]:
        """Get processing order using topological sort based on dependency graph.

        Falls back to tree-hierarchy order if cycles exist.
        """
        module_deps = self.build_module_dependency_graph(module_tree, components)
        tree_order = self.get_processing_order(module_tree)

        # Build a lookup: module_name -> (path, name) from tree order
        tree_order_lookup: Dict[str, tuple[List[str], str]] = {}
        for path, name in tree_order:
            tree_order_lookup[name] = (path, name)

        # Kahn's algorithm for topological sort (dependencies first)
        # in_degree = how many deps each module has
        in_degree: Dict[str, int] = {m: len(deps) for m, deps in module_deps.items()}

        # Build reverse graph: dep -> set of modules that depend on it
        reverse_deps: Dict[str, set] = {m: set() for m in module_deps}
        for module_name, deps in module_deps.items():
            for dep in deps:
                if dep in reverse_deps:
                    reverse_deps[dep].add(module_name)

        # Start with modules that have no dependencies
        queue = deque(sorted(m for m, d in in_degree.items() if d == 0))
        topo_order: List[str] = []
        topo_level: Dict[str, int] = {m: 0 for m in queue}

        while queue:
            module_name = queue.popleft()
            topo_order.append(module_name)
            # For each module that depends on this one, decrement its in_degree
            for dependent in sorted(reverse_deps.get(module_name, set())):
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    # Track the level: max dependency depth + 1
                    topo_level[dependent] = max(
                        topo_level.get(dependent, 0),
                        topo_level[module_name] + 1,
                    )
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        # Check for cycles
        remaining = set(module_deps.keys()) - set(topo_order)
        if remaining:
            logger.warning(
                f"Dependency cycle detected among modules: {remaining}. "
                f"Appending in tree-hierarchy order as fallback."
            )
            for path, name in tree_order:
                if name in remaining:
                    topo_order.append(name)

        # Enforce tree constraint: leaf modules before their parents.
        # The tree_order already has this property (children before parents).
        # We use tree_order positions as a tiebreaker to ensure parents come after children.
        tree_position: Dict[str, int] = {}
        for idx, (_, name) in enumerate(tree_order):
            tree_position[name] = idx

        # Build final order: sort by topo level (dependency depth) primarily,
        # tree position as tiebreaker (children before parents within same level).
        sorted_names = sorted(
            topo_order,
            key=lambda name: (topo_level.get(name, 0), tree_position.get(name, 0))
        )

        # Convert back to the expected format: List[tuple[List[str], str]]
        processing_order: List[tuple[List[str], str]] = []
        seen = set()
        for name in sorted_names:
            if name in tree_order_lookup and name not in seen:
                seen.add(name)
                processing_order.append(tree_order_lookup[name])

        # Ensure any modules from tree_order that weren't in module_deps are included
        for path, name in tree_order:
            if name not in seen:
                seen.add(name)
                processing_order.append((path, name))

        logger.info(f"Topological processing order: {[name for _, name in processing_order]}")
        return processing_order

    def is_leaf_module(self, module_info: Dict[str, Any]) -> bool:
        """Check if a module is a leaf module (has no children or empty children)."""
        children = module_info.get("children", {})
        return not children or (isinstance(children, dict) and len(children) == 0)

    def build_overview_structure(self, module_tree: Dict[str, Any], module_path: List[str],
                                 working_dir: str) -> Dict[str, Any]:
        """Build structure for overview generation with 1-depth children docs and target indicator."""

        processed_module_tree = deepcopy(module_tree)
        module_info = processed_module_tree
        for path_part in module_path:
            module_info = module_info[path_part]
            if path_part != module_path[-1]:
                module_info = module_info.get("children", {})
            else:
                module_info["is_target_for_overview_generation"] = True

        if "children" in module_info:
            module_info = module_info["children"]

        for child_name, child_info in module_info.items():
            if os.path.exists(os.path.join(working_dir, f"{child_name}.md")):
                child_info["docs"] = file_manager.load_text(os.path.join(working_dir, f"{child_name}.md"))
            else:
                logger.warning(f"Module docs not found at {os.path.join(working_dir, f'{child_name}.md')}")
                child_info["docs"] = ""

        return processed_module_tree

    def _extract_module_summary(self, docs_path: str, module_name: str, max_chars: int = 800) -> str:
        """Extract a brief summary from a generated module doc file.

        Takes the first heading and first 2-3 paragraphs (up to max_chars).
        """
        if not os.path.exists(docs_path):
            return ""
        try:
            content = file_manager.load_text(docs_path)
            if not content:
                return ""

            lines = content.split('\n')
            summary_lines = []
            char_count = 0

            for line in lines:
                # Stop at max_chars
                if char_count > max_chars:
                    break
                # Skip empty lines at the start
                if not summary_lines and not line.strip():
                    continue
                summary_lines.append(line)
                char_count += len(line)

            return '\n'.join(summary_lines).strip()
        except Exception as e:
            logger.debug(f"Could not extract summary for {module_name}: {e}")
            return ""

    # ── Cross-document linking ──

    def post_process_cross_links(self, working_dir: str, module_tree: Dict[str, Any]) -> None:
        """Post-process all generated docs to add inter-document markdown links.

        Walks every .md file in working_dir and replaces mentions of other module
        names with relative markdown links.  Avoids linking inside code blocks,
        inline code, or existing markdown links, and only links the first
        occurrence of each module per heading section.
        """
        # Step 1: Build module_name -> filename mapping
        module_files: Dict[str, str] = {}
        self._collect_module_files(module_tree, module_files)

        # Include well-known special files
        overview_stem = OVERVIEW_FILENAME.replace(".md", "")
        module_files[overview_stem] = OVERVIEW_FILENAME
        module_files["CODEBASE_MAP"] = "CODEBASE_MAP.md"

        if not module_files:
            return

        # Step 2: Process each .md file
        for filename in os.listdir(working_dir):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(working_dir, filename)
            try:
                content = file_manager.load_text(filepath)
            except Exception:
                continue
            if not content:
                continue

            current_module = filename.replace(".md", "")
            modified = self._add_cross_links(content, module_files, current_module)

            if modified != content:
                file_manager.save_text(modified, filepath)
                logger.debug(f"Added cross-links to {filename}")

    def _collect_module_files(self, tree: Dict[str, Any], result: Dict[str, str]) -> None:
        """Recursively walk the module tree and populate name -> filename mapping."""
        for name, info in tree.items():
            result[name] = f"{name}.md"
            children = info.get("children", {})
            if children and isinstance(children, dict):
                self._collect_module_files(children, result)

    def _add_cross_links(self, content: str, module_files: Dict[str, str],
                         current_module: str) -> str:
        """Replace module-name mentions with markdown links in non-code text.

        Splits the document by fenced code blocks and inline code spans so that
        only prose segments are modified.  Within each heading section, only the
        first occurrence of each module name is linked (to avoid over-linking).
        """
        # Split content into code and non-code segments.
        # Fenced code blocks (```...```) and inline code (`...`) are preserved as-is.
        segments = re.split(r"(```[\s\S]*?```|`[^`\n]+`)", content)

        # Build name variants: both "Module Name" (with spaces) and
        # "module_name" (with underscores) should match for each module.
        # We sort by length descending so longer names match first and
        # shorter substrings don't accidentally consume partial matches.
        link_targets: List[tuple[str, str, re.Pattern]] = []
        for name, filename in sorted(module_files.items(), key=lambda x: len(x[0]), reverse=True):
            if name == current_module:
                continue
            # Variant with underscores replaced by spaces
            display_name = name.replace("_", " ")
            escaped = re.escape(name)
            # Match both underscore form and space form
            if "_" in name:
                space_variant = re.escape(display_name)
                pattern = re.compile(
                    r"(?<!\[)(?<!\()\b(" + escaped + r"|" + space_variant + r")\b(?!\]|\))",
                    re.IGNORECASE,
                )
            else:
                pattern = re.compile(
                    r"(?<!\[)(?<!\()\b(" + escaped + r")\b(?!\]|\))",
                    re.IGNORECASE,
                )
            link_targets.append((name, filename, pattern))

        # Track linked modules per section to enforce first-occurrence-only rule.
        linked_in_section: set = set()

        result_parts: List[str] = []
        for i, segment in enumerate(segments):
            # Odd-indexed segments are code blocks/inline code -- keep as-is
            if i % 2 == 1:
                result_parts.append(segment)
                continue

            # Reset per-section tracking when we encounter a heading
            lines = segment.split("\n")
            processed_lines: List[str] = []
            for line in lines:
                # Detect heading to reset per-section tracking
                if re.match(r"^#{1,6}\s", line):
                    linked_in_section = set()

                modified_line = line
                for name, filename, pattern in link_targets:
                    if name in linked_in_section:
                        continue
                    # Check if this line already contains a markdown link for this module
                    if f"](./{filename})" in modified_line or f"]({filename})" in modified_line:
                        continue

                    def _replace_first(match: re.Match) -> str:
                        """Replace only the first match and mark as linked."""
                        matched_text = match.group(0)
                        # Don't replace if inside an existing markdown link
                        # Check surrounding context in the full line
                        start = match.start()
                        prefix = modified_line[:start]
                        # If there's an unmatched '[' before us, we're inside a link text
                        open_brackets = prefix.count("[") - prefix.count("]")
                        if open_brackets > 0:
                            return matched_text
                        return f"[{matched_text}](./{filename})"

                    new_line, count = pattern.subn(_replace_first, modified_line, count=1)
                    if count > 0:
                        modified_line = new_line
                        linked_in_section.add(name)

                processed_lines.append(modified_line)

            result_parts.append("\n".join(processed_lines))

        return "".join(result_parts)

    # ── Documentation generation ──

    async def generate_module_documentation(self, components: Dict[str, Any], leaf_nodes: List[str],
                                            module_tree: Dict[str, Any], changed_ids: set,
                                            metadata: dict, working_dir: str) -> str:
        """Generate documentation for all modules using dynamic programming approach.

        Uses metadata to track per-module completion for crash recovery.
        """
        file_manager.ensure_directory(working_dir)

        self.module_deps = self.build_module_dependency_graph(module_tree, components)
        processing_order = self.topological_sort_modules(module_tree, components)
        completed_modules = self._get_completed_modules(metadata)
        final_module_tree = module_tree

        if module_tree:
            for module_path, module_name in processing_order:
                module_key = "/".join(module_path)
                try:
                    module_info = module_tree
                    for path_part in module_path:
                        module_info = module_info[path_part]
                        if path_part != module_path[-1]:
                            module_info = module_info.get("children", {})

                    docs_path = os.path.join(working_dir, f"{module_name}.md")

                    # Determine if module has changed components (for incremental)
                    has_changes = True
                    if changed_ids is not None:
                        module_component_ids = set(module_info.get("components", []))
                        has_changes = bool(module_component_ids.intersection(changed_ids))

                    # Skip if docs exist on disk and no components changed
                    # Filesystem is the primary truth (survives metadata corruption)
                    if not has_changes and os.path.exists(docs_path):
                        logger.info(f"Skipping module (docs exist, no changes): {module_key}")
                        if module_key not in completed_modules:
                            self._mark_module_completed(working_dir, metadata, module_key)
                            completed_modules.add(module_key)
                        continue

                    # Collect dependency context from already-processed modules
                    dependency_context = ""
                    if hasattr(self, 'module_deps') and module_name in self.module_deps:
                        dep_summaries = {}
                        for dep_name in self.module_deps[module_name]:
                            if dep_name in self.module_summaries:
                                dep_summaries[dep_name] = self.module_summaries[dep_name]
                        dependency_context = format_dependency_context(module_name, dep_summaries)

                    if self.is_leaf_module(module_info):
                        logger.info(f"Processing leaf module: {module_key}")
                        final_module_tree = await self._process_module(
                            module_name, components, module_info.get("components", []),
                            module_path, working_dir, dependency_context=dependency_context
                        )
                    elif self.config.progressive == 2:
                        logger.info(f"Skipping parent module in progressive phase 2: {module_key}")
                        continue
                    else:
                        logger.info(f"Processing parent module: {module_key}")
                        final_module_tree = await self.generate_parent_module_docs(
                            module_path, working_dir, module_tree
                        )

                    # Store summary for downstream modules
                    summary = self._extract_module_summary(docs_path, module_name)
                    if summary:
                        self.module_summaries[module_name] = summary

                    if os.path.exists(docs_path):
                        self._mark_module_completed(working_dir, metadata, module_key)
                    else:
                        logger.warning(f"Module processed but output file not found: {docs_path}")

                except Exception as e:
                    logger.error(f"Failed to process module {module_key}: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue

            # Progressive phase 2: skip overview and CODEBASE_MAP.md (only leaf docs)
            if self.config.progressive != 2:
                # Generate repo overview
                overview_key = "__overview__"
                overview_path = os.path.join(working_dir, OVERVIEW_FILENAME)
                if overview_key not in completed_modules or not os.path.exists(overview_path):
                    logger.info("Generating repository overview")
                    final_module_tree = await self.generate_parent_module_docs(
                        [], working_dir, module_tree
                    )
                    self._mark_module_completed(working_dir, metadata, overview_key)
                else:
                    logger.info("Skipping completed overview")

                # Generate CODEBASE_MAP.md index document
                logger.info("Generating CODEBASE_MAP.md index document")
                await self.generate_codebase_map_doc(working_dir, module_tree)

                # Post-process: add cross-document links
                logger.info("Post-processing: adding cross-document links")
                self.post_process_cross_links(working_dir, module_tree)
            else:
                logger.info("Progressive phase 2: skipping overview, CODEBASE_MAP.md, and cross-links")
        else:
            logger.info("Processing whole repo because repo can fit in the context window")
            repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
            final_module_tree = await self._process_module(
                repo_name, components, leaf_nodes, [], working_dir
            )

            file_manager.save_json(final_module_tree, os.path.join(working_dir, MODULE_TREE_FILENAME))

            repo_overview_path = os.path.join(working_dir, f"{repo_name}.md")
            if os.path.exists(repo_overview_path):
                os.rename(repo_overview_path, os.path.join(working_dir, OVERVIEW_FILENAME))

        return working_dir

    async def generate_parent_module_docs(self, module_path: List[str],
                                        working_dir: str, module_tree: Dict[str, Any]) -> Dict[str, Any]:
        """Generate documentation for a parent module based on its children's documentation."""
        module_name = module_path[-1] if module_path else os.path.basename(os.path.normpath(self.config.repo_path))

        logger.info(f"Generating parent documentation for: {module_name}")

        parent_docs_path = os.path.join(working_dir, f"{module_name if module_path else OVERVIEW_FILENAME.replace('.md', '')}.md")

        if not self.config.no_cache:
            overview_docs_path = os.path.join(working_dir, OVERVIEW_FILENAME)
            if os.path.exists(overview_docs_path) and not module_path:
                logger.info(f"Overview docs already exists at {overview_docs_path}")
                return module_tree

            if os.path.exists(parent_docs_path) and module_path:
                logger.info(f"Parent docs already exists at {parent_docs_path}")
                return module_tree

        # Create repo structure with 1-depth children docs and target indicator
        repo_structure = self.build_overview_structure(module_tree, module_path, working_dir)

        structure_json = json.dumps(repo_structure, indent=4)
        if module_path:
            prompt = MODULE_OVERVIEW_PROMPT.format(module_name=module_name, repo_structure=structure_json)
        else:
            # Load summary metrics from codebase_map.json for repo overview
            summary_metrics_section = ""
            codebase_map_path = os.path.join(working_dir, "codebase_map.json")
            if os.path.exists(codebase_map_path):
                try:
                    codebase_map = file_manager.load_json(codebase_map_path)
                    summary_metrics = codebase_map.get("summary_metrics", {})
                    summary_metrics_section = format_summary_metrics_section(summary_metrics)
                except Exception as e:
                    logger.warning(f"Could not load codebase_map.json for summary metrics: {e}")
            prompt = REPO_OVERVIEW_PROMPT.format(
                repo_name=module_name,
                repo_structure=structure_json,
                summary_metrics_section=summary_metrics_section,
            )

        try:
            parent_docs = await self._call_llm(prompt)

            if "<OVERVIEW>" not in parent_docs or "</OVERVIEW>" not in parent_docs:
                raise ValueError(f"LLM response missing OVERVIEW tags for module {module_name}")
            parent_content = parent_docs.split("<OVERVIEW>")[1].split("</OVERVIEW>")[0].strip()
            file_manager.save_text(parent_content, parent_docs_path)

            logger.debug(f"Successfully generated parent documentation for: {module_name}")
            return module_tree

        except Exception as e:
            logger.error(f"Error generating parent documentation for {module_name}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    async def generate_codebase_map_doc(self, working_dir: str, module_tree: dict) -> None:
        """Generate CODEBASE_MAP.md — a comprehensive index document for the codebase."""
        codebase_map_doc_path = os.path.join(working_dir, "CODEBASE_MAP.md")

        if not self.config.no_cache and os.path.exists(codebase_map_doc_path):
            logger.info(f"CODEBASE_MAP.md already exists at {codebase_map_doc_path}")
            return

        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
        module_tree_text = json.dumps(module_tree, indent=2)

        # Load codebase metrics from codebase_map.json
        codebase_metrics = "{}"
        codebase_map_json_path = os.path.join(working_dir, "codebase_map.json")
        if os.path.exists(codebase_map_json_path):
            try:
                codebase_map_data = file_manager.load_json(codebase_map_json_path)
                codebase_metrics = json.dumps(codebase_map_data, indent=2)
            except Exception as e:
                logger.warning(f"Could not load codebase_map.json: {e}")

        # List all generated docs
        generated_docs = []
        try:
            for f in sorted(os.listdir(working_dir)):
                if f.endswith('.md'):
                    generated_docs.append(f"- `{f}`")
        except Exception as e:
            logger.warning(f"Could not list docs: {e}")
        generated_docs_list = "\n".join(generated_docs) if generated_docs else "(no docs found)"

        prompt = CODEBASE_MAP_PROMPT.format(
            repo_name=repo_name,
            module_tree=module_tree_text,
            codebase_metrics=codebase_metrics,
            generated_docs_list=generated_docs_list,
        )

        try:
            result = await self._call_llm(prompt)

            if "<CODEBASE_MAP>" not in result or "</CODEBASE_MAP>" not in result:
                raise ValueError("LLM response missing CODEBASE_MAP tags")
            content = result.split("<CODEBASE_MAP>")[1].split("</CODEBASE_MAP>")[0].strip()
            file_manager.save_text(content, codebase_map_doc_path)
            logger.info(f"Generated CODEBASE_MAP.md at {codebase_map_doc_path}")

        except Exception as e:
            logger.error(f"Error generating CODEBASE_MAP.md: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")

    # ── Main pipeline ──

    async def run(self, on_progress: Optional[Callable] = None) -> None:
        """Run the complete documentation generation pipeline.

        Supports resume: reads metadata.json to skip completed stages and modules.
        On crash, re-running will pick up where it left off.

        Args:
            on_progress: Optional callback(stage, stage_name, progress, message)
        """
        def progress(stage, name, pct, msg):
            if on_progress:
                on_progress(stage, name, pct, msg)

        try:
            working_dir = os.path.abspath(self.config.docs_dir)
            file_manager.ensure_directory(working_dir)

            # Load existing metadata for resume
            metadata = self._load_metadata(working_dir) if not self.config.no_cache else {}

            # Stage 1: Dependency Analysis (always re-run, fast AST parsing)
            progress(1, "Dependency Analysis", 0.2, "Parsing source files...")
            components, leaf_nodes = self.graph_builder.build_dependency_graph()
            progress(1, "Dependency Analysis", 1.0, f"Found {len(leaf_nodes)} leaf nodes")

            # Initialize/update metadata with current run info
            metadata = self._init_metadata(metadata, components, len(leaf_nodes))
            self._save_metadata(working_dir, metadata)

            # Generate codebase map
            circular_deps = self.graph_builder.circular_deps
            temporal_couplings = self.graph_builder.temporal_couplings

            try:
                from codewiki.src.be.arch_rules import evaluate_rules
                arch_violations = evaluate_rules(components, circular_deps, temporal_couplings)
            except ImportError:
                logger.debug("arch_rules module not available, skipping architectural rule evaluation")
                arch_violations = []

            from codewiki.src.be.codebase_map_generator import generate_codebase_map
            generate_codebase_map(
                components, working_dir, self.commit_id, self.config.repo_path,
                circular_deps, temporal_couplings, arch_violations
            )

            from codewiki.src.be.graph_viewer_generator import generate_graph_viewer
            generate_graph_viewer(working_dir)

            self._mark_stage_completed(working_dir, metadata, "dependency_analysis")

            # Analysis-only mode: stop after static analysis
            if self.config.analysis_only:
                self._finalize_metadata(working_dir, metadata)
                progress(4, "Finalization", 1.0, "Analysis complete")
                return

            # Stage 2: Module Clustering
            progress(2, "Module Clustering", 0.3, "Clustering modules...")
            initial_module_tree_path = os.path.join(working_dir, INITIAL_MODULE_TREE_FILENAME)
            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)

            if os.path.exists(initial_module_tree_path) and not self.config.no_cache:
                module_tree = file_manager.load_json(initial_module_tree_path) or {}
            else:
                module_tree = await cluster_modules(leaf_nodes, components, self.config, call_llm_fn=agent_sdk_call_llm)
                file_manager.save_json(module_tree, initial_module_tree_path)

            file_manager.save_json(module_tree, module_tree_path)
            self._mark_stage_completed(working_dir, metadata, "module_clustering")
            progress(2, "Module Clustering", 1.0, f"Created {len(module_tree)} modules")

            # Progressive phase 1: stop after analysis + module tree
            if self.config.progressive == 1:
                self._finalize_metadata(working_dir, metadata)
                progress(4, "Finalization", 1.0, "Progressive phase 1 complete (analysis + module tree)")
                return

            # Cache filtering: determine which components changed
            cache = None
            changed_ids = None
            if not self.config.no_cache:
                from codewiki.src.be.content_cache import get_changed_components, save_cache
                changed_components, cache = get_changed_components(components, working_dir)

                if not changed_components:
                    # No files changed. Check if all modules are completed.
                    completed = self._get_completed_modules(metadata)
                    processing_order = self.get_processing_order(module_tree)
                    all_module_keys = {"/".join(p) for p, _ in processing_order}
                    all_module_keys.add("__overview__")

                    if all_module_keys.issubset(completed):
                        logger.info("All components unchanged and all modules completed (cache hit).")
                        save_cache(working_dir, cache)
                        self._finalize_metadata(working_dir, metadata)
                        progress(4, "Finalization", 1.0, "Complete (cached)")
                        return
                    else:
                        pending = all_module_keys - completed
                        logger.info(f"No files changed but {len(pending)} module(s) incomplete. Resuming generation.")
                        changed_ids = set()  # No files changed, but need to process incomplete modules
                else:
                    changed_ids = set(changed_components.keys())
                    logger.info(f"{len(changed_ids)} changed component(s) detected")

            # Stage 3: Documentation Generation
            progress(3, "Documentation Generation", 0.1, "Generating module documentation...")
            await self.generate_module_documentation(
                components, leaf_nodes, module_tree, changed_ids, metadata, working_dir
            )
            self._mark_stage_completed(working_dir, metadata, "documentation_generation")

            # Stage 4: Finalization
            progress(4, "Finalization", 0.5, "Creating metadata...")
            self._finalize_metadata(working_dir, metadata)

            # Save content cache AFTER finalization (not before)
            if cache is not None:
                save_cache(working_dir, cache)

            progress(4, "Finalization", 1.0, "Complete")

        except Exception as e:
            logger.error(f"Documentation generation failed: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
