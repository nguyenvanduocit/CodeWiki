# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeWiki is a static analysis CLI tool for codebases. It parses source code using tree-sitter AST analysis across 10 languages (Python, Java, JavaScript, TypeScript, C, C++, C#, Go, PHP, Vue), builds dependency graphs, computes metrics, and generates architectural reports.

## Common Commands

### Development Setup
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements.txt
```

### Running Tests
```bash
pytest
pytest tests/test_vue_analyzer.py
pytest --cov=codewiki tests/
```

### Code Quality
```bash
black codewiki/
mypy codewiki/
ruff check codewiki/
```

### Running the CLI
```bash
codewiki --version

# Configure file patterns
codewiki config agent --include "*.cs" --exclude "*Tests*"

# Run static analysis
codewiki generate --verbose
codewiki generate -o analysis_output --include "*.py"
```

## Architecture

```
Repository Input
      ↓
Dependency Analysis (AST parsing → Call graph)
      ↓
Graph Metrics (PageRank, betweenness, communities, complexity)
      ↓
Reports (codebase_map.json + interactive graph.html)
```

### Directory Structure

```
codewiki/
├── config.py                 # Config dataclass
├── utils.py                  # FileManager for I/O
├── cli/                      # CLI layer (Click)
│   ├── main.py               # CLI entry point
│   ├── commands/
│   │   ├── config.py         # `codewiki config` commands
│   │   └── generate.py       # `codewiki generate` command
│   ├── config_manager.py     # ~/.codewiki/config.json persistence
│   ├── models/config.py      # Configuration + AgentInstructions dataclasses
│   └── utils/                # errors, fs, logging, validation, repo_validator
├── analyzer/                 # Core static analysis engine
│   ├── ast_parser.py         # Entry point — DependencyParser
│   ├── dependency_graphs_builder.py  # Orchestrates full analysis pipeline
│   ├── topo_sort.py          # Graph algorithms (topo sort, cycle detection)
│   ├── query_analyzer.py     # Tree-sitter query-based analysis
│   ├── languages/            # Language-specific analyzers (10 languages)
│   ├── analysis/             # AnalysisService, CallGraphAnalyzer, RepoAnalyzer, cloning
│   ├── models/               # Node, CallRelationship, Repository, AnalysisResult
│   └── utils/                # patterns, security, logging_config
└── reporting/                # Output generation
    ├── arch_rules.py         # Architectural rule validation
    ├── codebase_map_generator.py  # codebase_map.json output
    ├── graph_viewer_generator.py  # Interactive D3.js graph.html
    ├── graph_metrics.py      # PageRank, fan-in/out, communities
    ├── complexity_scorer.py  # Cyclomatic + cognitive complexity
    ├── temporal_coupling.py  # Git history co-change analysis
    └── tfidf_keywords.py     # TF-IDF keyword extraction
```

### Key Components

**Analyzer** (`codewiki/analyzer/`) — the core engine:
- `DependencyParser` parses a repository into `Node` components via tree-sitter
- `CallGraphAnalyzer` dispatches to language-specific analyzers
- `DependencyGraphBuilder` orchestrates: parse → build graph → compute metrics → save

**Reporting** (`codewiki/reporting/`) — output generators:
- `generate_codebase_map()` produces `codebase_map.json` with all nodes, edges, metrics
- `generate_graph_viewer()` produces self-contained `graph.html` with D3.js visualization
- `evaluate_rules()` detects architectural violations (god components, circular deps, etc.)

**CLI** (`codewiki/cli/`) — user interface:
- `config` command: manage `~/.codewiki/config.json` (file patterns)
- `generate` command: validate repo → run analysis → produce reports

### Configuration Flow

1. CLI stores config in `~/.codewiki/config.json`
2. `Config` dataclass (`codewiki/config.py`) holds repo_path, output_dir, patterns
3. `AgentInstructions` (`codewiki/cli/models/config.py`) for include/exclude patterns

### Adding Language Support

1. Create analyzer in `codewiki/analyzer/languages/newlang.py`
2. Register in `codewiki/analyzer/analysis/call_graph_analyzer.py`
3. Add tree-sitter dependency to `pyproject.toml`

## Important File Locations

| Purpose | Path |
|---------|------|
| CLI entry | `codewiki/cli/main.py` |
| Config model | `codewiki/config.py` |
| Analysis pipeline | `codewiki/analyzer/dependency_graphs_builder.py` |
| AST parser | `codewiki/analyzer/ast_parser.py` |
| Language analyzers | `codewiki/analyzer/languages/` |
| Graph metrics | `codewiki/reporting/graph_metrics.py` |
| Codebase map output | `codewiki/reporting/codebase_map_generator.py` |
| Arch rules | `codewiki/reporting/arch_rules.py` |

## Debugging

```bash
codewiki generate --verbose
# or
export CODEWIKI_LOG_LEVEL=DEBUG
```
