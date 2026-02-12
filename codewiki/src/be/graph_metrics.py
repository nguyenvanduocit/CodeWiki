import logging
import networkx as nx
from typing import Dict, List, Tuple
from codewiki.src.be.dependency_analyzer.models.core import Node

logger = logging.getLogger(__name__)


def compute_graph_metrics(components: Dict[str, Node], graph: nx.DiGraph) -> List[Tuple[str, ...]]:
    """
    Compute graph metrics and annotate Node objects in-place.
    Returns list of circular dependencies found.
    """
    if not graph.nodes():
        return []

    # PageRank
    try:
        pagerank_scores = nx.pagerank(graph, alpha=0.85)
        for node_id, score in pagerank_scores.items():
            if node_id in components:
                components[node_id].pagerank = score
    except Exception as e:
        logger.warning(f"PageRank computation failed: {e}")

    # Fan-in / Fan-out
    for node_id in graph.nodes():
        if node_id in components:
            components[node_id].fan_in = graph.in_degree(node_id)
            components[node_id].fan_out = graph.out_degree(node_id)

    # Instability (Robert C. Martin)
    for node_id in graph.nodes():
        if node_id in components:
            node = components[node_id]
            total = node.fan_in + node.fan_out
            node.instability = node.fan_out / total if total > 0 else 0.0

    # Hub detection (top 10% by PageRank OR fan_in >= 3)
    if components:
        pagerank_values = sorted(
            [(nid, components[nid].pagerank) for nid in graph.nodes() if nid in components],
            key=lambda x: x[1], reverse=True
        )
        top_10_percent = max(1, len(pagerank_values) // 10)
        top_pagerank_ids = {nid for nid, _ in pagerank_values[:top_10_percent]}

        for node_id in graph.nodes():
            if node_id in components:
                components[node_id].is_hub = (
                    node_id in top_pagerank_ids or components[node_id].fan_in >= 3
                )

    # Louvain communities
    try:
        import community as community_louvain
        undirected = graph.to_undirected()
        if undirected.nodes():
            partition = community_louvain.best_partition(undirected, resolution=1.5)
            for node_id, comm_id in partition.items():
                if node_id in components:
                    components[node_id].community_id = comm_id
    except ImportError:
        logger.warning("python-louvain not installed, skipping community detection")
    except Exception as e:
        logger.warning(f"Community detection failed: {e}")

    # Circular dependency detection
    circular_deps = []
    try:
        cycles = list(nx.simple_cycles(graph))
        circular_deps = [tuple(cycle) for cycle in cycles[:100]]  # Limit to 100
        if circular_deps:
            logger.warning(f"Found {len(cycles)} circular dependencies")
    except Exception as e:
        logger.warning(f"Cycle detection failed: {e}")

    return circular_deps
