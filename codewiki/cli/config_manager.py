"""
Configuration manager with keyring integration for secure credential storage.
"""

import json
from pathlib import Path
from typing import Optional
import keyring
from keyring.errors import KeyringError

from codewiki.cli.models.config import Configuration
from codewiki.cli.utils.errors import ConfigurationError, FileSystemError
from codewiki.cli.utils.fs import ensure_directory, safe_write, safe_read


# Keyring configuration
KEYRING_SERVICE = "codewiki"

# Configuration file location
CONFIG_DIR = Path.home() / ".codewiki"
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_VERSION = "1.0"


class ConfigManager:
    """
    Manages CodeWiki configuration.

    Storage:
        - Settings: ~/.codewiki/config.json
    """

    def __init__(self):
        """Initialize the configuration manager."""
        self._config: Optional[Configuration] = None
        self._keyring_available = self._check_keyring_available()

    def _check_keyring_available(self) -> bool:
        """Check if system keyring is available."""
        try:
            keyring.get_password(KEYRING_SERVICE, "__test__")
            return True
        except KeyringError:
            return False

    def load(self) -> bool:
        """
        Load configuration from file.

        Returns:
            True if configuration exists, False otherwise
        """
        # Load from JSON file
        if not CONFIG_FILE.exists():
            return False

        try:
            content = safe_read(CONFIG_FILE)
            data = json.loads(content)

            # Validate version
            if data.get('version') != CONFIG_VERSION:
                # Could implement migration here
                pass

            self._config = Configuration.from_dict(data)

            return True
        except (json.JSONDecodeError, FileSystemError) as e:
            raise ConfigurationError(f"Failed to load configuration: {e}")

    def save(
        self,
        main_model: Optional[str] = None,
        cluster_model: Optional[str] = None,
        default_output: Optional[str] = None,
        max_tokens: Optional[int] = None,
        max_token_per_module: Optional[int] = None,
        max_token_per_leaf_module: Optional[int] = None,
        max_depth: Optional[int] = None
    ):
        """
        Save configuration to file.

        Args:
            main_model: Primary model
            cluster_model: Clustering model
            default_output: Default output directory
            max_tokens: Maximum tokens for LLM response
            max_token_per_module: Maximum tokens per module for clustering
            max_token_per_leaf_module: Maximum tokens per leaf module
            max_depth: Maximum depth for hierarchical decomposition
        """
        # Ensure config directory exists
        try:
            ensure_directory(CONFIG_DIR)
        except FileSystemError as e:
            raise ConfigurationError(f"Cannot create config directory: {e}")

        # Load existing config or create new
        if self._config is None:
            if CONFIG_FILE.exists():
                self.load()
            else:
                from codewiki.cli.models.config import AgentInstructions
                self._config = Configuration(
                    main_model="opus",
                    cluster_model="opus",
                    default_output="docs",
                    agent_instructions=AgentInstructions()
                )

        # Update fields if provided
        if main_model is not None:
            self._config.main_model = main_model
        if cluster_model is not None:
            self._config.cluster_model = cluster_model
        if default_output is not None:
            self._config.default_output = default_output
        if max_tokens is not None:
            self._config.max_tokens = max_tokens
        if max_token_per_module is not None:
            self._config.max_token_per_module = max_token_per_module
        if max_token_per_leaf_module is not None:
            self._config.max_token_per_leaf_module = max_token_per_leaf_module
        if max_depth is not None:
            self._config.max_depth = max_depth

        # Validate configuration (only if base fields are set)
        if self._config.main_model and self._config.cluster_model:
            self._config.validate()

        # Save config to JSON
        config_data = {
            "version": CONFIG_VERSION,
            **self._config.to_dict()
        }

        try:
            safe_write(CONFIG_FILE, json.dumps(config_data, indent=2))
        except FileSystemError as e:
            raise ConfigurationError(f"Failed to save configuration: {e}")

    def get_config(self) -> Optional[Configuration]:
        """
        Get current configuration.

        Returns:
            Configuration object or None if not loaded
        """
        return self._config

    def is_configured(self) -> bool:
        """
        Check if configuration is complete and valid.

        Returns:
            True if configured, False otherwise
        """
        if self._config is None:
            return False

        # Check if config is complete
        return self._config.is_complete()

    def clear(self):
        """Clear all configuration."""
        # Delete config file
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()

        self._config = None

    @property
    def keyring_available(self) -> bool:
        """Check if keyring is available."""
        return self._keyring_available

    @property
    def config_file_path(self) -> Path:
        """Get configuration file path."""
        return CONFIG_FILE
