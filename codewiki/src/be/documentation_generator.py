import logging
import os
import json
from typing import Dict, List, Any
from copy import deepcopy
import traceback

# Configure logging and monitoring
logger = logging.getLogger(__name__)

# Local imports
from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder
from codewiki.src.be.llm_services import call_llm
from codewiki.src.be.prompt_template import (
    REPO_OVERVIEW_PROMPT,
    MODULE_OVERVIEW_PROMPT,
)
from codewiki.src.be.cluster_modules import cluster_modules
from codewiki.src.config import (
    Config,
    FIRST_MODULE_TREE_FILENAME,
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
                              module_path, working_dir):
        """Dispatch module processing to Agent SDK or pydantic-ai orchestrator."""
        if self.config.use_agent_sdk:
            from codewiki.src.be.claude_agent_sdk_adapter import agent_sdk_process_module
            return await agent_sdk_process_module(
                module_name, components, component_ids,
                module_path, working_dir, self.config
            )
        return await self.agent_orchestrator.process_module(
            module_name, components, component_ids, module_path, working_dir
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
                "first_module_tree.json",
                "codebase_map.json",
                "graph.html"
            ]
        }
        
        # Add generated markdown files to the metadata
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

                # If this module has children, process them first
                if module_info.get("children") and isinstance(module_info["children"], dict) and module_info["children"]:
                    collect_modules(module_info["children"], current_path)
                    # Add this parent module after its children
                    processing_order.append((current_path, module_name))
                else:
                    # This is a leaf module, add it immediately
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
                logger.warning(f"Module docs not found at {os.path.join(working_dir, f"{child_name}.md")}")
                child_info["docs"] = ""

        return processed_module_tree

    async def generate_module_documentation(self, components: Dict[str, Any], leaf_nodes: List[str]) -> str:
        """Generate documentation for all modules using dynamic programming approach."""
        # Prepare output directory
        working_dir = os.path.abspath(self.config.docs_dir)
        file_manager.ensure_directory(working_dir)

        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
        module_tree = file_manager.load_json(module_tree_path)
        first_module_tree = file_manager.load_json(first_module_tree_path)
        
        # Get processing order (leaf modules first)
        processing_order = self.get_processing_order(first_module_tree)

        
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
                        if path_part != module_path[-1]:  # Not the last part
                            module_info = module_info.get("children", {})
                    
                    # Skip if already processed
                    module_key = "/".join(module_path)
                    if module_key in processed_modules:
                        continue
                    
                    # Process the module
                    if self.is_leaf_module(module_info):
                        logger.info(f"Processing leaf module: {module_key}")
                        final_module_tree = await self._process_module(
                            module_name, components, module_info["components"],
                            module_path, working_dir
                        )
                    else:
                        logger.info(f"Processing parent module: {module_key}")
                        final_module_tree = await self.generate_parent_module_docs(
                            module_path, working_dir
                        )
                    
                    processed_modules.add(module_key)
                    
                except Exception as e:
                    logger.error(f"Failed to process module {module_key}: {str(e)}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue

            # Generate repo overview
            logger.info(f"Generating repository overview")
            final_module_tree = await self.generate_parent_module_docs(
                [], working_dir
            )
        else:
            logger.info(f"Processing whole repo because repo can fit in the context window")
            repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
            final_module_tree = await self._process_module(
                repo_name, components, leaf_nodes, [], working_dir
            )

            # save final_module_tree to module_tree.json
            file_manager.save_json(final_module_tree, os.path.join(working_dir, MODULE_TREE_FILENAME))

            # rename repo_name.md to overview.md
            repo_overview_path = os.path.join(working_dir, f"{repo_name}.md")
            if os.path.exists(repo_overview_path):
                os.rename(repo_overview_path, os.path.join(working_dir, OVERVIEW_FILENAME))
        
        return working_dir

    async def generate_parent_module_docs(self, module_path: List[str], 
                                        working_dir: str) -> Dict[str, Any]:
        """Generate documentation for a parent module based on its children's documentation."""
        module_name = module_path[-1] if len(module_path) >= 1 else os.path.basename(os.path.normpath(self.config.repo_path))

        logger.info(f"Generating parent documentation for: {module_name}")
        
        # Load module tree
        module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)
        module_tree = file_manager.load_json(module_tree_path)

        # check if overview docs already exists
        overview_docs_path = os.path.join(working_dir, OVERVIEW_FILENAME)
        if os.path.exists(overview_docs_path):
            logger.info(f"✓ Overview docs already exists at {overview_docs_path}")
            return module_tree

        # check if parent docs already exists
        parent_docs_path = os.path.join(working_dir, f"{module_name if len(module_path) >= 1 else OVERVIEW_FILENAME.replace('.md', '')}.md")
        if os.path.exists(parent_docs_path):
            logger.info(f"✓ Parent docs already exists at {parent_docs_path}")
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
            
            # Parse and save parent documentation
            parent_content = parent_docs.split("<OVERVIEW>")[1].split("</OVERVIEW>")[0].strip()
            file_manager.save_text(parent_content, parent_docs_path)
            
            logger.debug(f"Successfully generated parent documentation for: {module_name}")
            return module_tree
            
        except Exception as e:
            logger.error(f"Error generating parent documentation for {module_name}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    def _generate_codebase_map(self, components: Dict[str, Any], graph, working_dir: str, circular_deps=None) -> None:
        """Generate codebase_map.json with structural analysis data."""
        from datetime import datetime
        from collections import defaultdict

        # Build metadata
        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
        languages = set()
        for node in components.values():
            ext = os.path.splitext(node.relative_path)[1]
            if ext:
                languages.add(ext.lstrip('.'))

        # Community aggregation
        communities_map = defaultdict(lambda: {"node_count": 0, "hub_count": 0, "keywords": defaultdict(float)})
        for node in components.values():
            cid = node.community_id
            if cid >= 0:
                communities_map[cid]["node_count"] += 1
                if node.is_hub:
                    communities_map[cid]["hub_count"] += 1
                for kw, score in node.tfidf_keywords:
                    communities_map[cid]["keywords"][kw] += score

        communities = []
        for cid, info in sorted(communities_map.items()):
            top_kw = sorted(info["keywords"].items(), key=lambda x: x[1], reverse=True)[:10]
            communities.append({
                "id": cid,
                "node_count": info["node_count"],
                "hub_count": info["hub_count"],
                "tfidf_keywords": [[kw, round(s, 4)] for kw, s in top_kw]
            })

        # Build nodes and edges
        nodes = []
        edges = []
        hub_files = []
        instabilities = []

        for comp_id, node in components.items():
            nodes.append({
                "id": comp_id,
                "name": node.name,
                "type": node.component_type,
                "file_path": node.relative_path,
                "metrics": {
                    "pagerank": round(node.pagerank, 6),
                    "fan_in": node.fan_in,
                    "fan_out": node.fan_out,
                    "instability": round(node.instability, 4),
                    "is_hub": node.is_hub,
                    "complexity_score": round(node.complexity_score, 2),
                    "tfidf_keywords": node.tfidf_keywords
                },
                "community_id": node.community_id,
                "depends_on": list(node.depends_on)
            })

            for dep in node.depends_on:
                edges.append({"source": comp_id, "target": dep, "type": "depends_on"})

            if node.is_hub:
                hub_files.append(node.name)
            instabilities.append((node.name, node.instability))

        instabilities.sort(key=lambda x: x[1], reverse=True)

        # Use pre-computed circular dependencies if available
        if circular_deps is None:
            circular_deps = []

        codebase_map = {
            "version": "1.0",
            "metadata": {
                "project_name": repo_name,
                "generated_at": datetime.now().isoformat(),
                "commit_sha": self.commit_id,
                "languages": sorted(languages),
                "total_components": len(components)
            },
            "nodes": nodes,
            "edges": edges,
            "communities": communities,
            "summary_metrics": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "hub_files": hub_files,
                "most_unstable": [name for name, _ in instabilities[:5]],
                "most_stable": [name for name, _ in instabilities[-5:]],
                "circular_dependencies": circular_deps
            }
        }

        map_path = os.path.join(working_dir, "codebase_map.json")
        file_manager.save_json(codebase_map, map_path)
        logger.info(f"Generated codebase_map.json with {len(nodes)} nodes and {len(edges)} edges")

    async def run(self) -> None:
        """Run the complete documentation generation process using dynamic programming."""
        try:
            # Build dependency graph
            components, leaf_nodes, graph = self.graph_builder.build_dependency_graph()

            logger.debug(f"Found {len(leaf_nodes)} leaf nodes")

            working_dir = os.path.abspath(self.config.docs_dir)
            file_manager.ensure_directory(working_dir)

            # Generate codebase map (uses pre-computed circular deps)
            circular_deps = self.graph_builder.circular_deps
            self._generate_codebase_map(components, graph, working_dir, circular_deps=circular_deps)

            # Generate interactive graph viewer
            from codewiki.src.be.graph_viewer_generator import generate_graph_viewer
            generate_graph_viewer(working_dir)

            # Cluster modules (needs FULL components dict before cache filtering)
            first_module_tree_path = os.path.join(working_dir, FIRST_MODULE_TREE_FILENAME)
            module_tree_path = os.path.join(working_dir, MODULE_TREE_FILENAME)

            if os.path.exists(first_module_tree_path):
                logger.debug(f"Module tree found at {first_module_tree_path}")
                module_tree = file_manager.load_json(first_module_tree_path)
            else:
                logger.debug(f"Module tree not found at {module_tree_path}, clustering modules")
                module_tree = cluster_modules(leaf_nodes, components, self.config)
                file_manager.save_json(module_tree, first_module_tree_path)

            file_manager.save_json(module_tree, module_tree_path)

            logger.debug(f"Grouped components into {len(module_tree)} modules")

            # Content-hash caching: skip unchanged components
            cache = None
            if not self.config.no_cache:
                from codewiki.src.be.content_cache import get_changed_components, save_cache
                components, cache = get_changed_components(components, working_dir)
                leaf_nodes = [ln for ln in leaf_nodes if ln in components]
                if not components:
                    logger.info("All components unchanged (cache hit). Skipping generation.")
                    return

            # Generate module documentation using dynamic programming approach
            # This processes leaf modules first, then parent modules
            working_dir = await self.generate_module_documentation(components, leaf_nodes)

            # Create documentation metadata
            self.create_documentation_metadata(working_dir, components, len(leaf_nodes))

            # Save content cache after successful generation
            if cache is not None:
                save_cache(os.path.abspath(self.config.docs_dir), cache)

            logger.debug(f"Documentation generation completed successfully using dynamic programming!")
            logger.debug(f"Processing order: leaf modules → parent modules → repository overview")
            logger.debug(f"Documentation saved to: {working_dir}")

        except Exception as e:
            logger.error(f"Documentation generation failed: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise