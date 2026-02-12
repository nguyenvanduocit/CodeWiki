"""
Call Graph Analyzer

Central orchestrator for multi-language call graph analysis.
Coordinates language-specific analyzers to build comprehensive call graphs
across different programming languages in a repository.
"""

from typing import Dict, List, Tuple, Optional
import logging
import os
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship
from codewiki.src.be.dependency_analyzer.utils.patterns import CODE_EXTENSIONS
from codewiki.src.be.dependency_analyzer.utils.security import safe_open_text

logger = logging.getLogger(__name__)


def _dispatch_language_analyzer(
    language: str, file_path, content: str, repo_dir: str
) -> Optional[Tuple[List[Node], List[CallRelationship]]]:
    """
    Dispatch to the appropriate language-specific analyzer.

    Must be at module level for pickle compatibility with multiprocessing.
    Tries query-based analyzer first, falls back to hand-coded analyzers.

    Args:
        language: Programming language identifier
        file_path: Path to the file
        content: File content string
        repo_dir: Repository base directory path

    Returns:
        Tuple of (nodes, relationships) or None if language is unsupported
    """
    # Try query-based analyzer first (skip Go: hand-coded analyzer has type resolution)
    if language != "go":
        try:
            from codewiki.src.be.dependency_analyzer.query_analyzer import analyze_file_with_queries
            result = analyze_file_with_queries(language, file_path, content, repo_dir)
            if result is not None:
                return result
        except Exception as e:
            logger.debug(f"Query-based analyzer failed for {language}, falling back: {e}")

    # Fall back to hand-coded analyzers
    if language == "python":
        from codewiki.src.be.dependency_analyzer.analyzers.python import analyze_python_file
        return analyze_python_file(file_path, content, repo_path=repo_dir)
    elif language == "javascript":
        from codewiki.src.be.dependency_analyzer.analyzers.javascript import analyze_javascript_file_treesitter
        return analyze_javascript_file_treesitter(file_path, content, repo_path=repo_dir)
    elif language == "typescript":
        from codewiki.src.be.dependency_analyzer.analyzers.typescript import analyze_typescript_file_treesitter
        return analyze_typescript_file_treesitter(file_path, content, repo_path=repo_dir)
    elif language == "java":
        from codewiki.src.be.dependency_analyzer.analyzers.java import analyze_java_file
        return analyze_java_file(file_path, content, repo_path=repo_dir)
    elif language == "csharp":
        from codewiki.src.be.dependency_analyzer.analyzers.csharp import analyze_csharp_file
        return analyze_csharp_file(file_path, content, repo_path=repo_dir)
    elif language == "c":
        from codewiki.src.be.dependency_analyzer.analyzers.c import analyze_c_file
        return analyze_c_file(file_path, content, repo_path=repo_dir)
    elif language == "cpp":
        from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file
        return analyze_cpp_file(file_path, content, repo_path=repo_dir)
    elif language == "php":
        from codewiki.src.be.dependency_analyzer.analyzers.php import analyze_php_file
        return analyze_php_file(file_path, content, repo_path=repo_dir)
    elif language == "go":
        from codewiki.src.be.dependency_analyzer.analyzers.golang import analyze_go_file
        return analyze_go_file(file_path, content, repo_path=repo_dir)
    return None


def analyze_single_file(
    repo_dir: str, file_info: Dict
) -> Tuple[Dict[str, Node], List[CallRelationship], Optional[str]]:
    """
    Analyze a single code file and return results.

    This is a standalone function (not a method) to support multiprocessing.
    Must be at module level for pickle compatibility.

    Args:
        repo_dir: Repository directory path
        file_info: File information dictionary with 'path', 'language', etc.

    Returns:
        Tuple of (functions dict, relationships list, error message or None)
    """
    functions: Dict[str, Node] = {}
    call_relationships: List[CallRelationship] = []
    error: Optional[str] = None

    base = Path(repo_dir)
    file_path = base / file_info["path"]

    try:
        content = safe_open_text(base, file_path)
        file_results = _dispatch_language_analyzer(
            file_info["language"], file_path, content, repo_dir
        )

        if file_results:
            funcs, rels = file_results
            for func in funcs:
                func_id = func.id if func.id else f"{file_path}:{func.name}"
                functions[func_id] = func
            call_relationships = rels

    except Exception as e:
        error = f"Error analyzing {file_path}: {str(e)}"

    return functions, call_relationships, error


