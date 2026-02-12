import hashlib
import json
import os
import logging
from typing import Dict, Tuple
from codewiki.src.be.dependency_analyzer.models.core import Node

logger = logging.getLogger(__name__)

CACHE_FILENAME = '.codewiki_cache.json'


def compute_file_hash(filepath: str) -> str:
    """Compute SHA-256 hash of file content."""
    sha256 = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
    except (IOError, OSError) as e:
        logger.warning(f"Cannot hash file {filepath}: {e}")
        return ''


def load_cache(output_dir: str) -> dict:
    """Load cache from output directory."""
    cache_path = os.path.join(output_dir, CACHE_FILENAME)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load cache: {e}")
    return {}


def save_cache(output_dir: str, cache: dict) -> None:
    """Save cache to output directory."""
    cache_path = os.path.join(output_dir, CACHE_FILENAME)
    try:
        with open(cache_path, 'w') as f:
            json.dump(cache, f, indent=2)
    except IOError as e:
        logger.warning(f"Failed to save cache: {e}")


def get_changed_components(
    components: Dict[str, Node],
    output_dir: str
) -> Tuple[Dict[str, Node], dict]:
    """
    Compare component file hashes with cache and return only changed components.

    Returns:
        Tuple of (changed_components, updated_cache)
    """
    old_cache = load_cache(output_dir)
    file_hashes = old_cache.get('file_hashes', {})
    new_cache = {'file_hashes': {}}

    changed = {}
    unchanged_count = 0

    # Group components by file to avoid hashing same file multiple times
    file_to_components: Dict[str, list] = {}
    for comp_id, node in components.items():
        fp = node.file_path
        if fp not in file_to_components:
            file_to_components[fp] = []
        file_to_components[fp].append(comp_id)

    for filepath, comp_ids in file_to_components.items():
        current_hash = compute_file_hash(filepath)
        new_cache['file_hashes'][filepath] = current_hash

        cached_hash = file_hashes.get(filepath)

        if current_hash and current_hash == cached_hash:
            # File unchanged, skip all components from this file
            unchanged_count += len(comp_ids)
        else:
            # File changed or new, include all components
            for comp_id in comp_ids:
                changed[comp_id] = components[comp_id]

    if unchanged_count > 0:
        logger.info(f"Cache hit: {unchanged_count} components unchanged, {len(changed)} changed")

    return changed, new_cache
