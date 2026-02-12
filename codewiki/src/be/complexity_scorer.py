import re
import logging
from typing import Dict
from codewiki.src.be.dependency_analyzer.models.core import Node

logger = logging.getLogger(__name__)

BRANCH_PATTERNS = re.compile(
    r'\b(if|elif|else|switch|case|catch)\b'
)
LOOP_PATTERNS = re.compile(
    r'\b(for|while|forEach|\.map\(|\.filter\(|\.reduce\()\b'
)


def _estimate_nesting_depth(source: str) -> int:
    """Estimate max nesting depth from indentation."""
    max_depth = 0
    for line in source.split('\n'):
        stripped = line.lstrip()
        if not stripped:
            continue
        indent = len(line) - len(stripped)
        # Approximate: 4 spaces or 1 tab = 1 level
        depth = indent // 4 if '\t' not in line else line.count('\t')
        max_depth = max(max_depth, depth)
    return max_depth


def _compute_raw_complexity(source: str) -> float:
    """Compute raw complexity score for source code."""
    branches = len(BRANCH_PATTERNS.findall(source))
    loops = len(LOOP_PATTERNS.findall(source))
    nesting = _estimate_nesting_depth(source)
    line_count = len([line for line in source.split('\n') if line.strip()])

    return branches + (loops * 2) + (nesting * 1.5) + (line_count / 20)


def compute_complexity_scores(components: Dict[str, Node]) -> None:
    """Compute complexity scores for all components and annotate in-place."""
    raw_scores = {}

    for comp_id, node in components.items():
        if node.source_code:
            raw_scores[comp_id] = _compute_raw_complexity(node.source_code)
        else:
            raw_scores[comp_id] = 0.0

    if not raw_scores:
        return

    # Normalize to 0-100
    min_score = min(raw_scores.values())
    max_score = max(raw_scores.values())
    score_range = max_score - min_score

    for comp_id, raw_score in raw_scores.items():
        if score_range > 0:
            normalized = ((raw_score - min_score) / score_range) * 100
        else:
            normalized = 0.0
        components[comp_id].complexity_score = round(normalized, 2)