class CallGraphAnalyzer:
    def __init__(self):
        """Initialize the call graph analyzer."""
        self.functions: Dict[str, Node] = {}
        self.call_relationships: List[CallRelationship] = []
        logger.debug("CallGraphAnalyzer initialized.")

    def analyze_code_files(self, code_files: List[Dict], base_dir: str) -> Dict:
        """
        Complete analysis: Analyze all files to build complete call graph with all nodes.

        This approach:
        1. Analyzes all code files (parallelized with ProcessPoolExecutor)
        2. Extracts all functions and relationships
        3. Builds complete call graph
        4. Returns all nodes and relationships
        """
        logger.debug(f"Starting analysis of {len(code_files)} files")

        self.functions = {}
        self.call_relationships = []

        files_analyzed = 0
        failed_files: List[str] = []
        num_workers = os.cpu_count() or 4

        try:
            with ProcessPoolExecutor(max_workers=num_workers) as executor:
                futures = {
                    executor.submit(analyze_single_file, base_dir, file_info): file_info
                    for file_info in code_files
                }

                for future in as_completed(futures):
                    file_info = futures[future]
                    try:
                        funcs, rels, error = future.result()
                        if error:
                            logger.error(error)
                            failed_files.append(file_info['path'])
                        else:
                            logger.debug(f"Analyzed: {file_info['path']}")
                            self.functions.update(funcs)
                            self.call_relationships.extend(rels)
                            files_analyzed += 1
                    except Exception as e:
                        logger.error(f"Failed to get result for {file_info['path']}: {e}")
                        failed_files.append(file_info['path'])

        except Exception as e:
            logger.warning(f"Parallel analysis failed, falling back to sequential: {e}")
            files_analyzed = 0
            failed_files = []
            for file_info in code_files:
                logger.debug(f"Analyzing: {file_info['path']}")
                self._analyze_code_file(base_dir, file_info)
                files_analyzed += 1

        if failed_files:
            logger.warning(
                f"{len(failed_files)} file(s) failed analysis: {failed_files}"
            )

        logger.debug(
            f"Analysis complete: {files_analyzed}/{len(code_files)} files analyzed, "
            f"{len(self.functions)} functions, {len(self.call_relationships)} relationships"
        )

        logger.debug("Resolving call relationships")
        self._resolve_call_relationships()
        self._deduplicate_relationships()
        viz_data = self._generate_visualization_data()

        return {
            "call_graph": {
                "total_functions": len(self.functions),
                "total_calls": len(self.call_relationships),
                "languages_found": list(set(f.get("language") for f in code_files)),
                "files_analyzed": files_analyzed,
                "analysis_approach": "complete_unlimited",
            },
            "functions": [func.model_dump() for func in self.functions.values()],
            "relationships": [rel.model_dump() for rel in self.call_relationships],
            "visualization": viz_data,
        }

    def extract_code_files(self, file_tree: Dict) -> List[Dict]:
        """
        Extract code files from file tree structure.

        Filters files based on supported extensions and excludes test/config files.

        Args:
            file_tree: Nested dictionary representing file structure

        Returns:
            List of code file information dictionaries
        """
        code_files = []

        def traverse(tree):
            if tree["type"] == "file":
                ext = tree.get("extension", "").lower()
                if ext in CODE_EXTENSIONS:
                    code_files.append(
                        {
                            "path": tree["path"],
                            "name": tree["name"],
                            "extension": ext,
                            "language": CODE_EXTENSIONS[ext],
                        }
                    )
            elif tree["type"] == "directory" and tree.get("children"):
                for child in tree["children"]:
                    traverse(child)

        traverse(file_tree)
        return code_files

    def _analyze_code_file(self, repo_dir: str, file_info: Dict):
        """
        Analyze a single code file based on its language.

        Routes to appropriate language-specific analyzer via the shared dispatcher.

        Args:
            repo_dir: Repository directory path
            file_info: File information dictionary
        """
        base = Path(repo_dir)
        file_path = base / file_info["path"]

        try:
            content = safe_open_text(base, file_path)
            file_results = _dispatch_language_analyzer(
                file_info["language"], file_path, content, repo_dir
            )

            if file_results:
                funcs, rels = file_results
                for func in funcs:
                    func_id = func.id if func.id else f"{file_path}:{func.name}"
                    self.functions[func_id] = func
                self.call_relationships.extend(rels)

        except Exception as e:
            logger.error(f"Error analyzing {file_path}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")

    def _resolve_call_relationships(self):
        """
        Resolve function call relationships across all languages.

        Attempts to match function calls to actual function definitions,
        handling cross-language calls where possible.
        """
        func_lookup = {}
        for func_id, func_info in self.functions.items():
            func_lookup[func_id] = func_id
            func_lookup[func_info.name] = func_id
            if func_info.component_id:
                func_lookup[func_info.component_id] = func_id
                method_name = func_info.component_id.split(".")[-1]
                if method_name not in func_lookup:
                    func_lookup[method_name] = func_id
            # Add ReceiverType.MethodName key for method resolution
            if func_info.class_name and func_info.name:
                class_method_key = f"{func_info.class_name}.{func_info.name}"
                if class_method_key not in func_lookup:
                    func_lookup[class_method_key] = func_id

        for relationship in self.call_relationships:
            callee_name = relationship.callee

            if callee_name in func_lookup:
                relationship.callee = func_lookup[callee_name]
                relationship.is_resolved = True
            elif "." in callee_name:
                method_name = callee_name.split(".")[-1]
                if method_name in func_lookup:
                    relationship.callee = func_lookup[method_name]
                    relationship.is_resolved = True

    def _deduplicate_relationships(self):
        """
        Deduplicate call relationships based on caller-callee pairs.

        Removes duplicate relationships while preserving the first occurrence.
        This helps eliminate noise from multiple calls to the same function.
        """
        seen = set()
        unique_relationships = []

        for rel in self.call_relationships:
            key = (rel.caller, rel.callee)
            if key not in seen:
                seen.add(key)
                unique_relationships.append(rel)

        self.call_relationships = unique_relationships

    def _generate_visualization_data(self) -> Dict:
        """
        Generate visualization data for graph rendering.

        Creates Cytoscape.js compatible graph data with nodes and edges.

        Returns:
            Dict: Visualization data with cytoscape elements and summary
        """
        cytoscape_elements = []

        for func_id, func_info in self.functions.items():
            node_classes = []
            if func_info.node_type == "method":
                node_classes.append("node-method")
            else:
                node_classes.append("node-function")

            file_ext = Path(func_info.file_path).suffix.lower()
            if file_ext == ".py":
                node_classes.append("lang-python")
            elif file_ext == ".js":
                node_classes.append("lang-javascript")
            elif file_ext == ".ts":
                node_classes.append("lang-typescript")
            elif file_ext in [".c", ".h"]:
                node_classes.append("lang-c")
            elif file_ext in [".cpp", ".cc", ".cxx", ".hpp", ".hxx"]:
                node_classes.append("lang-cpp")
            elif file_ext in [".php", ".phtml", ".inc"]:
                node_classes.append("lang-php")
            elif file_ext == ".go":
                node_classes.append("lang-go")

            cytoscape_elements.append(
                {
                    "data": {
                        "id": func_id,
                        "label": func_info.name,
                        "file": func_info.file_path,
                        "type": func_info.node_type or "function",
                        "language": CODE_EXTENSIONS.get(file_ext, "unknown"),
                    },
                    "classes": " ".join(node_classes),
                }
            )

        resolved_rels = [r for r in self.call_relationships if r.is_resolved]
        for rel in resolved_rels:
            cytoscape_elements.append(
                {
                    "data": {
                        "id": f"{rel.caller}->{rel.callee}",
                        "source": rel.caller,
                        "target": rel.callee,
                        "line": rel.call_line,
                    },
                    "classes": "edge-call",
                }
            )

        summary = {
            "total_nodes": len(self.functions),
            "total_edges": len(resolved_rels),
            "unresolved_calls": len(self.call_relationships) - len(resolved_rels),
        }

        return {
            "cytoscape": {"elements": cytoscape_elements},
            "summary": summary,
        }

    def generate_llm_format(self) -> Dict:
        """Generate clean format optimized for LLM consumption."""
        return {
            "functions": [
                {
                    "name": func.name,
                    "file": Path(func.file_path).name,
                    "purpose": (func.docstring.split("\n")[0] if func.docstring else None),
                    "parameters": func.parameters,
                    "is_recursive": func.name
                    in [
                        rel.callee
                        for rel in self.call_relationships
                        if rel.caller.endswith(func.name)
                    ],
                }
                for func in self.functions.values()
            ],
            "relationships": {
                func.name: {
                    "calls": [
                        rel.callee.split(":")[-1]
                        for rel in self.call_relationships
                        if rel.caller.endswith(func.name) and rel.is_resolved
                    ],
                    "called_by": [
                        rel.caller.split(":")[-1]
                        for rel in self.call_relationships
                        if rel.callee.endswith(func.name) and rel.is_resolved
                    ],
                }
                for func in self.functions.values()
            },
        }

