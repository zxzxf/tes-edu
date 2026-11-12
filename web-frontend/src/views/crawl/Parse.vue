<template>
  <div class="cw-page">
    <h2 class="cw-title">课程解析预览</h2>
    <p class="cw-hint">
      把教材 / 大纲文本解析成「章 → 节 → 知识点」三层结构化大纲，纯预览不落库；
      确认后一键导入为 <code>draft</code> 候选，再到知识点审核页发布。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head"><b>课程解析 pipeline（M5-2）</b></div>
      <label class="cw-block">
        <span>粘贴教材/大纲文本（支持 <code># 章</code> / <code>## 节</code>，或「第X章 / 第X节」）</span>
        <textarea v-model="text" rows="8" placeholder="# 第一章 数列&#10;## 第一节 等差数列&#10;等差数列的通项公式&#10;等差数列的前n项和"></textarea>
      </label>
      <div class="cw-row">
        <label>
          <span>解析器</span>
          <select v-model="outlineType">
            <option value="textbook">教材</option>
            <option value="outline">大纲</option>
            <option value="video">视频</option>
            <option value="web">网页</option>
            <option value="bank">题库包</option>
          </select>
        </label>
        <label>
          <span>学科（后端解析结果不带学科）</span>
          <select v-model="subject">
            <option value="数学">数学</option>
            <option value="物理">物理</option>
            <option value="化学">化学</option>
            <option value="语文">语文</option>
            <option value="英语">英语</option>
          </select>
        </label>
        <button class="ghost" :disabled="busy" @click="parse">解析</button>
        <button v-if="parsed" class="tes-btn-primary" :disabled="busy" @click="importParsed">
          导入 {{ candidateCount }} 个候选
        </button>
      </div>
      <p v-if="parsed" class="cw-hint">
        解析出 {{ parsed.chapter_count }} 章 / {{ parsed.point_count }} 个知识点，
        按三层结构拍平为 {{ candidateCount }} 个 draft 候选，审核通过后才进学生端图谱。
      </p>
      <pre v-if="parsed" class="cw-outline">{{ JSON.stringify(parsed, null, 2) }}</pre>
    </section>

    <div class="cw-links">
      <router-link class="cw-link" to="/course-intake/review">去知识点审核 →</router-link>
      <router-link class="cw-link" to="/course-intake/workbench">返回工作台</router-link>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useCrawlRequest } from '../../composables/useCrawlRequest'
import { flattenOutline } from '../../utils/courseOutline'
import { parseCourseSource, upsertKpNodes } from '../../api/admin'

const { busy, error, msg, guard } = useCrawlRequest()
const text = ref('')
const outlineType = ref('textbook')
const subject = ref('数学')
const parsed = ref(null)
const candidateCount = computed(() => flattenOutline(parsed.value, subject.value).length)

async function parse() {
  await guard(async () => {
    parsed.value = await parseCourseSource({ text: text.value, source_type: outlineType.value })
    msg.value = `解析出 ${parsed.value.chapter_count} 章 / ${parsed.value.point_count} 个知识点，可导入 ${candidateCount.value} 个候选`
  })
}
async function importParsed() {
  await guard(async () => {
    // 拍平规则按 /course/parse 真实三层结构，code 用后端签发的稳定短码
    const items = flattenOutline(parsed.value, subject.value)
    if (!items.length) throw new Error('没有可导入的知识点候选')
    const out = await upsertKpNodes(items)
    const created = out?.created ?? items.length
    const merged = out?.merged ?? 0
    msg.value = `已提交 ${items.length} 个候选：新建 ${created} 个、与已发布知识点合并 ${merged} 个（draft，待审核）`
  })
}
</script>
