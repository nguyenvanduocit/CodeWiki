# Vue 3 SFC Analyzer Design

## Problem

CodeWiki's AST dependency analyzer does not support `.vue` files. Vue 3 Single File Components contain multiple sections (template, script, style) that require specialized parsing. Without Vue support, any Vue 3 codebase gets incomplete dependency analysis and documentation.

## Approach: Two-Stage Parse

Parse `.vue` with `tree-sitter-vue` (already available via `tree-sitter-language-pack`) to extract SFC structure, then re-parse the script content with the existing `TreeSitterTSAnalyzer` or `TreeSitterJSAnalyzer`.

**Why two-stage:** The Vue grammar treats `<script>` content as `raw_text` — it doesn't parse TypeScript/JavaScript inside it. The existing TS analyzer is ~1000 lines of battle-tested code. Reusing it with a line offset adjustment is far simpler than reimplementing.

## Scope

**In scope:**
- Composition API only (`<script setup>`, `defineComponent()`)
- Full SFC analysis: script + template + cross-section linking
- `tree-sitter-vue` grammar for SFC parsing

**Out of scope:**
- Options API (`data()`, `methods`, `computed` object syntax)
- `<style>` analysis
- `v-for` scoped variable extraction
- `v-slot` / scoped slot analysis
- Complex JS expressions inside directives
- Dynamic component `:is` resolution

## Design

### Section 1: SFC Parsing & Block Extraction

The Vue analyzer parses `.vue` with `tree-sitter-vue` and extracts blocks:

```python
@dataclass
class ScriptBlock:
    content: str          # raw_text from script_element
    lang: str             # "ts" | "js" | "" (from lang attribute)
    is_setup: bool        # True if <script setup>
    start_line: int       # line offset in original .vue file (0-indexed)
```

Extraction logic:
1. Walk `component` root's children
2. For `script_element`: read `start_tag` attributes for `setup` and `lang`, extract `raw_text` node's text and start line
3. For `template_element`: keep the node reference for template analysis

Line offset handling: When the TS analyzer reports `start_line=5` for a function, the Vue analyzer adds `ScriptBlock.start_line` to map back to the original `.vue` file position.

### Section 2: Script Analysis via Existing Analyzers

Script block content is passed directly to existing analyzers:

- `lang="ts"` or `is_setup` → `TreeSitterTSAnalyzer(content, file_path, repo_path)`
- `lang="js"` or empty → `TreeSitterJSAnalyzer(content, file_path, repo_path)`

**No changes to existing analyzers.** Content is passed as if it were a standalone file.

Post-processing by Vue analyzer:
1. Fix line numbers — add `ScriptBlock.start_line` offset to every `Node.start_line`, `Node.end_line`, and `CallRelationship.call_line`
2. Fix file paths — keep the original `.vue` path
3. Fix module paths — strip `.vue` extension: `src/components/MyComp.vue` → `src.components.MyComp`

Vue-specific enrichment (post-processing pass over TS analyzer output):
- `defineProps` call → `component_type="vue_props"`
- `defineEmits` call → `component_type="vue_emits"`
- `defineExpose` call → `component_type="vue_expose"`
- Vue lifecycle hooks (`onMounted`, etc.) → tagged in relationships
- Composable calls (`use*` pattern) → tagged in relationships

### Section 3: Template Analysis

Traverse the `template_element` AST to extract 4 categories:

**Component References:** Any `element` or `self_closing_tag` with PascalCase `tag_name` (`^[A-Z]`). Creates `CallRelationship(caller=vue_component_id, callee=component_name, relationship_type="uses_component")`. Built-ins (`slot`, `component`, `transition`, `keep-alive`, `teleport`, `suspense`) are skipped.

**Event Handlers:** `directive_attribute` with `directive_name="@"`. Value `attribute_value` is the handler name. Creates `CallRelationship(relationship_type="calls")`.

**Prop Bindings:** `directive_attribute` with `directive_name=":"`. Simple bindings (single identifier value) create relationships. Complex expressions skipped.

**Interpolations:** `interpolation` → `raw_text`. If trimmed text is a single identifier, create a relationship. Complex expressions skipped.

### Section 4: Vue Component Entity Model

Each `.vue` file produces one top-level Node + all child nodes from script analysis:

```
src/components/UserCard.vue
  ├─ Node: "src.components.UserCard" (component_type="vue_component")
  ├─ Node: "src.components.UserCard.handleClick" (component_type="function")
  ├─ Node: "src.components.UserCard.message" (component_type="variable", node_type="ref")
  └─ ...
```

New `component_type` values: `vue_component`, `vue_props`, `vue_emits`

New `node_type` annotations on variables: `ref`, `reactive`, `computed` — detected by checking callee name in variable initializer relationships.

Relationship types produced:

| Source | Target | relationship_type |
|--------|--------|-------------------|
| vue_component | ChildComponent (template) | `uses_component` |
| vue_component | handleClick (from @click) | `calls` |
| vue_component | message (from interpolation) | `references` |
| vue_component | ./Child.vue (from import) | `imports` |

### Section 5: Pipeline Integration

**File extension registration** in `patterns.py`:
```python
CODE_EXTENSIONS[".vue"] = "vue"
```

**Supported languages** in `analysis_service.py`:
```python
SUPPORTED_LANGUAGES.add("vue")
```

**Analyzer dispatch** in `call_graph_analyzer.py`: Add `vue` case bypassing query-based analyzer, calling `analyze_vue_file()` directly.

**New file:** `codewiki/src/be/dependency_analyzer/analyzers/vue.py` containing `TreeSitterVueAnalyzer` class and `analyze_vue_file()` function.

**No new pip dependencies.** `tree-sitter-language-pack` already provides the `vue` grammar.

### Processing Flow

```
analyze_vue_file(file_path, content, repo_dir)
  1. Parse content with tree-sitter-vue
  2. Extract ScriptBlock (content, lang, is_setup, start_line)
  3. Extract template_element node
  4. Pass ScriptBlock.content to TreeSitterTSAnalyzer or TreeSitterJSAnalyzer
     → (script_nodes, script_relationships)
  5. Fix line offsets on all script_nodes and script_relationships
  6. Traverse template_element for component refs, event handlers, bindings, interpolations
     → (template_relationships)
  7. Create top-level vue_component Node
  8. Post-process: enrich node_type for ref/reactive/computed, detect defineProps/defineEmits
  9. Return (all_nodes, script_relationships + template_relationships)
```

## Files Changed

| File | Change |
|------|--------|
| `codewiki/src/be/dependency_analyzer/analyzers/vue.py` | **NEW** — Vue 3 SFC analyzer |
| `codewiki/src/be/dependency_analyzer/utils/patterns.py` | Add `.vue` → `"vue"` |
| `codewiki/src/be/dependency_analyzer/analysis/analysis_service.py` | Add `"vue"` to `SUPPORTED_LANGUAGES` |
| `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py` | Add `vue` dispatch case |
