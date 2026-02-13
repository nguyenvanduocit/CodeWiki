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
