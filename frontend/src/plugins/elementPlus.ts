// Element Plus 按需注册（仅注册项目实际使用的组件，替代全量引入）
//
// 使用清单（grep 全 src 确认，仅 KnowledgeBaseView 使用）：
//   组件：el-button / el-table / el-table-column / el-tag / el-input
//   函数：ElMessage / ElMessageBox（视图内按需导入 JS，样式在此统一加载）
//   指令：v-loading
//   依赖样式：tooltip（表格 show-overflow-tooltip 内部使用）
import type { App } from 'vue'
import {
  ElButton,
  ElInput,
  ElLoadingDirective,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/loading/style/css'
import 'element-plus/es/components/tooltip/style/css'

export function setupElementPlus(app: App): void {
  app.use(ElButton)
  app.use(ElInput)
  app.use(ElTag)
  app.use(ElTable)
  app.use(ElTableColumn)
  app.directive('loading', ElLoadingDirective)
}
