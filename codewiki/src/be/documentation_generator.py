import logging
import os
import json
from typing import Dict, List, Any, Optional, Callable
from copy import deepcopy
import traceback

logger = logging.getLogger(__name__)

from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.prompt_template import (
    REPO_OVERVIEW_PROMPT,
    MODULE_OVERVIEW_PROMPT,
)
from codewiki.src.be.cluster_modules import cluster_modules
from codewiki.src.config import (
    Config,
    INITIAL_MODULE_TREE_FILENAME,
    MODULE_TREE_FILENAME,
    OVERVIEW_FILENAME
)
from codewiki.src.utils import file_manager
from codewiki.src.be.agent_orchestrator import AgentOrchestrator


class DocumentationGenerator:
    """Main documentation generation orchestrator."""

    def __init__(self, config: Config, commit_id: str = None):
        self.config = config
        self.commit_id = commit_id
        self.graph_builder = DependencyGraphBuilder(config)
        self.agent_orchestrator = AgentOrchestrator(config)

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

    def create_documentation_metadata(self, working_dir: str, components: Dict[str, Any], num_leaf_nodes: int):
        """Create a metadata file with documentation generation information."""
        from datetime import datetime

        metadata = {
            "generation_info": {
                "timestamp": datetime.now().isoformat(),
                "main_model": self.config.main_model,
                "generator_version": "1.0.1",
                "repo_path": self.config.repo_path,
                "commit_id": self.commit_id
            },
            "statistics": {
                "total_components": len(components),
                "leaf_nodes": num_leaf_nodes,
                "max_depth": self.config.max_depth
            },
            "files_generated": [
                "overview.md",
                "module_tree.json",
                "initial_module_tree.json",
                "codebase_map.json",
                "graph.html"
            ]
        }

        try:
            for file_path in os.listdir(working_dir):
                if file_path.endswith('.md') and file_path not in metadata["files_generated"]:
                    metadata["files_generated"].append(file_path)
        except Exception as e:
            logger.warning(f"Could not list generated files: {e}")

        metadata_path = os.path.join(working_dir, "metadata.json")
        file_manager.save_json(metadata, metadata_path)


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

    async def generate_module_documentation(self, components: Dict[str, Any], leaf_nodes: List[str], module_tree: Dict[str, Any], changed_ids: set = None) -> str:
        """Generate documentation for all modules using dynamic programming approach."""
        working_dir = os.path.abspath(self.config.docs_dir)
        file_manager.ensure_directory(working_dir)

        # Get processing order from the initial module tree
        processing_order = self.get_processing_order(module_tree)

        # Process modules in dependency order
        final_module_tree = module_tree
        processed_modules = set()

        if len(module_tree) > 0:
            for module_path, module_name in processing_order:
                try:
                    # Get the module info from the tree
                    module_info = module_tree
                    for path_part in module_path:
                        module_info = module_info[path_part]
                        if path_part != module_path[-1]:
                            module_info = module_info.get("children", {})

                    module_key = "/".join(module_path)
                    if module_key in processed_modules:
                        continue

                    if changed_ids is not None:
                        module_component_ids = set(module_info.get("components", []))
                        if not module_component_ids.intersection(changed_ids):
                            docs_path = os.path.join(working_dir, f"{module_name}.md")
                            if os.path.exists(docs_path):
                                logger.info(f"Skipping unchanged module: {module_key}")
                                processed_modules.add(module_key)
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

                    processed_modules.add(module_key)

                except Exception as e:
                    logger.error(f"Failed to process module {module_key}: {str(e)}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue

            # Generate repo overview
            logger.info(f"Generating repository overview")
            final_module_tree = await self.generate_parent_module_docs(
                [], working_dir, module_tree
            )
        else:
            logger.info(f"Processing whole repo because repo can fit in the context window")
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
        module_name = module_path[-1] if len(module_path) >= 1 else os.path.basename(os.path.normpath(self.config.repo_path))

        logger.info(f"Generating parent documentation for: {module_name}")

        parent_docs_path = os.path.join(working_dir, f"{module_name if len(module_path) >= 1 else OVERVIEW_FILENAME.replace('.md', '')}.md")

        if not self.config.no_cache:
            overview_docs_path = os.path.join(working_dir, OVERVIEW_FILENAME)
            if os.path.exists(overview_docs_path):
                logger.info(f"Overview docs already exists at {overview_docs_path}")
                return module_tree

            if os.path.exists(parent_docs_path):
                logger.info(f"Parent docs already exists at {parent_docs_path}")
                return module_tree

        # Create repo structure with 1-depth children docs and target indicator
        repo_structure = self.build_overview_structure(module_tree, module_path, working_dir)

        prompt = MODULE_OVERVIEW_PROMPT.format(
            module_name=module_name,
            repo_structure=json.dumps(repo_structure, indent=4)
        ) if len(module_path) >= 1 else REPO_OVERVIEW_PROMPT.format(
            repo_name=module_name,
            repo_structure=json.dumps(repo_structure, indent=4)
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
            logger.error(f"Error generating parent documentation for {module_name}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    async def run(self, on_progress: Optional[Callable] = None) -> None:
        """Run the complete documentation generation pipeline.

        Args:
            on_progress: Optional callback(stage, stage_name, progress, message)
        """
        def progress(stage, name, pct, msg):
            if on_progress:
                on_progress(stage, name, pct, msg)

        try:
            # Stage 1: Dependency Analysis
            progress(1, "Dependency Analysis", 0.2, "Parsing source files...")
            components, leaf_nodes = self.graph_builder.build_dependency_graph()
            progress(1, "Dependency Analysis", 1.0, f"Found {len(leaf_nodes)} leaf nodes")

            working_dir = os.path.abspath(self.config.docs_dir)
            file_manager.ensure_directory(working_dir)

            # Generate codebase map
            circular_deps = self.graph_builder.circular_deps
            temporal_couplings = self.graph_builder.temporal_couplings

            # Evaluate architectural rules
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

            # Generate interactive graph viewer
            from codewiki.src.be.graph_viewer_generator import generate_graph_viewer
            generate_graph_viewer(working_dir)

            # Analysis-only mode: stop after static analysis
            if self.config.analysis_only:
                self.create_documentation_metadata(working_dir, components, len(leaf_nodes))
                progress(4, "Finalization", 1.0, "Analysis complete")
                return

            # Stage 2: Module Clustering
            progress(2, "Module Clustering", 0.3, "Clustering modules...")
            initial_module_tree_path = os.path.join(working_dir, INITIAL_MODULE_TREE_FILENAME)
            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)

            if os.path.exists(initial_module_tree_path) and not self.config.no_cache:
                module_tree = file_manager.load_json(initial_module_tree_path)
            else:
                if self.config.use_agent_sdk:
                    from codewiki.src.be.claude_agent_sdk_adapter import agent_sdk_cluster
                    module_tree = await agent_sdk_cluster(leaf_nodes, components, self.config)
                else:
                    module_tree = cluster_modules(leaf_nodes, components, self.config)
                file_manager.save_json(module_tree, initial_module_tree_path)

            file_manager.save_json(module_tree, module_tree_path)
            progress(2, "Module Clustering", 1.0, f"Created {len(module_tree)} modules")

            # Cache filtering (AFTER clustering, uses full components)
            cache = None
            changed_ids = None
            if not self.config.no_cache:
                from codewiki.src.be.content_cache import get_changed_components, save_cache
                changed_components, cache = get_changed_components(components, working_dir)
                save_cache(working_dir, cache)
                if not changed_components:
                    logger.info("All components unchanged (cache hit). Skipping generation.")
                    self.create_documentation_metadata(working_dir, components, len(leaf_nodes))
                    progress(4, "Finalization", 1.0, "Complete (cached)")
                    return
                changed_ids = set(changed_components.keys())

            # Stage 3: Documentation Generation
            progress(3, "Documentation Generation", 0.1, "Generating module documentation...")
            await self.generate_module_documentation(components, leaf_nodes, module_tree, changed_ids)

            # Stage 4: Finalization
            progress(4, "Finalization", 0.5, "Creating metadata...")
            self.create_documentation_metadata(working_dir, components, len(leaf_nodes))

            progress(4, "Finalization", 1.0, "Complete")

        except Exception as e:
            logger.error(f"Documentation generation failed: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
