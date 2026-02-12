"""
Go language analyzer using tree-sitter-go.

Extracts functions, methods, structs, interfaces, and type declarations from Go files,
along with their dependency relationships (imports, struct embedding, interface implementation,
function calls, method calls).
"""

import logging
from typing import List, Optional, Tuple, Dict, Set
from pathlib import Path
import os

from tree_sitter import Parser, Language
import tree_sitter_go
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)

# Go primitive and built-in types to exclude from dependencies
GO_PRIMITIVES: Set[str] = {
    "bool", "byte", "rune", "int", "int8", "int16", "int32", "int64",
    "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
    "float32", "float64", "complex64", "complex128",
    "string", "error", "any",
    # Common Go built-in types
    "Context", "error", "Time", "Duration",
}

# Go built-in functions to exclude from call tracking
GO_BUILTINS: Set[str] = {
    "append", "cap", "clear", "close", "complex", "copy", "delete",
    "imag", "len", "make", "new", "panic", "print", "println",
    "real", "recover", "min", "max",
}


class TreeSitterGoAnalyzer:
    """Analyzes Go files using tree-sitter to extract nodes and relationships."""

    def __init__(self, file_path: str, content: str, repo_path: str = None):
        self.file_path = Path(file_path)
        self.content = content or ""
        self.repo_path = repo_path or ""
        self.nodes: List[Node] = []
        self.call_relationships: List[CallRelationship] = []
        self._top_level_nodes: Dict[str, Node] = {}
        self._package_name: str = ""
        self._import_map: Dict[str, str] = {}  # alias -> full package path
        self._current_function: Optional[str] = None
        self._current_method_receiver: Optional[str] = None

        self._analyze()

    def _get_module_path(self) -> str:
        """Get module path for the file (package-based)."""
        if self.repo_path:
            try:
                rel_path = os.path.relpath(str(self.file_path), self.repo_path)
            except ValueError:
                rel_path = str(self.file_path)
        else:
            rel_path = str(self.file_path)

        # Remove .go extension
        if rel_path.endswith('.go'):
            rel_path = rel_path[:-3]

        return rel_path.replace('/', '.').replace('\\', '.')

    def _get_relative_path(self) -> str:
        """Get relative path from repo root."""
        if self.repo_path:
            try:
                return os.path.relpath(str(self.file_path), self.repo_path)
            except ValueError:
                return str(self.file_path)
        return str(self.file_path)

    def _get_component_id(self, name: str, receiver_type: str = None) -> str:
        """Generate component ID for a node."""
        module_path = self._get_module_path()
        if receiver_type:
            # For methods: module_path.ReceiverType.MethodName
            return f"{module_path}.{receiver_type}.{name}"
        return f"{module_path}.{name}" if module_path else name

    def _analyze(self):
        """Parse and analyze the Go file."""
        try:
            go_lang_capsule = tree_sitter_go.language()
            go_language = Language(go_lang_capsule)
            parser = Parser(go_language)

            tree = parser.parse(bytes(self.content, "utf8"))
            root = tree.root_node
            lines = self.content.splitlines()

            # First pass: extract package name and imports
            self._extract_package_info(root)

            # Second pass: extract nodes (functions, methods, types)
            self._extract_nodes(root, lines)

            # Third pass: extract call relationships
            self._extract_call_relationships(root)

        except Exception as e:
            logger.error(f"Error parsing Go file {self.file_path}: {e}")

    def _extract_package_info(self, node):
        """Extract package name and import statements."""
        if node.type == "package_clause":
            for child in node.children:
                if child.type == "package_identifier":
                    self._package_name = child.text.decode()
                    break

        elif node.type == "import_declaration":
            self._extract_import(node)

        for child in node.children:
            self._extract_package_info(child)

    def _extract_import(self, node):
        """Extract import statement."""
        # Handle: import "fmt" or import alias "pkg"
        import_spec = None
        for child in node.children:
            if child.type == "import_spec":
                import_spec = child
                break

        if import_spec:
            alias = None
            path = None

            for child in import_spec.children:
                if child.type == "package_identifier":
                    alias = child.text.decode()
                elif child.type == "interpreted_string_literal":
                    # Remove quotes
                    path = child.text.decode().strip('"')

            if path:
                # If no alias, use last part of path
                if not alias:
                    alias = path.split('/')[-1]
                self._import_map[alias] = path

    def _extract_nodes(self, node, lines: List[str], depth: int = 0):
        """Extract function, method, struct, interface nodes."""
        if depth > 100:
            return

        node_type = None
        node_name = None
        receiver_type = None
        docstring = ""

        # Get preceding docstring (Go comments)
        docstring = self._get_preceding_docstring(node, lines)

        # Function declaration: func Name()
        if node.type == "function_declaration":
            node_type = "function"
            name_node = self._find_child_by_type(node, "identifier")
            node_name = name_node.text.decode() if name_node else None

        # Method declaration: func (r *Receiver) Name()
        elif node.type == "method_declaration":
            node_type = "method"
            name_node = self._find_child_by_type(node, "field_identifier")
            node_name = name_node.text.decode() if name_node else None

            # Extract receiver type
            receiver_node = self._find_child_by_type(node, "parameter_list")
            if receiver_node:
                for child in receiver_node.children:
                    if child.type == "parameter_declaration":
                        type_node = self._find_child_by_type(child, "type_identifier")
                        if type_node:
                            receiver_type = type_node.text.decode()
                        # Handle pointer receiver: *Type
                        elif child.text:
                            text = child.text.decode()
                            if text.startswith('*'):
                                receiver_type = text[1:]
                            else:
                                # Look for type_identifier in children
                                for subchild in child.children:
                                    if subchild.type == "type_identifier":
                                        receiver_type = subchild.text.decode()
                                        break
                        break

        # Type declaration: type Name struct/interface
        elif node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    name_node = self._find_child_by_type(child, "type_identifier")
                    node_name = name_node.text.decode() if name_node else None

                    # Check what kind of type
                    type_node = self._find_child_by_type(child, "struct_type")
                    if type_node:
                        node_type = "struct"
                    else:
                        type_node = self._find_child_by_type(child, "interface_type")
                        if type_node:
                            node_type = "interface"

                    if node_type and node_name:
                        self._create_type_node(node_name, node_type, node, lines, docstring)
                    break

        # Create function/method node
        if node_type in ("function", "method") and node_name:
            component_id = self._get_component_id(node_name, receiver_type)
            relative_path = self._get_relative_path()

            # Extract parameters
            parameters = self._extract_parameters(node)

            display_name = node_name
            if receiver_type:
                display_name = f"{receiver_type}.{node_name}"

            # Safely extract source code
            start_line = node.start_point[0] if node.start_point else 0
            end_line = node.end_point[0] if node.end_point else start_line
            source_code = "\n".join(lines[start_line:end_line+1]) if lines else ""

            node_obj = Node(
                id=component_id,
                name=node_name,
                component_type=node_type,
                file_path=str(self.file_path),
                relative_path=relative_path,
                source_code=source_code,
                start_line=start_line + 1,
                end_line=end_line + 1,
                has_docstring=bool(docstring),
                docstring=docstring,
                parameters=parameters,
                node_type=node_type,
                base_classes=None,
                class_name=receiver_type,
                display_name=f"{node_type} {display_name}",
                component_id=component_id
            )
            self.nodes.append(node_obj)
            self._top_level_nodes[node_name] = node_obj
            if receiver_type:
                self._top_level_nodes[f"{receiver_type}.{node_name}"] = node_obj

        # Recursively process children
        for child in node.children:
            self._extract_nodes(child, lines, depth + 1)

    def _create_type_node(self, name: str, node_type: str, node, lines: List[str], docstring: str):
        """Create a node for struct or interface type."""
        component_id = self._get_component_id(name)
        relative_path = self._get_relative_path()

        # Safely extract source code
        start_line = node.start_point[0] if node.start_point else 0
        end_line = node.end_point[0] if node.end_point else start_line
        source_code = "\n".join(lines[start_line:end_line+1]) if lines else ""

        node_obj = Node(
            id=component_id,
            name=name,
            component_type=node_type,
            file_path=str(self.file_path),
            relative_path=relative_path,
            source_code=source_code,
            start_line=start_line + 1,
            end_line=end_line + 1,
            has_docstring=bool(docstring),
            docstring=docstring,
            parameters=None,
            node_type=node_type,
            base_classes=None,
            class_name=None,
            display_name=f"{node_type} {name}",
            component_id=component_id
        )
        self.nodes.append(node_obj)
        self._top_level_nodes[name] = node_obj

    def _extract_call_relationships(self, node, depth: int = 0):
        """Extract function call relationships."""
        if depth > 100:
            return

        # Track current function context
        if node.type == "function_declaration":
            name_node = self._find_child_by_type(node, "identifier")
            if name_node:
                self._current_function = name_node.text.decode()
                self._current_method_receiver = None

        elif node.type == "method_declaration":
            name_node = self._find_child_by_type(node, "field_identifier")
            if name_node:
                self._current_function = name_node.text.decode()
                # Extract receiver type
                receiver_node = self._find_child_by_type(node, "parameter_list")
                if receiver_node:
                    for child in receiver_node.children:
                        if child.type == "parameter_declaration":
                            for subchild in child.children:
                                if subchild.type == "type_identifier":
                                    self._current_method_receiver = subchild.text.decode()
                                    break
                            break

        # Handle call expressions
        if node.type == "call_expression":
            self._process_call_expression(node)

        # Handle struct embedding (inheritance-like)
        elif node.type == "struct_type":
            self._process_struct_embedding(node)

        # Handle interface embedding
        elif node.type == "interface_type":
            self._process_interface_embedding(node)

        # Recursively process children
        for child in node.children:
            self._extract_call_relationships(child, depth + 1)

        # Reset context when leaving function
        if node.type in ("function_declaration", "method_declaration"):
            self._current_function = None
            self._current_method_receiver = None

    def _process_call_expression(self, node):
        """Process a call expression and extract the callee."""
        if not self._current_function:
            return

        # Get the function/method being called
        func_node = node.children[0] if node.children else None
        if not func_node:
            return

        callee_name = None
        receiver_type = None

        # Direct call: funcName()
        if func_node.type == "identifier":
            callee_name = func_node.text.decode()

        # Method call: receiver.Method() or pkg.Func()
        elif func_node.type == "selector_expression":
            operand = func_node.children[0] if func_node.children else None
            field = func_node.children[1] if len(func_node.children) > 1 else None

            if field and field.type == "field_identifier":
                callee_name = field.text.decode()

            if operand and callee_name:
                if operand.type == "identifier":
                    op_name = operand.text.decode()
                    # Check if it's a package reference
                    if op_name in self._import_map:
                        # This is pkg.Func() call
                        callee_name = f"{op_name}.{callee_name}"
                    else:
                        # This is receiver.Method() call
                        receiver_type = op_name
                elif operand.type == "selector_expression":
                    # Nested selector: a.b.Method()
                    selector_name = self._get_full_selector_name(operand)
                    if selector_name:
                        callee_name = f"{selector_name}.{callee_name}"

        if not callee_name or self._is_builtin(callee_name):
            return

        # Build caller ID
        caller_id = self._get_component_id(
            self._current_function,
            self._current_method_receiver
        )

        # Build callee ID - try to resolve
        callee_id = callee_name
        if receiver_type:
            # Method call on a receiver
            if f"{receiver_type}.{callee_name}" in self._top_level_nodes:
                callee_id = self._get_component_id(callee_name, receiver_type)
            else:
                callee_id = f"{receiver_type}.{callee_name}"

        self.call_relationships.append(CallRelationship(
            caller=caller_id,
            callee=callee_id,
            call_line=node.start_point[0] + 1,
            is_resolved=callee_id in self._top_level_nodes
        ))

    def _process_struct_embedding(self, node):
        """Process struct type for embedded structs (inheritance-like)."""
        containing_type = self._find_containing_type_name(node)
        if not containing_type:
            return

        for child in node.children:
            if child.type == "field_declaration_list":
                for field in child.children:
                    if field.type == "field_declaration":
                        # Embedded struct: just a type, no name
                        names = [c for c in field.children if c.type == "identifier"]
                        types = [c for c in field.children if c.type == "type_identifier"]

                        # If no name but has type, it's embedded
                        if types and not names:
                            embedded_type = types[0].text.decode()
                            if not self._is_primitive(embedded_type):
                                caller_id = self._get_component_id(containing_type)
                                self.call_relationships.append(CallRelationship(
                                    caller=caller_id,
                                    callee=embedded_type,
                                    call_line=node.start_point[0] + 1,
                                    is_resolved=False
                                ))

    def _process_interface_embedding(self, node):
        """Process interface type for embedded interfaces."""
        containing_type = self._find_containing_type_name(node)
        if not containing_type:
            return

        for child in node.children:
            if child.type == "method_spec":
                # Check if it's just a type (embedded interface)
                has_name = any(c.type == "field_identifier" for c in child.children)
                type_ids = [c for c in child.children if c.type == "type_identifier"]

                if type_ids and not has_name:
                    embedded_type = type_ids[0].text.decode()
                    if not self._is_primitive(embedded_type):
                        caller_id = self._get_component_id(containing_type)
                        self.call_relationships.append(CallRelationship(
                            caller=caller_id,
                            callee=embedded_type,
                            call_line=node.start_point[0] + 1,
                            is_resolved=False
                        ))

    def _find_containing_type_name(self, node) -> Optional[str]:
        """Find the containing struct/interface name."""
        current = node.parent
        while current:
            if current.type == "type_spec":
                name_node = self._find_child_by_type(current, "type_identifier")
                return name_node.text.decode() if name_node else None
            current = current.parent
        return None

    def _get_full_selector_name(self, node) -> str:
        """Get full name from a selector expression."""
        parts = []
        current = node
        while current and current.type == "selector_expression":
            if len(current.children) >= 2:
                field = current.children[1]
                if field.type == "field_identifier":
                    parts.insert(0, field.text.decode())
                current = current.children[0]
            else:
                break

        if current and current.type == "identifier":
            parts.insert(0, current.text.decode())

        return ".".join(parts)

    def _find_child_by_type(self, node, child_type: str):
        """Find first child of a specific type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def _get_preceding_docstring(self, node, lines: List[str]) -> str:
        """Extract Go comment preceding a node."""
        if node.start_point[0] == 0:
            return ""

        # Check lines directly before the node
        start_line = node.start_point[0]
        doc_lines = []

        for i in range(start_line - 1, max(0, start_line - 20), -1):
            if i >= len(lines):
                continue
            line = lines[i].strip()

            if line.startswith("//"):
                doc_lines.insert(0, line[2:].strip())
            elif line == "":
                if doc_lines:
                    break  # End of comment block
            else:
                break  # Non-comment, non-empty line

        return "\n".join(doc_lines) if doc_lines else ""

    def _extract_parameters(self, node) -> Optional[List[str]]:
        """Extract function/method parameters."""
        params = []

        # For methods, skip the receiver parameter list
        start_idx = 0
        if node.type == "method_declaration":
            for i, child in enumerate(node.children):
                if child.type == "parameter_list":
                    start_idx = i + 1
                    break

        # Find the main parameter list
        for child in node.children[start_idx:]:
            if child.type == "parameter_list":
                for param in child.children:
                    if param.type == "parameter_declaration":
                        # Get parameter names
                        names = []
                        param_type = None

                        for pchild in param.children:
                            if pchild.type == "identifier":
                                names.append(pchild.text.decode())
                            elif pchild.type in ("type_identifier", "qualified_type",
                                                  "pointer_type", "slice_type", "map_type",
                                                  "chan_type"):
                                param_type = pchild.text.decode()

                        for name in names:
                            if param_type:
                                params.append(f"{name} {param_type}")
                            else:
                                params.append(name)

        return params if params else None

    def _is_primitive(self, type_name: str) -> bool:
        """Check if type is a Go primitive or built-in."""
        if not type_name:
            return True
        # Remove pointer prefix
        clean_name = type_name.lstrip("*").lstrip("[]").lstrip("map[").split("]")[0]
        clean_name = clean_name.split(".")[0]  # Handle pkg.Type
        return clean_name.lower() in {p.lower() for p in GO_PRIMITIVES}

    def _is_builtin(self, name: str) -> bool:
        """Check if name is a Go built-in function."""
        if not name:
            return True
        # Remove package prefix if any
        base_name = name.split(".")[-1]
        return base_name in GO_BUILTINS


def analyze_go_file(file_path: str, content: str, repo_path: str = None) -> Tuple[List[Node], List[CallRelationship]]:
    """
    Analyze a Go file and extract nodes and call relationships.

    Args:
        file_path: Path to the Go file
        content: Content of the Go file
        repo_path: Optional path to the repository root

    Returns:
        Tuple of (nodes, call_relationships)
    """
    analyzer = TreeSitterGoAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
