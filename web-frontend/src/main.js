import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './styles/global.css'
import App from './App.vue'
import router from './router'
import { permissionDirective } from './directives/permission'

const app = createApp(App)
app.use(createPinia())
app.use(router)
// T-AM7：按钮级权限指令（v-has-permission="'system:menu:add'"）
app.directive('has-permission', permissionDirective)
app.mount('#app')
