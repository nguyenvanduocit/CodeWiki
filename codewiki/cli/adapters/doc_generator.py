"""
CLI adapter for documentation generator backend.

This adapter wraps the existing backend documentation_generator.py
and provides CLI-specific functionality like progress reporting.
"""

from pathlib import Path
from typing import Dict, Any
import time
import asyncio
import json
import os
import logging
import sys


from codewiki.cli.utils.progress import ProgressTracker
from codewiki.cli.models.job import DocumentationJob, LLMConfig
from codewiki.cli.utils.errors import APIError

# Import backend modules
from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.config import Config as BackendConfig, set_cli_context


class CLIDocumentationGenerator:
    """
    CLI adapter for documentation generation with progress reporting.

    This class wraps the backend documentation generator and adds
    CLI-specific features like progress tracking and error handling.
    """

    def __init__(
        self,
        repo_path: Path,
        output_dir: Path,
        config: Dict[str, Any],
        verbose: bool = False,
        generate_html: bool = False
    ):
        self.repo_path = repo_path
        self.output_dir = output_dir
        self.config = config
        self.verbose = verbose
        self.generate_html = generate_html
        self.progress_tracker = ProgressTracker(total_stages=5, verbose=verbose)
        self.job = DocumentationJob()

        # Setup job metadata
        self.job.repository_path = str(repo_path)
        self.job.repository_name = repo_path.name
        self.job.output_directory = str(output_dir)
        self.job.llm_config = LLMConfig(
            main_model=config.get('main_model', ''),
            cluster_model=config.get('cluster_model', ''),
        )

        self._configure_backend_logging()

    def _configure_backend_logging(self):
        """Configure backend logger for CLI use with colored output."""
        from codewiki.src.be.dependency_analyzer.utils.logging_config import ColoredFormatter

        backend_logger = logging.getLogger('codewiki.src.be')
        backend_logger.handlers.clear()

        if self.verbose:
            backend_logger.setLevel(logging.DEBUG)
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.DEBUG)
            colored_formatter = ColoredFormatter()
            console_handler.setFormatter(colored_formatter)
            backend_logger.addHandler(console_handler)
        else:
            backend_logger.setLevel(logging.WARNING)
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setLevel(logging.WARNING)
            colored_formatter = ColoredFormatter()
            console_handler.setFormatter(colored_formatter)
            backend_logger.addHandler(console_handler)

        backend_logger.propagate = False

    def generate(self) -> DocumentationJob:
        """
        Generate documentation with progress tracking.

        Returns:
            Completed DocumentationJob

        Raises:
            APIError: If LLM API call fails
        """
        self.job.start()
        start_time = time.time()

        try:
            set_cli_context(True)

            backend_config = BackendConfig.from_cli(
                repo_path=str(self.repo_path),
                output_dir=str(self.output_dir),
                main_model=self.config.get('main_model'),
                cluster_model=self.config.get('cluster_model'),
                max_tokens=self.config.get('max_tokens', 32768),
                max_token_per_module=self.config.get('max_token_per_module', 36369),
                max_token_per_leaf_module=self.config.get('max_token_per_leaf_module', 16000),
                max_depth=self.config.get('max_depth', 2),
                agent_instructions=self.config.get('agent_instructions'),
                no_cache=self.config.get('no_cache', False),
                analysis_only=self.config.get('analysis_only', False),
                deep_analysis=self.config.get('deep_analysis', False),
                progressive=self.config.get('progressive', 0),
                only_debug_docs=self.config.get('only_debug_docs', False),
                only_monitoring_docs=self.config.get('only_monitoring_docs', False),
            )

            asyncio.run(self._run_backend_generation(backend_config))

            # Stage 4: HTML Generation (optional)
            if self.generate_html:
                self._run_html_generation()

            # Stage 5: Finalization (metadata already created by backend)
            self._finalize_job()

            generation_time = time.time() - start_time
            self.job.complete()

            return self.job

        except Exception as e:
            self.job.fail(str(e))
            raise

    async def _run_backend_generation(self, backend_config: BackendConfig):
        """Run backend generation with progress tracking."""
        doc_generator = DocumentationGenerator(backend_config)

        def on_progress(stage, stage_name, progress, message):
            if progress <= 0.01:
                self.progress_tracker.start_stage(stage, stage_name)
            if self.verbose:
                self.progress_tracker.update_stage(progress, message)
            if progress >= 1.0:
                self.progress_tracker.complete_stage()

        try:
            await doc_generator.run(on_progress=on_progress)
        except Exception as e:
            raise APIError(f"Documentation generation failed: {e}")

        # Collect generated files
        working_dir = str(self.output_dir.absolute())
        for file_path in os.listdir(working_dir):
            if file_path.endswith(('.md', '.json', '.html')):
                self.job.files_generated.append(file_path)

        # Populate statistics from backend-generated metadata
        self._populate_statistics_from_output(working_dir)

    def _populate_statistics_from_output(self, working_dir: str):
        """Read metadata.json and module_tree.json to populate job statistics."""
        metadata_path = os.path.join(working_dir, "metadata.json")
        if os.path.exists(metadata_path):
            try:
                metadata = json.loads(Path(metadata_path).read_text())
                stats = metadata.get("statistics", {})
                self.job.statistics.total_files_analyzed = stats.get("total_components", 0)
                self.job.statistics.leaf_nodes = stats.get("leaf_nodes", 0)
                self.job.statistics.max_depth = stats.get("max_depth", 0)
            except Exception:
                pass

        module_tree_path = os.path.join(working_dir, "module_tree.json")
        if os.path.exists(module_tree_path):
            try:
                module_tree = json.loads(Path(module_tree_path).read_text())
                self.job.module_count = len(module_tree)
            except Exception:
                pass

    def _run_html_generation(self):
        """Run HTML generation stage."""
        self.progress_tracker.start_stage(4, "HTML Generation")

        from codewiki.cli.html_generator import HTMLGenerator

        html_generator = HTMLGenerator()

        if self.verbose:
            self.progress_tracker.update_stage(0.3, "Loading module tree and metadata...")

        repo_info = html_generator.detect_repository_info(self.repo_path)

        output_path = self.output_dir / "index.html"
        html_generator.generate(
            output_path=output_path,
            title=repo_info['name'],
            repository_url=repo_info['url'],
            github_pages_url=repo_info['github_pages_url'],
            docs_dir=self.output_dir
        )

        self.job.files_generated.append("index.html")

        if self.verbose:
            self.progress_tracker.update_stage(1.0, "Generated index.html")

        self.progress_tracker.complete_stage()

    def _finalize_job(self):
        """Finalize the job (metadata already created by backend)."""
        metadata_path = self.output_dir / "metadata.json"
        if not metadata_path.exists():
            with open(metadata_path, 'w') as f:
                f.write(self.job.to_json())
