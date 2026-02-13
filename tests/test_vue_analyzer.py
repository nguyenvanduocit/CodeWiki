import pytest
from codewiki.src.be.dependency_analyzer.analyzers.vue import TreeSitterVueAnalyzer, analyze_vue_file


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
    assert block.start_line == 5  # 0-indexed line where script content starts (after <script> tag on line 4)


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
    sfc = '''<template><div>hi</div></template>
<script lang="js">
export default { name: 'App' }
</script>'''
    analyzer = TreeSitterVueAnalyzer("/repo/src/App.vue", sfc, "/repo")
    block = analyzer._extract_script_block()
    assert block is not None
    assert block.is_setup is False
    assert block.lang == "js"


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
