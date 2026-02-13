# Vue 3 SFC Analyzer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Vue 3 Single File Component (.vue) support to CodeWiki's AST dependency analyzer using a two-stage parse approach.

**Architecture:** Parse `.vue` files with `tree-sitter-vue` (already in `tree-sitter-language-pack`) to extract SFC structure, then delegate `<script>` content to the existing `TreeSitterTSAnalyzer` / `TreeSitterJSAnalyzer`. Template traversal extracts component references, event handlers, prop bindings, and interpolations.

**Tech Stack:** tree-sitter, tree-sitter-language-pack (vue grammar), existing TreeSitterTSAnalyzer/TreeSitterJSAnalyzer

**Design doc:** `docs/plans/2026-02-13-vue3-analyzer-design.md`

---

## Task 1: Pipeline Registration

Register `.vue` files in the pipeline so they get picked up and routed correctly.

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/utils/patterns.py:187-211` (CODE_EXTENSIONS)
- Modify: `codewiki/src/be/dependency_analyzer/utils/patterns.py:151-185` (DEFAULT_INCLUDE_PATTERNS)
- Modify: `codewiki/src/be/dependency_analyzer/analysis/analysis_service.py:23-33` (SUPPORTED_LANGUAGES)
- Modify: `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py:40-78` (_dispatch_language_analyzer)
- Modify: `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py:398-412` (visualization lang class)

**Step 1: Add `.vue` to CODE_EXTENSIONS**

In `codewiki/src/be/dependency_analyzer/utils/patterns.py`, add to `CODE_EXTENSIONS` dict after `.cs`:

```python
    ".cs": "csharp",
    ".vue": "vue",
```

**Step 2: Add `*.vue` to DEFAULT_INCLUDE_PATTERNS**

In same file, add `"*.vue"` to `DEFAULT_INCLUDE_PATTERNS` list (after `"*.tsx"`):

```python
    "*.tsx",
    "*.vue",
```

**Step 3: Add `"vue"` to SUPPORTED_LANGUAGES**

In `codewiki/src/be/dependency_analyzer/analysis/analysis_service.py`, add to the set:

```python
SUPPORTED_LANGUAGES = {
    "python",
    "javascript",
    "typescript",
    "java",
    "csharp",
    "c",
    "cpp",
    "php",
    "go",
    "vue",
}
```

**Step 4: Add Vue dispatch case**

In `codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py`, in `_dispatch_language_analyzer()`, add Vue case BEFORE the query-based fallback (line 41). Vue must bypass the query-based analyzer entirely since it needs two-stage parsing:

```python
def _dispatch_language_analyzer(
    language: str, file_path, content: str, repo_dir: str
) -> Optional[Tuple[List[Node], List[CallRelationship]]]:
    # Vue requires two-stage parsing - always use hand-coded analyzer
    if language == "vue":
        from codewiki.src.be.dependency_analyzer.analyzers.vue import analyze_vue_file
        return analyze_vue_file(file_path, content, repo_path=repo_dir)

    # Try query-based analyzer first (skip Go: hand-coded analyzer has type resolution)
    if language != "go":
        # ... existing code ...
```

**Step 5: Add Vue visualization class**

In same file, in `_generate_visualization_data()`, add after the Go case (around line 412):

```python
            elif file_ext == ".vue":
                node_classes.append("lang-vue")
```

**Step 6: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/utils/patterns.py \
        codewiki/src/be/dependency_analyzer/analysis/analysis_service.py \
        codewiki/src/be/dependency_analyzer/analysis/call_graph_analyzer.py
git commit -m "feat(vue): register .vue extension and dispatch in pipeline"
```

---

## Task 2: SFC Block Extraction

Create the Vue analyzer file with SFC parsing — extract script and template blocks from `.vue` files.

**Files:**
- Create: `codewiki/src/be/dependency_analyzer/analyzers/vue.py`
- Test: `tests/test_vue_analyzer.py`

**Step 1: Write the failing test**

Create `tests/test_vue_analyzer.py`:

