import json
import logging
import os
import traceback
from copy import deepcopy
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable

from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.prompt_template import (
    REPO_OVERVIEW_PROMPT,
    MODULE_OVERVIEW_PROMPT,
    format_summary_metrics_section,
)
from codewiki.src.be.cluster_modules import cluster_modules
from codewiki.src.config import (
    Config,
    INITIAL_MODULE_TREE_FILENAME,
    METADATA_FILENAME,
    MODULE_TREE_FILENAME,
    OVERVIEW_FILENAME
)
from codewiki.src.utils import file_manager
from codewiki.src.be.agent_orchestrator import AgentOrchestrator

logger = logging.getLogger(__name__)


class DocumentationGenerator:
    """Main documentation generation orchestrator."""

    def __init__(self, config: Config, commit_id: str = None):
        self.config = config
        self.commit_id = commit_id
        self.graph_builder = DependencyGraphBuilder(config)
        self.agent_orchestrator = AgentOrchestrator(config)

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
                              module_path, working_dir, module_tree):
        """Dispatch module processing to Agent SDK or pydantic-ai orchestrator."""
        if self.config.use_agent_sdk:
            from codewiki.src.be.claude_agent_sdk_adapter import agent_sdk_process_module
            return await agent_sdk_process_module(
                module_name, components, component_ids,
                module_path, working_dir, self.config, module_tree
            )
        return await self.agent_orchestrator.process_module(
            module_name, components, component_ids, module_path, working_dir, module_tree
        )

    async def _call_llm(self, prompt):
        """Dispatch LLM call to Agent SDK or standard call_llm."""
        if self.config.use_agent_sdk:
            from codewiki.src.be.claude_agent_sdk_adapter import agent_sdk_call_llm
            return await agent_sdk_call_llm(prompt, self.config)
        return call_llm(prompt, self.config)

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

    # ── Documentation generation ──

    async def generate_module_documentation(self, components: Dict[str, Any], leaf_nodes: List[str],
                                            module_tree: Dict[str, Any], changed_ids: set,
                                            metadata: dict, working_dir: str) -> str:
        """Generate documentation for all modules using dynamic programming approach.

        Uses metadata to track per-module completion for crash recovery.
        """
        file_manager.ensure_directory(working_dir)

        processing_order = self.get_processing_order(module_tree)
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

                    if self.is_leaf_module(module_info):
                        logger.info(f"Processing leaf module: {module_key}")
                        final_module_tree = await self._process_module(
                            module_name, components, module_info["components"],
                            module_path, working_dir, module_tree
                        )
                    else:
                        logger.info(f"Processing parent module: {module_key}")
                        final_module_tree = await self.generate_parent_module_docs(
                            module_path, working_dir, module_tree
                        )

                    if os.path.exists(docs_path):
                        self._mark_module_completed(working_dir, metadata, module_key)
                    else:
                        logger.warning(f"Module processed but output file not found: {docs_path}")

                except Exception as e:
                    logger.error(f"Failed to process module {module_key}: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue

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
        else:
            logger.info("Processing whole repo because repo can fit in the context window")
            repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
            final_module_tree = await self._process_module(
                repo_name, components, leaf_nodes, [], working_dir, module_tree
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
                if self.config.use_agent_sdk:
                    from codewiki.src.be.claude_agent_sdk_adapter import agent_sdk_cluster
                    module_tree = await agent_sdk_cluster(leaf_nodes, components, self.config)
                else:
                    module_tree = cluster_modules(leaf_nodes, components, self.config)
                file_manager.save_json(module_tree, initial_module_tree_path)

            file_manager.save_json(module_tree, module_tree_path)
            self._mark_stage_completed(working_dir, metadata, "module_clustering")
            progress(2, "Module Clustering", 1.0, f"Created {len(module_tree)} modules")

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
