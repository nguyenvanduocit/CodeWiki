from typing import Dict, List, Any
import os
from codewiki.src.config import Config
from codewiki.src.be.dependency_analyzer.ast_parser import DependencyParser
from codewiki.src.be.dependency_analyzer.topo_sort import build_graph_from_components, get_leaf_nodes, _get_valid_leaf_types
from codewiki.src.utils import file_manager
import networkx as nx
from codewiki.src.be.graph_metrics import compute_graph_metrics
from codewiki.src.be.tfidf_keywords import compute_tfidf_keywords
from codewiki.src.be.complexity_scorer import compute_complexity_scores

import logging
logger = logging.getLogger(__name__)


class DependencyGraphBuilder:
    """Handles dependency analysis and graph building."""
    
    def __init__(self, config: Config):
        self.config = config
        self.graph = None
    
    def build_dependency_graph(self) -> tuple[Dict[str, Any], List[str], Any]:
        """
        Build and save dependency graph, returning components and leaf nodes.
        
        Returns:
            Tuple of (components, leaf_nodes, graph)
        """
        # Ensure output directory exists
        file_manager.ensure_directory(self.config.dependency_graph_dir)

        # Prepare dependency graph path
        repo_name = os.path.basename(os.path.normpath(self.config.repo_path))
        sanitized_repo_name = ''.join(c if c.isalnum() else '_' for c in repo_name)
        dependency_graph_path = os.path.join(
            self.config.dependency_graph_dir, 
            f"{sanitized_repo_name}_dependency_graph.json"
        )
        include_patterns = self.config.include_patterns or None
        exclude_patterns = self.config.exclude_patterns or None
        
        parser = DependencyParser(
            self.config.repo_path,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns
        )

        filtered_folders = None

        # Parse repository
        components = parser.parse_repository(filtered_folders)
        
        # Save dependency graph
        parser.save_dependency_graph(dependency_graph_path)
        
        # Build graph for traversal
        graph = build_graph_from_components(components)
        self.graph = graph

        # Convert adjacency dict to networkx DiGraph for metrics computation
        nx_graph = nx.DiGraph()
        for node_id, deps in graph.items():
            nx_graph.add_node(node_id)
            for dep in deps:
                nx_graph.add_edge(node_id, dep)

        # Compute graph metrics, TF-IDF keywords, and complexity scores
        self.circular_deps = compute_graph_metrics(components, nx_graph)
        compute_tfidf_keywords(components)
        compute_complexity_scores(components)

        # Get leaf nodes
        leaf_nodes = get_leaf_nodes(graph, components)

        valid_types = _get_valid_leaf_types(components)

        keep_leaf_nodes = []
        for leaf_node in leaf_nodes:
            if not isinstance(leaf_node, str) or leaf_node.strip() == "":
                logger.warning(f"Skipping invalid leaf node identifier: '{leaf_node}'")
                continue

            if leaf_node in components:
                if components[leaf_node].component_type in valid_types:
                    keep_leaf_nodes.append(leaf_node)
            else:
                logger.warning(f"Leaf node {leaf_node} not found in components, removing it")

        return components, keep_leaf_nodes, graph
