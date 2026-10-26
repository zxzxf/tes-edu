<template>
  <div class="proj">
    <header class="topbar">
      <div class="brand"><span class="mark">T</span> Tes Edu · 我的项目</div>
      <div class="user">
        <span class="greet">{{ auth.displayName }}</span>
        <button class="logout" @click="onBack">返回</button>
      </div>
    </header>

    <main class="content">
      <div class="head">
        <h1>我的学习项目</h1>
        <button class="btn primary" @click="openCreate">＋ 新建项目</button>
      </div>

      <div class="layout">
        <!-- 左：项目列表 -->
        <aside class="list">
          <p v-if="!projects.length" class="empty">还没有项目，点击右上角新建一个吧～</p>
          <button
            v-for="p in projects"
            :key="p.project_id"
            class="row"
            :class="{ active: selected && selected.project_id === p.project_id }"
            @click="select(p.project_id)"
          >
            <div class="row-top">
              <span class="title">{{ p.title }}</span>
              <span class="badge" :class="p.status ? 'on' : 'arch'">
                {{ p.status ? '进行中' : '已归档' }}
              </span>
            </div>
            <div class="bar"><i :style="{ width: (p.progress * 100).toFixed(0) + '%' }"></i></div>
            <div class="meta">{{ p.task_done }}/{{ p.task_count }} 任务 · {{ (p.progress * 100).toFixed(0) }}%</div>
          </button>
        </aside>

        <!-- 右：选中项目的任务区 -->
        <section class="detail" v-if="selected">
          <div class="detail-head">
            <div>
              <h2>{{ selected.title }}</h2>
              <p class="goal">{{ selected.goal || selected.description || '暂无描述' }}</p>
            </div>
            <div class="ops">
              <button class="btn" @click="openEdit(selected)">编辑</button>
              <button class="btn" v-if="selected.status" @click="archive(selected)">归档</button>
              <button class="btn danger" @click="remove(selected)">删除</button>
            </div>
          </div>

          <div class="tasks">
            <div class="task-head">
              <h3>任务清单</h3>
              <button class="btn small" @click="openTaskAdd">＋ 添加任务</button>
            </div>
            <label v-for="t in selected.tasks" :key="t.task_id" class="task" :class="{ done: t.done }">
              <input type="checkbox" :checked="t.done" @change="toggleTask(t)" />
              <span class="t-title">{{ t.title }}</span>
              <button class="x" @click="delTask(t)">×</button>
            </label>
            <p v-if="!selected.tasks.length" class="empty">还没有任务，添加第一个目标吧。</p>
          </div>
        </section>

        <section class="detail placeholder" v-else>
          <p>从左侧选择一个项目查看任务</p>
        </section>
      </div>
    </main>

    <!-- 新建/编辑项目弹窗 -->
    <div v-if="editing" class="mask" @click.self="editing = null">
      <div class="dialog">
        <h3>{{ editing.project_id ? '编辑项目' : '新建项目' }}</h3>
        <label>标题</label>
        <input v-model="editing.title" placeholder="如：攻克电磁感应" />
        <label>目标</label>
        <textarea v-model="editing.goal" rows="2" placeholder="想达成什么？"></textarea>
        <label>描述</label>
        <textarea v-model="editing.description" rows="2" placeholder="补充说明（可选）"></textarea>
        <label>关联知识点编码（可选）</label>
        <input v-model="editing.kp_code" placeholder="如：KB-EM-001" />
        <div class="dialog-ops">
          <button class="btn" @click="editing = null">取消</button>
          <button class="btn primary" @click="saveProject">保存</button>
        </div>
      </div>
    </div>

    <!-- 添加任务弹窗 -->
    <div v-if="taskAdding" class="mask" @click.self="taskAdding = false">
      <div class="dialog">
        <h3>添加任务</h3>
        <label>任务标题</label>
        <input v-model="taskTitle" placeholder="如：完成第 3 章习题" />
        <div class="dialog-ops">
          <button class="btn" @click="taskAdding = false">取消</button>
          <button class="btn primary" @click="saveTask">添加</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import {
  listProjects, getProject, createProject, updateProject, deleteProject,
  addTask, updateTask, deleteTask,
} from '../api/projects'

const auth = useAuthStore()
const router = useRouter()

const projects = ref([])
const selected = ref(null)
const editing = ref(null) // 新建/编辑表单
const taskAdding = ref(false)
const taskTitle = ref('')

async function refresh() {
  projects.value = await listProjects()
}
async function select(id) {
  selected.value = await getProject(id)
}
function openCreate() {
  editing.value = { title: '', description: '', goal: '', kp_code: '' }
}
function openEdit(p) {
  editing.value = { ...p }
}
async function saveProject() {
  const e = editing.value
  if (!e.title.trim()) return
  if (e.project_id) {
    await updateProject(e.project_id, {
      title: e.title, description: e.description, goal: e.goal, kp_code: e.kp_code,
    })
  } else {
    const r = await createProject({
      title: e.title, description: e.description, goal: e.goal, kp_code: e.kp_code,
    })
    if (r.project_id) await select(r.project_id)
  }
  editing.value = null
  await refresh()
}
async function archive(p) {
  await updateProject(p.project_id, { status: 0 })
  await refresh()
  if (selected.value?.project_id === p.project_id) await select(p.project_id)
}
async function remove(p) {
  if (!confirm('确定删除该项目及其任务？')) return
  await deleteProject(p.project_id)
  selected.value = null
  await refresh()
}
async function toggleTask(t) {
  await updateTask(selected.value.project_id, t.task_id, { done: !t.done })
  await select(selected.value.project_id)
}
async function delTask(t) {
  await deleteTask(selected.value.project_id, t.task_id)
  await select(selected.value.project_id)
}
function openTaskAdd() {
  taskTitle.value = ''
  taskAdding.value = true
}
async function saveTask() {
  if (!taskTitle.value.trim()) return
  await addTask(selected.value.project_id, { title: taskTitle.value })
  taskAdding.value = false
  await select(selected.value.project_id)
}
function onBack() {
  router.push('/')
}