```python
import pytest
from codewiki.src.be.dependency_analyzer.analyzers.vue import TreeSitterVueAnalyzer


SIMPLE_VUE_SFC = """<template>
  <div>{{ message }}</div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
const message = ref('Hello')
</script>
"""


def test_extract_script_block():
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", SIMPLE_VUE_SFC, "/repo")
    block = analyzer._extract_script_block()
    assert block is not None
    assert block.is_setup is True
    assert block.lang == "ts"
    assert "import { ref } from 'vue'" in block.content
    assert block.start_line == 4  # 0-indexed line of <script> tag


def test_extract_template_node():
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", SIMPLE_VUE_SFC, "/repo")
    template_node = analyzer._extract_template_node()
    assert template_node is not None
    assert template_node.type == "template_element"


def test_no_script_block():
    no_script = "<template><div>hi</div></template>"
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", no_script, "/repo")
    block = analyzer._extract_script_block()
    assert block is None


def test_script_without_setup():
    sfc = """<template><div>hi</div></template>
<script lang="js">
export default { name: 'App' }
</script>"""
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", sfc, "/repo")
    block = analyzer._extract_script_block()
    assert block is not None
    assert block.is_setup is False
    assert block.lang == "js"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vue_analyzer.py -v`
Expected: FAIL with ImportError (module doesn't exist yet)

**Step 3: Write SFC block extraction**

Create `codewiki/src/be/dependency_analyzer/analyzers/vue.py`:

```python
import logging
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from pathlib import Path

from tree_sitter import Parser, Language
from tree_sitter_language_pack import get_language

from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)


@dataclass
class ScriptBlock:
    content: str
    lang: str
    is_setup: bool
    start_line: int


class TreeSitterVueAnalyzer:

    VUE_BUILTINS = {
        "slot", "component", "transition", "transition-group",
        "keep-alive", "teleport", "suspense",
    }

    VUE_REACTIVITY_FNS = {
        "ref", "reactive", "computed", "readonly",
        "shallowRef", "shallowReactive", "toRef", "toRefs",
    }

    VUE_LIFECYCLE_HOOKS = {
        "onMounted", "onUpdated", "onUnmounted",
        "onBeforeMount", "onBeforeUpdate", "onBeforeUnmount",
        "onActivated", "onDeactivated", "onErrorCaptured",
        "onRenderTracked", "onRenderTriggered", "onServerPrefetch",
    }

    VUE_MACROS = {"defineProps", "defineEmits", "defineExpose", "defineSlots", "defineModel", "withDefaults"}

    def __init__(self, file_path: str, content: str, repo_path: str = None):
        self.file_path = Path(file_path)
        self.content = content
        self.repo_path = repo_path or ""
        self.nodes: List[Node] = []
        self.call_relationships: List[CallRelationship] = []

        try:
            vue_lang = get_language("vue")
            self.vue_language = Language(vue_lang)
            self.parser = Parser(self.vue_language)
            self.tree = self.parser.parse(bytes(content, "utf8"))
        except Exception as e:
            logger.error(f"Failed to initialize Vue parser: {e}")
            self.parser = None
            self.tree = None

    def _extract_script_block(self) -> Optional[ScriptBlock]:
        if self.tree is None:
            return None

        root = self.tree.root_node
        for child in root.children:
            if child.type == "script_element":
                start_tag = None
                raw_text = None
                for sub in child.children:
                    if sub.type == "start_tag":
                        start_tag = sub
                    elif sub.type == "raw_text":
                        raw_text = sub

                if raw_text is None:
                    return None

                is_setup = False
                lang = ""
                if start_tag:
                    for attr in start_tag.children:
                        if attr.type == "attribute":
                            attr_name = None
                            attr_value = None
                            for attr_child in attr.children:
                                if attr_child.type == "attribute_name":
                                    attr_name = attr_child.text.decode("utf8")
                                elif attr_child.type == "quoted_attribute_value":
                                    for v in attr_child.children:
                                        if v.type == "attribute_value":
                                            attr_value = v.text.decode("utf8")
                            if attr_name == "setup":
                                is_setup = True
                            elif attr_name == "lang" and attr_value:
                                lang = attr_value

                content = raw_text.text.decode("utf8")
                # raw_text starts on the same line as <script> tag end, so first char is \n
                if content.startswith("\n"):
                    content = content[1:]
                    start_line = raw_text.start_point[0] + 1
                else:
                    start_line = raw_text.start_point[0]

                return ScriptBlock(
                    content=content,
                    lang=lang,
                    is_setup=is_setup,
                    start_line=start_line,
                )
        return None

    def _extract_template_node(self):
        if self.tree is None:
            return None

        root = self.tree.root_node
        for child in root.children:
            if child.type == "template_element":
                return child
        return None
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vue_analyzer.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/vue.py tests/test_vue_analyzer.py
git commit -m "feat(vue): add SFC block extraction with tree-sitter-vue"
```

---

## Task 3: Script Delegation to TS/JS Analyzers

Delegate the extracted script content to existing analyzers with line offset correction.

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/vue.py`
- Test: `tests/test_vue_analyzer.py`

**Step 1: Write the failing test**

Add to `tests/test_vue_analyzer.py`:

```python
def test_script_analysis_extracts_entities():
    sfc = """<template>
  <div>hi</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import MyComponent from './MyComponent.vue'

const message = ref('Hello')
const count = ref(0)

const doubled = computed(() => count.value * 2)

function handleClick() {
  message.value = 'Clicked'
}

onMounted(() => {
  console.log('mounted')
})
</script>
"""
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", sfc, "/repo")
    analyzer.analyze()

    # Should find function and variables from script
    node_names = {n.name for n in analyzer.nodes}
    assert "handleClick" in node_names

    # Line numbers should be offset to original .vue file positions
    for node in analyzer.nodes:
        if node.name == "handleClick":
            # handleClick is at line 14 in the .vue file (1-indexed), not line 9
            assert node.start_line >= 10  # rough check it's offset correctly


def test_script_analysis_js_fallback():
    sfc = """<template><div>hi</div></template>
<script>
function greet() { return 'hi' }
</script>
"""
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", sfc, "/repo")
    analyzer.analyze()
    node_names = {n.name for n in analyzer.nodes}
    assert "greet" in node_names
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vue_analyzer.py::test_script_analysis_extracts_entities -v`
Expected: FAIL (no `analyze()` method yet)

**Step 3: Implement script delegation**

Add to `TreeSitterVueAnalyzer` class in `vue.py`:

```python
    def analyze(self) -> None:
        if self.tree is None:
            return

        try:
            script_block = self._extract_script_block()
            template_node = self._extract_template_node()
            module_path = self._get_module_path()
            component_id = module_path

            # Stage 1: Analyze script block with existing TS/JS analyzer
            if script_block and script_block.content.strip():
                self._analyze_script(script_block)

            # Stage 2: Analyze template for component refs, events, bindings
            if template_node:
                self._analyze_template(template_node, component_id)

            # Create top-level vue_component node
            self._create_component_node(component_id, module_path)

            # Post-process: enrich Vue-specific metadata
            self._enrich_vue_metadata()

        except Exception as e:
            logger.error(f"Error analyzing Vue file {self.file_path}: {e}", exc_info=True)

    def _analyze_script(self, script_block: ScriptBlock) -> None:
        use_ts = script_block.lang in ("ts", "tsx") or script_block.is_setup

        if use_ts:
            from codewiki.src.be.dependency_analyzer.analyzers.typescript import TreeSitterTSAnalyzer
            sub_analyzer = TreeSitterTSAnalyzer(
                str(self.file_path), script_block.content, self.repo_path
            )
        else:
            from codewiki.src.be.dependency_analyzer.analyzers.javascript import TreeSitterJSAnalyzer
            sub_analyzer = TreeSitterJSAnalyzer(
                str(self.file_path), script_block.content, self.repo_path
            )

        sub_analyzer.analyze()

        # Fix line offsets — map back to original .vue file positions
        offset = script_block.start_line
        for node in sub_analyzer.nodes:
            node.start_line += offset
            node.end_line += offset
        for rel in sub_analyzer.call_relationships:
            if rel.call_line is not None:
                rel.call_line += offset

        self.nodes.extend(sub_analyzer.nodes)
        self.call_relationships.extend(sub_analyzer.call_relationships)

    def _get_module_path(self) -> str:
        rel_path = os.path.relpath(str(self.file_path), self.repo_path)
        for ext in [".vue"]:
            if rel_path.endswith(ext):
                rel_path = rel_path[: -len(ext)]
                break
        return rel_path.replace("/", ".").replace("\\", ".")

    def _create_component_node(self, component_id: str, module_path: str) -> None:
        rel_path = os.path.relpath(str(self.file_path), self.repo_path)
        self.nodes.insert(
            0,
            Node(
                id=component_id,
                name=self.file_path.stem,
                component_type="vue_component",
                file_path=str(self.file_path),
                relative_path=rel_path,
                source_code=self.content,
                start_line=1,
                end_line=self.content.count("\n") + 1,
                component_id=component_id,
            ),
        )

    def _analyze_template(self, template_node, component_id: str) -> None:
        pass  # Implemented in Task 4

    def _enrich_vue_metadata(self) -> None:
        pass  # Implemented in Task 5
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vue_analyzer.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/vue.py tests/test_vue_analyzer.py
git commit -m "feat(vue): delegate script analysis to TS/JS analyzers with line offset"
```

---

## Task 4: Template Analysis

Extract component references, event handlers, prop bindings, and interpolations from the template AST.

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/vue.py`
- Test: `tests/test_vue_analyzer.py`

**Step 1: Write the failing test**

Add to `tests/test_vue_analyzer.py`:

```python
TEMPLATE_SFC = """<template>
  <div>
    <MyComponent :title="pageTitle" @click="handleClick">
      <slot name="header" />
    </MyComponent>
    <BaseLayout />
    <p v-if="show">{{ message }}</p>
    <transition><div /></transition>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import MyComponent from './MyComponent.vue'
import BaseLayout from './BaseLayout.vue'

const pageTitle = ref('Hello')
const message = ref('World')
const show = ref(true)

function handleClick() {}
</script>
"""


def test_template_component_refs():
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", TEMPLATE_SFC, "/repo")
    analyzer.analyze()

    rels = analyzer.call_relationships
    component_rels = [r for r in rels if r.relationship_type == "uses_component"]
    component_names = {r.callee for r in component_rels}

    assert "MyComponent" in component_names
    assert "BaseLayout" in component_names
    # Built-ins should NOT be included
    assert "transition" not in component_names
    assert "slot" not in component_names


def test_template_event_handlers():
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", TEMPLATE_SFC, "/repo")
    analyzer.analyze()

    rels = analyzer.call_relationships
    # @click="handleClick" should create a calls relationship
    event_rels = [r for r in rels if r.callee.endswith("handleClick") and r.caller.endswith("App")]
    assert len(event_rels) >= 1


def test_template_interpolations():
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", TEMPLATE_SFC, "/repo")
    analyzer.analyze()

    rels = analyzer.call_relationships
    ref_rels = [r for r in rels if r.relationship_type == "references"]
    ref_names = {r.callee for r in ref_rels}

    assert "message" in ref_names
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vue_analyzer.py::test_template_component_refs -v`
Expected: FAIL (template_relationships empty, `_analyze_template` is a no-op)

**Step 3: Implement template analysis**

Replace the `_analyze_template` method in `vue.py`:

```python
    def _analyze_template(self, template_node, component_id: str) -> None:
        stack = [template_node]
        while stack:
            node = stack.pop()

            if node.type in ("element", "self_closing_tag"):
                self._extract_template_element(node, component_id)
            elif node.type == "interpolation":
                self._extract_interpolation(node, component_id)

            for child in node.children:
                stack.append(child)

    def _extract_template_element(self, node, component_id: str) -> None:
        tag_node = node if node.type == "self_closing_tag" else None
        for child in node.children:
            if child.type == "start_tag":
                tag_node = child
                break
            elif child.type == "self_closing_tag":
                tag_node = child
                break

        if tag_node is None:
            return

        tag_name = None
        for child in tag_node.children:
            if child.type == "tag_name":
                tag_name = child.text.decode("utf8")
                break

        if not tag_name:
            return

        # Component reference: PascalCase tag that isn't a built-in
        if tag_name[0].isupper() and tag_name.lower() not in self.VUE_BUILTINS:
            self.call_relationships.append(
                CallRelationship(
                    caller=component_id,
                    callee=tag_name,
                    call_line=tag_node.start_point[0] + 1,
                    relationship_type="uses_component",
                )
            )

        # Directive attributes: @event and :binding
        for child in tag_node.children:
            if child.type == "directive_attribute":
                self._extract_directive(child, component_id)

    def _extract_directive(self, node, component_id: str) -> None:
        directive_name = None
        value = None

        for child in node.children:
            if child.type == "directive_name":
                directive_name = child.text.decode("utf8")
            elif child.type == "quoted_attribute_value":
                for v in child.children:
                    if v.type == "attribute_value":
                        value = v.text.decode("utf8")

        if not directive_name or not value:
            return

        # Simple identifier check: no spaces, dots, parens, brackets, operators
        is_simple_identifier = bool(re.match(r"^[a-zA-Z_$][a-zA-Z0-9_$]*$", value))

        if directive_name == "@" and is_simple_identifier:
            # Event handler: @click="handleClick"
            self.call_relationships.append(
                CallRelationship(
                    caller=component_id,
                    callee=value,
                    call_line=node.start_point[0] + 1,
                    relationship_type="calls",
                )
            )
        elif directive_name == ":" and is_simple_identifier:
            # Prop binding: :title="pageTitle"
            self.call_relationships.append(
                CallRelationship(
                    caller=component_id,
                    callee=value,
                    call_line=node.start_point[0] + 1,
                    relationship_type="references",
                )
            )

    def _extract_interpolation(self, node, component_id: str) -> None:
        for child in node.children:
            if child.type == "raw_text":
                text = child.text.decode("utf8").strip()
                if re.match(r"^[a-zA-Z_$][a-zA-Z0-9_$]*$", text):
                    self.call_relationships.append(
                        CallRelationship(
                            caller=component_id,
                            callee=text,
                            call_line=child.start_point[0] + 1,
                            relationship_type="references",
                        )
                    )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vue_analyzer.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/vue.py tests/test_vue_analyzer.py
git commit -m "feat(vue): extract component refs, events, bindings from template"
```

---

## Task 5: Vue-Specific Metadata Enrichment

Detect defineProps/defineEmits macros and annotate reactivity patterns (ref, computed, etc.).

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/vue.py`
- Test: `tests/test_vue_analyzer.py`

**Step 1: Write the failing test**

Add to `tests/test_vue_analyzer.py`:

```python
ENRICHMENT_SFC = """<template>
  <div>{{ message }}</div>
</template>

<script setup lang="ts">
import { ref, computed, reactive } from 'vue'

const message = ref('Hello')
const state = reactive({ count: 0 })
const doubled = computed(() => state.count * 2)

defineProps<{
  title: string
}>()

defineEmits<{
  (e: 'update', value: string): void
}>()
</script>
"""


def test_vue_metadata_enrichment():
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", ENRICHMENT_SFC, "/repo")
    analyzer.analyze()

    nodes_by_name = {n.name: n for n in analyzer.nodes}

    # Top-level component node
    assert "App" in nodes_by_name
    assert nodes_by_name["App"].component_type == "vue_component"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vue_analyzer.py::test_vue_metadata_enrichment -v`
Expected: FAIL (`_enrich_vue_metadata` is a no-op)

**Step 3: Implement metadata enrichment**

Replace `_enrich_vue_metadata` in `vue.py`:

```python
    def _enrich_vue_metadata(self) -> None:
        # Build lookup of callee names per node for reactivity detection
        node_callees = {}
        for rel in self.call_relationships:
            if rel.caller not in node_callees:
                node_callees[rel.caller] = set()
            node_callees[rel.caller].add(rel.callee)

        for node in self.nodes:
            if node.component_type == "vue_component":
                continue

            # Detect Vue macros (defineProps, defineEmits, etc.)
            if node.name in self.VUE_MACROS:
                if node.name == "defineProps":
                    node.component_type = "vue_props"
                elif node.name == "defineEmits":
                    node.component_type = "vue_emits"

            # Detect reactivity wrappers on variables
            if node.component_type == "variable" and node.id in node_callees:
                callees = node_callees[node.id]
                for callee in callees:
                    callee_name = callee.split(".")[-1]
                    if callee_name in self.VUE_REACTIVITY_FNS:
                        node.node_type = callee_name
                        break
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vue_analyzer.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/vue.py tests/test_vue_analyzer.py
git commit -m "feat(vue): enrich metadata for defineProps/Emits and reactivity"
```

---

## Task 6: Module-Level Entry Function & End-to-End Test

Add the `analyze_vue_file()` module-level function (matching the convention of other analyzers) and run an end-to-end test with a realistic Vue SFC.

**Files:**
- Modify: `codewiki/src/be/dependency_analyzer/analyzers/vue.py`
- Test: `tests/test_vue_analyzer.py`

**Step 1: Write the failing test**

Add to `tests/test_vue_analyzer.py`:

```python
from codewiki.src.be.dependency_analyzer.analyzers.vue import analyze_vue_file


E2E_SFC = """<template>
  <div class="user-card">
    <UserAvatar :src="avatarUrl" @load="onAvatarLoad" />
    <h2>{{ displayName }}</h2>
    <StatusBadge :status="userStatus" />
    <button @click="handleEdit">Edit</button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import UserAvatar from './UserAvatar.vue'
import StatusBadge from './StatusBadge.vue'
import { useUserStore } from '@/stores/user'

const store = useUserStore()

const avatarUrl = ref('')
const userStatus = ref('active')

const displayName = computed(() => `${store.firstName} ${store.lastName}`)

function handleEdit() {
  store.openEditor()
}

function onAvatarLoad() {
  console.log('avatar loaded')
}

onMounted(() => {
  avatarUrl.value = store.avatar
})

defineProps<{
  userId: string
}>()
</script>

<style scoped>
.user-card { padding: 16px; }
</style>
"""


def test_e2e_analyze_vue_file():
    nodes, rels = analyze_vue_file("/repo/src/components/UserCard.vue", E2E_SFC, repo_path="/repo")

    # Should have the component node + script entities
    assert len(nodes) > 0
    node_names = {n.name for n in nodes}
    assert "UserCard" in node_names  # vue_component
    assert "handleEdit" in node_names
    assert "onAvatarLoad" in node_names

    # Should have template relationships
    assert len(rels) > 0
    rel_types = {(r.relationship_type, r.callee) for r in rels}
    assert ("uses_component", "UserAvatar") in rel_types
    assert ("uses_component", "StatusBadge") in rel_types

    # Event handlers from template
    event_callees = {r.callee for r in rels if r.relationship_type == "calls" and r.caller.endswith("UserCard")}
    assert "handleEdit" in event_callees
    assert "onAvatarLoad" in event_callees

    # Interpolation references
    ref_callees = {r.callee for r in rels if r.relationship_type == "references"}
    assert "displayName" in ref_callees


def test_e2e_returns_tuple():
    nodes, rels = analyze_vue_file("/repo/src/App.vue", SIMPLE_VUE_SFC, repo_path="/repo")
    assert isinstance(nodes, list)
    assert isinstance(rels, list)


def test_e2e_empty_file():
    nodes, rels = analyze_vue_file("/repo/src/Empty.vue", "", repo_path="/repo")
    assert nodes == []
    assert rels == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vue_analyzer.py::test_e2e_analyze_vue_file -v`
Expected: FAIL (ImportError - `analyze_vue_file` not defined)

**Step 3: Add module-level function**

Add at the bottom of `vue.py`:

```python
def analyze_vue_file(
    file_path: str, content: str, repo_path: str = None
) -> Tuple[List[Node], List[CallRelationship]]:
    try:
        logger.debug(f"Vue analysis for {file_path}")
        analyzer = TreeSitterVueAnalyzer(file_path, content, repo_path)
        analyzer.analyze()
        logger.debug(
            f"Found {len(analyzer.nodes)} nodes, {len(analyzer.call_relationships)} relationships"
        )
        return analyzer.nodes, analyzer.call_relationships
    except Exception as e:
        logger.error(f"Error in Vue analysis for {file_path}: {e}", exc_info=True)
        return [], []
```

**Step 4: Run all tests**

Run: `python -m pytest tests/test_vue_analyzer.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/dependency_analyzer/analyzers/vue.py tests/test_vue_analyzer.py
git commit -m "feat(vue): add analyze_vue_file entry point and e2e tests"
```

---

## Task 7: Type Check & Final Verification

Run type checking and verify the full pipeline works.

**Step 1: Type check**

Run: `mypy codewiki/src/be/dependency_analyzer/analyzers/vue.py --ignore-missing-imports`
Expected: Success with no errors (or only pre-existing issues)

**Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 3: Quick smoke test with a real Vue file**

```bash
python -c "
from codewiki.src.be.dependency_analyzer.analyzers.vue import analyze_vue_file

sfc = '''<template>
  <div><Child @click=\"go\" /><p>{{ msg }}</p></div>
</template>
<script setup lang=\"ts\">
import { ref } from \"vue\"
import Child from \"./Child.vue\"
const msg = ref(\"hi\")
function go() {}
</script>'''

nodes, rels = analyze_vue_file('/test/App.vue', sfc, '/test')
print(f'Nodes ({len(nodes)}):')
for n in nodes:
    print(f'  {n.component_type}: {n.name} (L{n.start_line}-{n.end_line})')
print(f'Relationships ({len(rels)}):')
for r in rels:
    print(f'  {r.caller} --{r.relationship_type}--> {r.callee} (L{r.call_line})')
"
```

Expected: Nodes include `App` (vue_component), `msg` (variable), `go` (function). Relationships include `uses_component→Child`, `calls→go`, `references→msg`.

**Step 4: Commit final state**

```bash
git add -A
git commit -m "feat(vue): complete Vue 3 SFC analyzer with full pipeline integration"
```
