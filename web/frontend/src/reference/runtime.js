/** 样例页面统一入口；实现按领域拆分，旧 import 合同不变。 */
import { Current, Store, setContext, setLiveData, closeOverlays } from './shared.js'
import { renderAgentsPage, renderSkillsPage } from './agentsSkills.js'
import { renderKnowledgePage, renderMemoryPage } from './knowledgeMemory.js'
import { renderTasksPage } from './tasks.js'
import { renderSensePage, renderExpandPage } from './modules.js'
import { renderMessagesPage } from './messages.js'
import { renderStatusPage } from './status.js'
import { renderRuntimeModulesPage } from './runtimeModules.js'
export { setLiveData, toast } from './shared.js'
export function renderReferencePage(route,ctx,data) {
 setContext(ctx);setLiveData(data);
 const renderers={'/tasks':renderTasksPage,'/knowledge':renderKnowledgePage,'/memory':renderMemoryPage,'/agents':renderAgentsPage,'/skills':renderSkillsPage,'/sense':renderSensePage,'/expand':renderExpandPage,'/messages':renderMessagesPage,'/status':renderStatusPage,'/runtime':renderRuntimeModulesPage};
 return renderers[route](ctx);
}
export function disposeReferencePage(ctx) {ctx.active=false;if(Current===ctx){closeOverlays();document.querySelectorAll('.menu').forEach(el=>el.remove());}}