onMounted(refresh)
</script>

<style scoped>
.proj { min-height: 100vh; background: var(--bg); }
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 28px; background: var(--surface);
  box-shadow: 0 2px 12px rgba(255, 61, 129, .06);
}
.brand { display: flex; align-items: center; gap: 8px; font-weight: 800; }
.brand .mark {
  width: 30px; height: 30px; border-radius: 8px; background: var(--brand-grad);
  color: #fff; display: grid; place-items: center; font-weight: 900;
}
.user { display: flex; align-items: center; gap: 14px; }
.greet { color: var(--ink-2); font-size: 14px; }
.logout {
  border: 1.5px solid var(--line-strong); background: #fff; color: var(--ink-2);
  border-radius: 10px; padding: 7px 14px; cursor: pointer; font-weight: 600;
}
.logout:hover { border-color: var(--brand-2); color: var(--brand-2); }

.content { max-width: 1080px; margin: 0 auto; padding: 32px 24px; }
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 22px; }
.head h1 { font-size: 26px; font-weight: 900; }

.layout { display: grid; grid-template-columns: 320px 1fr; gap: 22px; align-items: start; }
.list { display: flex; flex-direction: column; gap: 12px; }
.row {
  text-align: left; background: var(--surface); border: 1.5px solid transparent;
  border-radius: var(--r-lg); padding: 14px 16px; cursor: pointer;
  box-shadow: 0 6px 18px rgba(255, 61, 129, .06);
}
.row:hover { border-color: var(--brand-2); }
.row.active { border-color: var(--brand-2); box-shadow: 0 10px 26px rgba(255, 61, 129, .14); }
.row-top { display: flex; align-items: center; justify-content: space-between; }
.title { font-weight: 700; }
.badge { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; }
.badge.on { color: #07a07a; background: #e6f7f1; }
.badge.arch { color: var(--muted); background: #f1f1f4; }
.bar { height: 6px; background: #f0f0f4; border-radius: 999px; margin: 10px 0 6px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--brand-grad); border-radius: 999px; }
.meta { font-size: 12px; color: var(--muted); }

.detail {
  background: var(--surface); border-radius: var(--r-lg); padding: 22px;
  box-shadow: 0 8px 24px rgba(255, 61, 129, .08); min-height: 320px;
}
.detail.placeholder { display: grid; place-items: center; color: var(--muted); }
.detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.detail-head h2 { font-size: 20px; }
.goal { color: var(--muted); font-size: 13px; margin-top: 6px; max-width: 560px; }
.ops { display: flex; gap: 8px; flex-shrink: 0; }

.tasks { margin-top: 22px; }
.task-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.task {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px;
  border-radius: 10px; background: #fafafc; margin-bottom: 8px;
}
.task.done .t-title { color: var(--muted); text-decoration: line-through; }
.t-title { flex: 1; font-size: 14px; }
.x {
  border: none; background: transparent; color: var(--muted); cursor: pointer;
  font-size: 18px; line-height: 1;
}
.x:hover { color: #e5484d; }

.btn {
  border: 1.5px solid var(--line-strong); background: #fff; color: var(--ink-2);
  border-radius: 10px; padding: 8px 14px; cursor: pointer; font-weight: 600;
}
.btn:hover { border-color: var(--brand-2); color: var(--brand-2); }
.btn.small { padding: 6px 10px; font-size: 13px; }
.btn.primary { background: var(--brand-grad); color: #fff; border: none; }
.btn.primary:hover { opacity: .92; color: #fff; }
.btn.danger { color: #e5484d; border-color: #f3c2c4; }
.btn.danger:hover { background: #fff0f0; color: #e5484d; }

.mask {
  position: fixed; inset: 0; background: rgba(20, 12, 28, .45);
  display: grid; place-items: center; z-index: 50;
}
.dialog {
  width: 420px; max-width: 92vw; background: #fff; border-radius: 16px;
  padding: 24px; box-shadow: 0 20px 60px rgba(0, 0, 0, .25);
}
.dialog h3 { margin-bottom: 14px; }
.dialog label { display: block; font-size: 13px; color: var(--ink-2); margin: 12px 0 6px; font-weight: 600; }
.dialog input, .dialog textarea {
  width: 100%; border: 1.5px solid var(--line-strong); border-radius: 10px;
  padding: 9px 12px; font: inherit; resize: vertical;
}
.dialog input:focus, .dialog textarea:focus { outline: none; border-color: var(--brand-2); }
.dialog-ops { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.empty { color: var(--muted); font-size: 14px; padding: 18px 0; }
</style>
