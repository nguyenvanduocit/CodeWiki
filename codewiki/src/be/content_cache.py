"""
Content-based caching using SHA-256 file hashes.

Uses diskcache (SQLite-backed) for persistence, with fallback to JSON files.
"""
import hashlib
import os
import logging
from typing import Dict, Tuple

from codewiki.src.be.dependency_analyzer.models.core import Node

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = '.codewiki_cache'


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
        return hashlib.sha256(f"UNREADABLE:{filepath}:{e}".encode()).hexdigest()


def _get_cache(output_dir: str):
    """Get or create diskcache Cache instance."""
    try:
        import diskcache
        cache_dir = os.path.join(output_dir, CACHE_DIR_NAME)
        return diskcache.Cache(cache_dir, size_limit=100 * 1024 * 1024)  # 100MB limit
    except ImportError:
        logger.info("diskcache not available, using in-memory cache")
        return None


def load_cache(output_dir: str) -> dict:
    """Load file hashes from cache."""
    cache = _get_cache(output_dir)
    if cache is not None:
        try:
            hashes = cache.get('file_hashes', {})
            cache.close()
            return {'file_hashes': hashes} if hashes else {}
        except Exception as e:
            logger.warning(f"Failed to load diskcache: {e}")
            cache.close()

    # Fallback: try old JSON cache
    import json
    cache_path = os.path.join(output_dir, '.codewiki_cache.json')
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load JSON cache: {e}")
    return {}


def save_cache(output_dir: str, cache_data: dict) -> None:
    """Save file hashes to cache."""
    cache = _get_cache(output_dir)
    if cache is not None:
        try:
            cache.set('file_hashes', cache_data.get('file_hashes', {}))
            cache.close()
            return
        except Exception as e:
            logger.warning(f"Failed to save to diskcache: {e}")
            cache.close()

    # Fallback: save as JSON
    import json
    cache_path = os.path.join(output_dir, '.codewiki_cache.json')
    try:
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f, indent=2)
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
            unchanged_count += len(comp_ids)
        else:
            for comp_id in comp_ids:
                changed[comp_id] = components[comp_id]

    if unchanged_count > 0:
        logger.info(f"Cache hit: {unchanged_count} components unchanged, {len(changed)} changed")

    return changed, new_cache
