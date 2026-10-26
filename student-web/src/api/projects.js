import client from './client'

// 学生项目 + 任务清单 CRUD（按 student_id+tenant_id 隔离，后端把关）。
export async function listProjects() {
  return client.get('/projects')
}
export async function getProject(id) {
  return client.get(`/projects/${id}`)
}
export async function createProject(payload) {
  return client.post('/projects', payload)
}
export async function updateProject(id, payload) {
  return client.put(`/projects/${id}`, payload)
}
export async function deleteProject(id) {
  return client.delete(`/projects/${id}`)
}
export async function addTask(id, payload) {
  return client.post(`/projects/${id}/tasks`, payload)
}
export async function updateTask(id, tid, payload) {
  return client.put(`/projects/${id}/tasks/${tid}`, payload)
}
export async function deleteTask(id, tid) {
  return client.delete(`/projects/${id}/tasks/${tid}`)
}
