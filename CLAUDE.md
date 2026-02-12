# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeWiki is an AI-powered documentation generator for large-scale codebases. It uses hierarchical decomposition and recursive multi-agent processing to generate holistic documentation across 8 programming languages (Python, Java, JavaScript, TypeScript, C, C++, C#, Go).

## Common Commands

### Development Setup
```bash
# Create virtual environment and install
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements.txt
```

### Running Tests
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_dependency_analyzer.py

# Run with coverage
pytest --cov=codewiki tests/
```

### Code Quality
```bash
# Format code
black codewiki/

# Type check
mypy codewiki/

# Lint
ruff check codewiki/
```

### Running the CLI
```bash
# After pip install -e ., the codewiki command is available
codewiki --version

# Configure API settings
codewiki config set \
  --api-key YOUR_KEY \
  --base-url https://api.anthropic.com \
  --main-model claude-sonnet-4 \
  --cluster-model claude-sonnet-4 \
  --fallback-model glm-4p5

# Generate documentation for a repository
codewiki generate --verbose

# Generate with custom patterns
codewiki generate --include "*.cs" --exclude "Tests,Specs"
```

### Running the Web App
```bash
# Start the FastAPI web application
python -m codewiki.run_web_app

# Or via uvicorn directly
uvicorn codewiki.src.fe.web_app:app --reload
```

### Docker
```bash
# Build and run with Docker Compose
cd docker
cp env.example .env
# Edit .env with your configuration
docker-compose up --build
```

## High-Level Architecture

### Core Pipeline

```
Repository Input
      ↓
Dependency Analysis (AST parsing → Call graph)
      ↓
Hierarchical Decomposition (cluster_modules.py)
      ↓
Module Tree
      ↓
Recursive Agent Processing (agent_orchestrator.py)
      ↓
Documentation Output (Markdown + Visual artifacts)
```

### Key Components

**1. Dependency Analysis** (`codewiki/src/be/dependency_analyzer/`)
- `ast_parser.py`: Entry point for parsing repositories
- `analyzers/`: Language-specific analyzers (python.py, java.py, typescript.py, etc.)
- `analysis_service.py`: Orchestrates structure and call graph analysis
- Uses tree-sitter for multi-language AST parsing

**2. Module Clustering** (`codewiki/src/be/cluster_modules.py`)
- Hierarchical decomposition using LLM-based clustering
- Groups components into coherent modules when token count exceeds thresholds
- Configurable via `max_token_per_module` and `max_depth`

**3. Agent System** (`codewiki/src/be/agent_orchestrator.py`)
- Uses pydantic-ai for agent orchestration
- Dynamic delegation: complex modules get `generate_sub_module_documentation_tool`
- Leaf modules get simpler processing with just `read_code_components_tool` and `str_replace_editor_tool`

**4. CLI** (`codewiki/cli/`)
- `commands/config.py`: API configuration management (stores keys in system keychain)
- `commands/generate.py`: Documentation generation command
- `config_manager.py`: Handles `~/.codewiki/config.json` persistence

**5. Frontend** (`codewiki/src/fe/`)
- FastAPI-based web application
- `web_app.py`, `routes.py`: HTTP API
- `github_processor.py`: GitHub repo processing
- `visualise_docs.py`: Documentation visualization

### Configuration Flow

1. CLI stores config in `~/.codewiki/config.json` + system keychain (API keys)
2. `Config` dataclass (`codewiki/src/config.py`) instantiated from CLI or env vars
3. `AgentInstructions` (`codewiki/cli/models/config.py`) for customization (include/exclude patterns, doc type, custom instructions)

### Adding Language Support

To add a new language analyzer:

1. Create analyzer in `codewiki/src/be/dependency_analyzer/analyzers/newlang.py`:
```python
from .base import BaseAnalyzer

class NewLangAnalyzer(BaseAnalyzer):
    def __init__(self):
        super().__init__("newlang")

    def extract_dependencies(self, ast_node):
        # Implement dependency extraction
        pass
```

2. Register in `codewiki/src/be/dependency_analyzer/ast_parser.py`:
```python
LANGUAGE_ANALYZERS = {
    # ... existing languages ...
    "newlang": NewLangAnalyzer,
}
```

3. Add tree-sitter dependency to `pyproject.toml`

## Important File Locations

| Purpose | Path |
|---------|------|
| CLI entry | `codewiki/cli/main.py` |
| Config model | `codewiki/src/config.py` |
| Agent orchestration | `codewiki/src/be/agent_orchestrator.py` |
| Module clustering | `codewiki/src/be/cluster_modules.py` |
| LLM services | `codewiki/src/be/llm_services.py` |
| Prompt templates | `codewiki/src/be/prompt_template.py` |
| Language analyzers | `codewiki/src/be/dependency_analyzer/analyzers/` |
| Agent tools | `codewiki/src/be/agent_tools/` |

## Key Constants

- `DEFAULT_MAX_TOKENS = 32768` - Max output tokens for LLM
- `DEFAULT_MAX_TOKEN_PER_MODULE = 36369` - Threshold for clustering
- `DEFAULT_MAX_TOKEN_PER_LEAF_MODULE = 16000` - Threshold for leaf module detection
- `MAX_DEPTH = 2` - Default hierarchical decomposition depth

## Agent Tools

Located in `codewiki/src/be/agent_tools/`:
- `read_code_components.py`: Read code from the repository
- `str_replace_editor.py`: Documentation editing
- `generate_sub_module_documentations.py`: Delegate complex modules
- `deps.py`: Dependency traversal utilities

## Debugging

Enable verbose logging:
```bash
codewiki generate --verbose
# or
export CODEWIKI_LOG_LEVEL=DEBUG
```
