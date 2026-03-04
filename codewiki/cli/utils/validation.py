"""
Validation utilities for CLI inputs and configuration.
"""

from pathlib import Path
from typing import Optional, List, Tuple

from codewiki.cli.utils.errors import ConfigurationError, RepositoryError


def validate_model_name(model: str) -> str:
    """
    Validate model name format.
    
    Args:
        model: Model name to validate
        
    Returns:
        Validated model name
        
    Raises:
        ConfigurationError: If model name is invalid
    """
    if not model or not model.strip():
        raise ConfigurationError("Model name cannot be empty")
    
    return model.strip()


def validate_output_directory(path: str) -> Path:
    """
    Validate output directory path.
    
    Args:
        path: Directory path to validate
        
    Returns:
        Validated Path object
        
    Raises:
        ConfigurationError: If path is invalid
    """
    if not path or not path.strip():
        raise ConfigurationError("Output directory cannot be empty")
    
    try:
        resolved_path = Path(path).expanduser().resolve()
        
        # Check if path is writable (or parent is writable if path doesn't exist)
        if resolved_path.exists():
            if not resolved_path.is_dir():
                raise ConfigurationError(
                    f"Output path exists but is not a directory: {path}"
                )
        
        return resolved_path
    except Exception as e:
        raise ConfigurationError(f"Invalid output directory path: {path}\nError: {e}")


def validate_repository_path(path: Path) -> Path:
    """
    Validate repository path exists and contains code files.
    
    Args:
        path: Repository path to validate
        
    Returns:
        Validated Path object
        
    Raises:
        RepositoryError: If repository is invalid
    """
    path = Path(path).expanduser().resolve()
    
    if not path.exists():
        raise RepositoryError(f"Repository path does not exist: {path}")
    
    if not path.is_dir():
        raise RepositoryError(f"Repository path is not a directory: {path}")
    
    return path


def detect_supported_languages(directory: Path) -> List[Tuple[str, int]]:
    """
    Detect supported programming languages in a directory.
    
    Args:
        directory: Directory to scan
        
    Returns:
        List of (language, file_count) tuples
    """
    language_extensions = {
        'Python': ['.py'],
        'Java': ['.java'],
        'JavaScript': ['.js', '.jsx'],
        'TypeScript': ['.ts', '.tsx'],
        'C': ['.c', '.h'],
        'C++': ['.cpp', '.hpp', '.cc', '.hh', '.cxx', '.hxx'],
        'C#': ['.cs'],
        'PHP': ['.php', '.phtml', '.inc'],
        'Go': ['.go'],
    }
    
    # Directories to exclude from counting
    excluded_dirs = {
        'node_modules', '__pycache__', '.git', 'build', 'dist', 
        '.venv', 'venv', 'env', '.env', 'target', 'bin', 'obj',
        '.pytest_cache', '.mypy_cache', '.tox', 'coverage',
        'htmlcov', '.eggs', '*.egg-info', 'vendor', 'bower_components',
        '.idea', '.vscode', '.gradle', '.mvn'
    }
    
    def should_exclude_file(file_path: Path) -> bool:
        """Check if file is in an excluded directory."""
        parts = file_path.parts
        return any(excluded_dir in parts for excluded_dir in excluded_dirs)
    
    # Build reverse mapping: extension -> language for O(1) lookup
    ext_to_language = {}
    for language, extensions in language_extensions.items():
        for ext in extensions:
            ext_to_language[ext] = language

    # Single filesystem traversal
    language_counts = {}
    for f in directory.rglob("*"):
        if not f.is_file() or should_exclude_file(f):
            continue
        ext = f.suffix
        if ext in ext_to_language:
            lang = ext_to_language[ext]
            language_counts[lang] = language_counts.get(lang, 0) + 1
    
    # Sort by count descending
    return sorted(language_counts.items(), key=lambda x: x[1], reverse=True)


def is_top_tier_model(model: str) -> bool:
    """
    Check if a model is considered top-tier for clustering.
    
    Args:
        model: Model name
        
    Returns:
        True if top-tier, False otherwise
    """
    top_tier_models = [
        'claude-opus',
        'claude-sonnet',
        'gpt-4',
        'gpt-5',
        'gemini-2.5',
    ]
    
    model_lower = model.lower()
    return any(tier in model_lower for tier in top_tier_models)


