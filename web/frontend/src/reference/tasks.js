import { LIVE_TASKS, h, pageHead, refreshPage, icon, statTile, $$, fill, Current, $, emptyState, makePager, Overlay, mutate, Api, makeSearchBox, statusBadge, fmtRelative, fmtDuration, toast, Store, append, readDetail, fmtDateTime, on, closeOverlays, renderMarkdownWithWidgets } from './shared.js'
import { taskCleanupButton } from './taskCleanup.js'
/* ============================================================================
   pages/tasks · 任务计划 / 定时任务 / 执行记录
   ========================================================================= */

export function renderTasksPage(ctx) {
  const tabs = [
    { key: 'plans', label: '任务计划', count: LIVE_TASKS.summary.active_plans + LIVE_TASKS.summary.waiting_plans },
    { key: 'cron', label: '定时任务', count: LIVE_TASKS.cron_tasks.length },
    { key: 'exec', label: '执行记录', count: LIVE_TASKS.executions.length },
  ];
  let active = ctx.viewState.tasksTab || ctx.params.tab || 'plans';

  const page = h('div.page', h('div.page-inner.wide'));
  const inner = page.firstElementChild;

  inner.appendChild(pageHead({
    title: '任务中枢',
    desc: '任务计划、定时调度与执行记录是三个独立视图。计划由对话生成后等待确认，执行中约 1.2 秒轮询一次状态。',
    actions: [
      h('button.btn.btn-outline', {
        onclick: () => { void refreshPage(); },
      }, h('span', { html: icon('refresh', 'icon-sm') }), '刷新'),
      h('button.btn.btn-primary', {
        onclick: () => openCronDialog(null, ctx),
      }, h('span', { html: icon('plus', 'icon-sm') }), '创建定时任务'),
    ],
  }));

  inner.appendChild(h('div.stat-row',
    statTile('执行中计划', LIVE_TASKS.summary.active_plans, { icon: 'play', color: 'var(--accent-text)', foot: '正在推进步骤' }),
    statTile('等待计划', LIVE_TASKS.summary.waiting_plans, { icon: 'clock', foot: '待确认或待开始' }),
    statTile('启用调度', LIVE_TASKS.summary.enabled_crons, { icon: 'tasks', foot: '定时任务' }),
    statTile('已完成计划', LIVE_TASKS.summary.completed_plans, { icon: 'checkCircle', color: 'var(--success)', foot: '历史累计' }),
  ));

  const tabBar = h('div.tabs', tabs.map((t) => h('button.tab' + (t.key === active ? '.is-active' : ''), {
    onclick: (ev) => {
      active = t.key;
      ctx.viewState.tasksTab = active;
      $$('.tab', tabBar).forEach((x) => x.classList.remove('is-active'));
      ev.currentTarget.classList.add('is-active');
      draw();
    },
  }, t.label, h('span.tab-count', String(t.count)))));
  inner.appendChild(tabBar);

  const body = h('div');
  inner.appendChild(body);

  function draw() {
    fill(body);
    if (active === 'plans') body.appendChild(renderPlansView());
    else if (active === 'cron') body.appendChild(renderCronView(ctx));
    else body.appendChild(renderExecutionsView());
  }
  draw();

  ctx.redraw = draw;
  return page;
}

/* ============================ 计划 ============================ */

/** 三个任务视图各自保存搜索/类型/页码；筛选先于六条分页。 */
export function paginateTasks(list, key) {
  const state=Current.viewState[key] ||= {page:1,search:'',kind:'all'};
  const entries=$$('.entry',list), search=$('input[type=search]',list);
  const footer=h('div');list.appendChild(footer);
  const apply=()=>{
    const filtered=entries.filter(el=>(el.dataset.text || '').includes(state.search.toLowerCase()) &&
      (state.kind==='all' || el.dataset.kind===state.kind));
    const total=Math.max(1,Math.ceil(filtered.length/6));
    state.page=Math.min(Math.max(1,state.page),total);
    entries.forEach(el=>el.style.display='none');
    filtered.slice((state.page-1)*6,state.page*6).forEach(el=>el.style.display='');
    fill(footer,
      filtered.length ? h('div.faint',{style:{fontSize:'var(--t-2xs)',padding:'8px 0'}},`共 ${filtered.length} 条 · 每页 6 条`) : emptyState({title:'暂无匹配记录'}),
      makePager(state.page,total,p=>{state.page=p;apply()}));
  };
  if(search) {
    search.value=state.search;
    search.addEventListener('input',()=>{state.search=search.value;state.page=1;apply()});
  }
  $$('.segments .segment',list).forEach((button,i)=>{
    const kind=['all','plan_step','cron'][i];
    button.classList.toggle('is-active',state.kind===kind);
    button.addEventListener('click',()=>{state.kind=kind;state.page=1;apply()});
  });
  apply();
}

export function openPlanEditor(plan) {
  const title=h('input.input',{value:plan.title,'aria-label':'计划标题'});
  const description=h('textarea.textarea',{rows:3,value:plan.description,'aria-label':'计划描述'});
  const steps=h('textarea.textarea.mono',{rows:12,'aria-label':'步骤修改 JSON',
    placeholder:'可选：[{"step_id":"s1","title":"新标题","tool_arguments":{}}]'});
  const error=h('div.banner.banner-danger',{role:'alert',style:{display:'none'}});
  const context=Current;
  const modal=Overlay.modal({title:'修正任务计划',sub:`${plan.plan_id} · revision ${plan.revision}`,size:'lg',
    body:h('div.form-grid',
      h('div.field',h('label.field-label','标题'),title),
      h('div.field',h('label.field-label','描述'),description),
      h('div.field',h('label.field-label','步骤补丁（可选 JSON 数组）'),steps),
      h('div.field-hint','提交携带原修订号与计划自己的会话/来源，冲突不会覆盖新版本。'),error),
    foot:[h('button.btn.btn-ghost',{onclick:()=>modal.close()},'取消'),
      h('button.btn.btn-primary',{onclick:async()=>{
        if(!context.active)return;
        try {
          if(!title.value.trim())throw new Error('标题不能为空');
          const changes={revision:plan.revision,title:title.value.trim(),description:description.value};
          if(steps.value.trim()){
            changes.steps=JSON.parse(steps.value);
            if(!Array.isArray(changes.steps))throw new Error('步骤修改必须是 JSON 数组');
          }
          await mutate(()=>Api.editPlan(context.user,plan.plan_id,changes,plan.session_id,plan.source || 'web'),'计划修改已保存',()=>modal.close());
        } catch(cause){error.textContent=cause.message || 'JSON 格式错误';error.style.display=''}
      }},'保存修正')]});
}

export function renderPlansView() {
  let selectedId = Current.viewState.planId || LIVE_TASKS.plans[0]?.plan_id;
  const wrap = h('div.split.task-hub-split');
  const listCol = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-3)' } });
  const detailCol = h('div');

  const drawList = () => {
    fill(listCol);
    const search = makeSearchBox({
      placeholder: '搜索计划标题或 ID…',
      onInput: (v) => {
        const q = v.toLowerCase();
        $$('.entry', listCol).forEach((el) => {
          el.style.display = el.dataset.text.includes(q) ? '' : 'none';
        });
      },
    });
    listCol.appendChild(h('div.toolbar.task-cleanup-toolbar', search, taskCleanupButton('plans')));
    LIVE_TASKS.plans.forEach((p) => {
      const el = h('div.entry' + (p.plan_id === selectedId ? '.is-active' : ''), {
        onclick: () => { selectedId = p.plan_id; Current.viewState.planId = selectedId; drawList(); drawDetail(); },
        dataset: { text: (p.title + p.plan_id).toLowerCase() },
      },
        h('div.entry-icon' + (p.status === 'running' ? '.accent' : p.status === 'failed' ? '.warning' : p.status === 'completed' ? '.success' : ''), {
          style: { width: '30px', height: '30px' },
        }, h('span', { html: icon('tasks', 'icon-sm') })),
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div.entry-title', p.title),
          h('div.entry-meta',
            statusBadge(p.status),
            h('span.mono', p.plan_id),
            h('span', { style: { fontWeight: '600', color: 'var(--text-secondary)' } }, `${p.progress.completed}/${p.progress.total}`),
            h('span', fmtRelative(p.updated_at)),
          ),
        ),
      );
      listCol.appendChild(el);
    });
    paginateTasks(listCol, 'plans');
  };

  const drawDetail = () => {
    fill(detailCol);
    const p = LIVE_TASKS.plans.find((x) => x.plan_id === selectedId);
    if (!p) { detailCol.appendChild(emptyState({ title: '选择一条计划', desc: '点击左侧计划查看步骤、依赖与修订历史。', icon: 'tasks' })); return; }

    const panel = h('div.editor-panel');

    panel.appendChild(h('div.editor-head',
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div.editor-title', p.title),
        h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: '2px' } },
          `${p.plan_id} · revision ${p.revision} · ${p.session_id}`),
      ),
      statusBadge(p.status),
    ));

    const body = h('div.editor-body');
    body.appendChild(h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } },
      p.description ? h('div.task-detail-description', renderMarkdownWithWidgets(p.description)) : null,
      h('div.progress-track', h('div.progress-fill', { style: { width: p.progress.percent + '%' } })),
      h('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)' } },
        h('span', { class: 'tnum' }, `${p.progress.completed} / ${p.progress.total} 步 · ${p.progress.percent}%`),
        h('span', p.auto_accept ? '自动批准已开启' : '需要确认'),
      ),
      p.reminder ? h('div.banner.banner-info',
        h('span', { html: icon('bell') }),
        h('div', h('div', { style: { fontWeight: '600' } }, '执行提醒'), renderMarkdownWithWidgets(p.reminder)),
      ) : null,
      h('div',
        h('div.section-title', { style: { marginBottom: 'var(--s-3)' } },
          h('span', { html: icon('list', 'icon-sm') }), '步骤'),
        h('div.plan-steps', { style: { border: '1px solid var(--border-subtle)', borderRadius: 'var(--r-md)' } },
          p.steps.map((s, i) => {
            const cls = s.status === 'completed' ? '.is-done' : s.status === 'running' ? '.is-running' : s.status === 'failed' ? '.is-failed' : '';
            return h('div.plan-step' + cls,
              h('div.plan-step-num', s.status === 'completed' ? h('span', { html: icon('check', 'icon-sm') }) : String(i + 1)),
              h('div.plan-step-main',
                h('div.plan-step-title', s.title),
                s.description ? h('div.plan-step-desc', renderMarkdownWithWidgets(s.description)) : null,
                h('div.plan-step-meta',
                  s.tool_name ? h('span.skill-code', s.tool_name) : null,
                  s.critical ? h('span.badge.badge-warning', '关键') : null,
                  s.depends_on && s.depends_on.length ? h('span.step-deps', '依赖 ', s.depends_on.map((d) => h('span.dep-chip', d))) : null,
                  s.started_at && s.finished_at ? h('span.faint', fmtDuration(new Date(s.finished_at) - new Date(s.started_at))) : null,
                ),
                s.error ? h('div.plan-step-error', s.error) : null,
              ),
            );
          }),
        ),
      ),
    ));
    panel.appendChild(body);

    const failed = p.steps.find((s) => s.status === 'failed');
    panel.appendChild(h('div.editor-foot', { style: { flexWrap: 'wrap' } },
      h('button.btn.btn-ghost.btn-sm', {
        onclick: () => openRevisionsDialog(p),
      }, h('span', { html: icon('history', 'icon-sm') }), '历史版本'),
      p.status !== 'running' ? h('button.btn.btn-ghost.btn-sm', {onclick:()=>openPlanEditor(p)}, h('span',{html:icon('edit','icon-sm')}), '修正计划') : null,
      ['pending','approved','paused','failed'].includes(p.status) ? h('button.btn.btn-outline.btn-sm', {
        disabled: Current.running || (p.source || 'web')!=='web',
        onclick:()=>void Current.navigatePlan(p).catch(error=>toast(error.message,{kind:'error'})),
      }, h('span',{html:icon('play','icon-sm')}), '进入对话执行') : null,
      h('div', { style: { flex: '1' } }),
      failed ? h('button.btn.btn-outline.btn-sm', {
        onclick: () => { void mutate(() => Api.retryPlanStep(Store.user, p.plan_id, failed.step_id, p.revision, p.session_id, p.source), '步骤已重试'); },
      }, h('span', { html: icon('rotateCcw', 'icon-sm') }), '重试步骤') : null,
      p.status === 'running' ? h('button.btn.btn-outline.btn-sm', {
        onclick: () => mutate(() => Api.commandPlan(Store.user, p.plan_id, 'pause', p.session_id, p.source), '计划已暂停'),
      }, h('span', { html: icon('pause', 'icon-sm') }), '暂停') : null,
      ['running','pending','approved','paused'].includes(p.status) ? h('button.btn.btn-danger.btn-sm',{onclick:async()=>{
        const ok=await Overlay.confirm({title:'取消任务计划',description:'未执行的步骤将不再继续；已完成的记录保留。',detail:p.plan_id,confirmLabel:'确认取消',tone:'danger'});
        if(ok)await mutate(()=>Api.commandPlan(Store.user,p.plan_id,'cancel',p.session_id,p.source),'计划已取消');
      }},'取消计划') : null,
      p.status !== 'running' ? h('button.btn.btn-danger.btn-sm', {
        onclick: async () => {
          const ok = await Overlay.confirm({
            title: '删除该计划', description: '删除后步骤与执行记录仍会保留在执行记录视图中。',
            detail: p.plan_id, confirmLabel: '删除计划', tone: 'danger',
          });
          if (ok) await mutate(() => Api.deletePlan(Store.user, p.plan_id, p.session_id, p.source), '计划已删除');
        },
      }, h('span', { html: icon('trash', 'icon-sm') }), '删除') : null,
    ));

    detailCol.appendChild(panel);
  };

  drawList();
  drawDetail();
  wrap.append(listCol, detailCol);
  return wrap;
}

export function openRevisionsDialog(plan) {
  return readDetail(() => Api.getPlanRevisions(Store.user, plan.plan_id, plan.session_id, plan.source), data => {
    let selected = data.revisions[0];
    const list = h('div.list');
    const snap = h('div');
    const draw = () => {
      if (!selected) { fill(snap, emptyState({title: '暂无修订历史'})); return; }
      const revision = selected.revision;
      void readDetail(() => Api.getPlanRevision(Store.user, plan.plan_id, revision, plan.session_id, plan.source), result => {
        if (selected.revision !== revision) return;
        fill(snap, h('div.doc-content', renderMarkdownWithWidgets('```json\n' + JSON.stringify(result.plan, null, 2) + '\n```', { readOnly: true })),
          h('button.btn.btn-outline', {disabled: plan.status === 'running' || revision === plan.revision,
            onclick: async () => {
              const ok = await Overlay.confirm({title:'回滚计划版本',description:'创建新的修订并恢复该快照，正在执行的计划不可回滚。',detail:`revision ${revision}`});
              if (ok) await mutate(() => Api.rollbackPlan(Store.user, plan.plan_id, revision, plan.revision, plan.session_id, plan.source), '计划已回滚');
            }}, '回滚到此版本'));
      });
    };
    data.revisions.forEach(rev => list.appendChild(h('button.list-row', {onclick:()=>{selected=rev;draw();}}, `revision ${rev.revision} · ${rev.note || ''}`)));
    Overlay.modal({title:'计划修订历史',sub:plan.plan_id,size:'lg',body:h('div.split',list,snap)});
    draw();
  });
}

/* ============================ 定时任务 ============================ */

export const CRON_TYPE_LABEL = { once: '单次', daily: '每日', weekly: '每周', monthly: '每月', recurring: '周期' };

export function renderCronView(ctx) {
  let selectedId = ctx.viewState.cronId || LIVE_TASKS.cron_tasks[0]?.task_id;
  const details = new Map(), pending = new Set();
  const wrap = h('div.split.task-hub-split');
  const listCol = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-3)' } });
  const detailCol = h('div');

  const drawList = () => {
    fill(listCol);
    listCol.appendChild(h('div.toolbar.task-cleanup-toolbar',
      makeSearchBox({ placeholder: '搜索定时任务…', onInput: (v) => {
        const q = v.toLowerCase();
        $$('.entry', listCol).forEach((el) => { el.style.display = el.dataset.text.includes(q) ? '' : 'none'; });
      } }),
      taskCleanupButton('crons'),
    ));
    // 按下次执行时间排序；空的排到最后
    const sorted = LIVE_TASKS.cron_tasks.slice().sort((a, b) => {
      if (!a.next_run_at && !b.next_run_at) return 0;
      if (!a.next_run_at) return 1;
      if (!b.next_run_at) return -1;
      return new Date(a.next_run_at) - new Date(b.next_run_at);
    });

    sorted.forEach((t) => {
      const el = h('div.entry' + (t.task_id === selectedId ? '.is-active' : ''), {
        onclick: () => { selectedId = t.task_id; ctx.viewState.cronId = selectedId; drawList(); drawDetail(); },
        dataset: { text: (t.title + t.task_id).toLowerCase() },
      },
        h('div.entry-icon' + (t.status === 'enabled' ? '.success' : t.status === 'failed' ? '.warning' : ''), {
          style: { width: '30px', height: '30px' },
        }, h('span', { html: icon('clock', 'icon-sm') })),
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div.entry-title', t.title),
          h('div.entry-meta',
            statusBadge(t.status),
            h('span.badge.badge-outline', CRON_TYPE_LABEL[t.type] || t.type),
            !t.user_defined ? h('span.badge', '系统维护') : null,
            h('span', t.next_run_at ? '下次 ' + fmtRelative(t.next_run_at).replace('后', '') : '无下次执行'),
          ),
        ),
      );
      listCol.appendChild(el);
    });
    paginateTasks(listCol, 'cron');
  };

  const drawDetail = () => {
    fill(detailCol);
    const summary = LIVE_TASKS.cron_tasks.find((x) => x.task_id === selectedId);
    const t = summary && {...summary, ...details.get(selectedId)};
    if (!t) { detailCol.appendChild(emptyState({ title: '选择一条定时任务', icon: 'clock' })); return; }
    if (t.user_defined && !details.has(selectedId) && !pending.has(selectedId)) {
      const id=selectedId; pending.add(id);
      void readDetail(()=>Api.getCron(Store.user,id), result=>{
        details.set(id,result.cron_task); if(selectedId===id)drawDetail();
      });
    }

    const schedule = (() => {
      if (t.type === 'once') return fmtDateTime(t.next_run_at);
      if (t.type === 'weekly') return `每周 ${(t.weekdays || []).map((d) => '日一二三四五六'[d % 7]).join('、')} ${t.time || ''}`;
      if (t.type === 'monthly') return `每月 ${(t.month_days || []).join('、')} 日 ${t.time || ''}`;
      if (t.type === 'recurring') return `每 ${Math.round((t.interval_seconds || 0) / 60)} 分钟`;
      if (t.times && t.times.length) return `每日 ${t.times.join('、')}`;
      return `每日 ${t.time || ''}`;
    })();

    const panel = h('div.editor-panel');
    panel.appendChild(h('div.editor-head',
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div.editor-title', t.title),
        h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: '2px' } }, t.task_id),
      ),
      statusBadge(t.status),
    ));

    panel.appendChild(h('div.editor-body',
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } },
        h('div.desc-list',
          h('div.desc-row', h('span.desc-key', '调度'), h('span.desc-val', schedule)),
          h('div.desc-row', h('span.desc-key', '任务类型'), h('span.desc-val', CRON_TYPE_LABEL[t.type] || t.type)),
          h('div.desc-row', h('span.desc-key', '下次执行'), h('span.desc-val', t.next_run_at ? fmtDateTime(t.next_run_at) : '—')),
          h('div.desc-row', h('span.desc-key', '上次执行'), h('span.desc-val', t.latest_run_at ? fmtDateTime(t.latest_run_at) : '—')),
          h('div.desc-row', h('span.desc-key', '上次状态'), h('span.desc-val', statusBadge(t.last_state))),
          h('div.desc-row', h('span.desc-key', '来源'), h('span.desc-val', t.user_defined ? '用户创建' : '系统维护（不可编辑）')),
          h('div.desc-row', h('span.desc-key', '创建时间'), h('span.desc-val', fmtDateTime(t.created_at))),
        ),
        h('div',
          h('div.doc-label', { style: { marginBottom: '6px' } }, '执行内容'),
          t.user_defined
            ? h('div.doc-content.task-cron-preview', details.has(selectedId)
              ? renderMarkdownWithWidgets(t.prompt || '（无 prompt）', { readOnly: true })
              : '正在读取任务详情；失败时可点击编辑重试。')
            : h('div.banner.banner-info',
              h('span', { html: icon('shield') }),
              h('div', '系统维护任务不通过网页回显内部 prompt，仅展示摘要信息。'),
            ),
        ),
      ),
    ));

    panel.appendChild(h('div.editor-foot', { style: { flexWrap: 'wrap' } },
      t.user_defined && t.status !== 'running'
        ? h('button.btn.btn-ghost.btn-sm', { onclick: () => readDetail(() => Api.getCron(Store.user, t.task_id), result => openCronDialog(result.cron_task, ctx)) },
          h('span', { html: icon('edit', 'icon-sm') }), '编辑')
        : null,
      t.status === 'failed' ? h('button.btn.btn-outline.btn-sm', {
        onclick: () => mutate(() => Api.updateCron(Store.user, t.task_id, {status:'enabled'}), '任务已重新启用'),
      }, h('span', { html: icon('rotateCcw', 'icon-sm') }), '重新启用') : null,
      t.status === 'enabled' ? h('button.btn.btn-outline.btn-sm', {
        onclick: () => mutate(() => Api.updateCron(Store.user, t.task_id, {status:'paused'}), '定时任务已暂停'),
      }, h('span', { html: icon('pause', 'icon-sm') }), '暂停') : null,
      t.status === 'paused' ? h('button.btn.btn-outline.btn-sm', {
        onclick: () => mutate(() => Api.updateCron(Store.user, t.task_id, {status:'enabled'}), '定时任务已恢复'),
      }, h('span', { html: icon('play', 'icon-sm') }), '恢复') : null,
      h('div', { style: { flex: '1' } }),
      h('button.btn.btn-danger.btn-sm', {
        onclick: async () => {
          const ok = await Overlay.confirm({
            title: '删除定时任务',
            description: '删除任务定义不会删除已有的执行记录，执行日志是只读保存的。',
            detail: t.task_id, confirmLabel: '删除任务', tone: 'danger',
          });
          if (ok) await mutate(() => Api.deleteCron(Store.user, t.task_id), '定时任务已删除');
        },
      }, h('span', { html: icon('trash', 'icon-sm') }), '删除'),
    ));

    detailCol.appendChild(panel);
  };

  drawList();
  drawDetail();
  wrap.append(listCol, detailCol);
  return wrap;
}

/** 创建 / 编辑定时任务对话框 */
export function openCronDialog(task, ctx) {
  const isEdit = !!task;
  const model = {
    title: task?.title || '',
    prompt: task?.prompt || '',
    type: task?.type || 'daily',
    time: task?.time || '03:30',
    times: [...(task?.times || [])],
    weekdays: task?.weekdays || [],
    month_days: task?.month_days || [],
    interval_seconds: task?.interval_seconds || 3600,
    next_run_at: task?.next_run_at
      ? new Date(new Date(task.next_run_at).getTime() + 8 * 3600 * 1000).toISOString().slice(0, 19)
      : '',
    start_date: task?.start_date || '',
    end_date: task?.end_date || '',
    max_runs: task?.max_runs ?? '',
  };

  const body = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } });

  const titleField = h('div.field',
    h('label.field-label', '任务名称', h('span', { style: { color: 'var(--danger)' } }, '*')),
    h('input.input', { 'aria-label': '任务名称', required: true, placeholder: '例如：每日运行日志汇总', value: model.title, oninput: (e) => { model.title = e.target.value; validate(); } }),
  );

  const promptField = h('div.field',
    h('label.field-label', '执行内容', h('span', '*')),
    h('textarea.textarea', { 'aria-label': '执行内容', required: true, rows: 3, placeholder: '定时触发时要执行的自然语言指令…', oninput: (e) => { model.prompt = e.target.value; validate(); } }),
  );
  promptField.querySelector('textarea').value = model.prompt;

  /* 调度类型选择 */
  const typeRow = h('div.field',
    h('label.field-label', '调度类型'),
    h('div.segments', ['once', 'daily', 'weekly', 'monthly', 'recurring'].map((tp) =>
      h('button.segment' + (tp === model.type ? '.is-active' : ''), {
        onclick: (ev) => {
          model.type = tp;
          $$('.segment', ev.currentTarget.parentElement).forEach((x) => x.classList.remove('is-active'));
          ev.currentTarget.classList.add('is-active');
          drawSchedule();
          validate();
        },
      }, CRON_TYPE_LABEL[tp]),
    )),
  );

  const scheduleHost = h('div');
  const errorHost = h('div', { style: { display: 'none' } });

  function drawSchedule() {
    fill(scheduleHost);
    const fields = [];

    if (model.type === 'once') {
      fields.push(h('div.field',
        h('label.field-label', '执行时刻'),
        h('input.input', { 'aria-label': '执行时刻', type: 'datetime-local', step: '1', value: model.next_run_at, oninput: (e) => { model.next_run_at = e.target.value; validate(); } }),
        h('div.field-hint', '带时区时刻，按北京时间（+08:00）填写。'),
      ));
    }
    if (['daily', 'weekly', 'monthly'].includes(model.type)) {
      fields.push(h('div.field',
        h('label.field-label', '执行时刻'),
        h('div', { style: { display: 'flex', gap: 'var(--s-2)' } },
          (model.times.length ? model.times : [model.time]).map((clock, index) =>
            h('input.input', { 'aria-label': model.times.length ? `执行时刻 ${index + 1}` : '执行时刻',
              type: 'time', step: '60', value: clock, oninput: (e) => {
                if (model.times.length) model.times[index] = e.target.value;
                else model.time = e.target.value;
                validate();
              } }),
          ),
        ),
        h('div.field-hint', '按北京时间填写，精确到分钟。'),
      ));
    }
    if (model.type === 'weekly') {
      fields.push(h('div.field',
        h('label.field-label', '星期'),
        h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
          [1, 2, 3, 4, 5, 6, 7].map((d) => {
            const on = model.weekdays.includes(d);
            const b = h('button.chip' + (on ? '.is-active' : ''), {
              onclick: () => {
                if (model.weekdays.includes(d)) model.weekdays = model.weekdays.filter((x) => x !== d);
                else model.weekdays = [...model.weekdays, d].sort((a, b2) => a - b2);
                b.classList.toggle('is-active');
                validate();
              },
            }, '周' + '日一二三四五六'[d % 7]);
            return b;
          }),
        ),
        h('div.field-hint', 'weekdays 取值 1–7，必须升序且无重复。'),
      ));
    }
    if (model.type === 'monthly') {
      fields.push(h('div.field',
        h('label.field-label', '日期'),
        h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(10, 1fr)', gap: '4px' } },
          Array.from({ length: 31 }, (_, i) => i + 1).map((d) => {
            const on = model.month_days.includes(d);
            const b = h('button', {
              style: {
                padding: '4px 0', borderRadius: 'var(--r-xs)', fontSize: 'var(--t-2xs)',
                fontVariantNumeric: 'tabular-nums',
                background: on ? 'var(--accent-soft)' : 'var(--bg-inset)',
                color: on ? 'var(--accent-text)' : 'var(--text-tertiary)',
                border: '1px solid ' + (on ? 'transparent' : 'var(--border-subtle)'),
                fontWeight: on ? '640' : '500',
              },
              onclick: () => {
                if (model.month_days.includes(d)) model.month_days = model.month_days.filter((x) => x !== d);
                else model.month_days = [...model.month_days, d].sort((a, b2) => a - b2);
                b.style.background = model.month_days.includes(d) ? 'var(--accent-soft)' : 'var(--bg-inset)';
                b.style.color = model.month_days.includes(d) ? 'var(--accent-text)' : 'var(--text-tertiary)';
                b.style.border = '1px solid ' + (model.month_days.includes(d) ? 'transparent' : 'var(--border-subtle)');
                validate();
              },
            }, String(d));
            return b;
          }),
        ),
        h('div.field-hint', 'month_days 取值 1–31，升序无重复。'),
      ));
    }
    if (model.type === 'recurring') {
      fields.push(h('div.field',
        h('label.field-label', '间隔（秒）'),
        h('input.input', { type: 'number', min: 60, value: model.interval_seconds, oninput: (e) => { model.interval_seconds = Number(e.target.value); validate(); } }),
        h('div.field-hint', 'Web 层要求整数且 ≥ 60 秒，核心底层更宽但界面按 60 约束。'),
      ));
    }

    if (['daily', 'weekly', 'monthly'].includes(model.type)) {
      fields.push(h('div.form-grid', { style: { gridTemplateColumns: '1fr 1fr' } },
        h('div.field', h('label.field-label', '生效开始日期'), h('input.input', { type: 'date', value: model.start_date, oninput: (e) => { model.start_date = e.target.value; validate(); } })),
        h('div.field', h('label.field-label', '生效结束日期'), h('input.input', { type: 'date', value: model.end_date, oninput: (e) => { model.end_date = e.target.value; validate(); } })),
      ));
    }

    if (model.type !== 'once') fields.push(h('div.field',
      h('label.field-label', '最大执行次数'),
      h('input.input', { type: 'number', min: 1, placeholder: '留空表示不限', value: model.max_runs, oninput: (e) => { model.max_runs = e.target.value; validate(); } }),
      h('div.field-hint', '可空或 ≥1 的整数，不能是布尔值。'),
    ));

    scheduleHost.append(...fields);
  }

  let modal = null;   // validate() 会读取它做按钮禁用，必须先声明

  function validate() {
    const errs = [];
    if (!model.title.trim()) errs.push('任务名称不能为空');
    if (!model.prompt.trim()) errs.push('执行内容不能为空');
    const terminalOnce = task?.type === 'once' && ['completed', 'cancelled'].includes(task.status);
    if (model.type === 'once' && !model.next_run_at && !terminalOnce) errs.push('请选择执行时刻');
    if (['daily', 'weekly', 'monthly'].includes(model.type)) {
      const clocks = model.times.length ? model.times : [model.time];
      if (clocks.some(clock => !/^([01]\d|2[0-3]):[0-5]\d$/.test(clock))) errs.push('执行时刻必须是 HH:MM');
      if (new Set(clocks).size !== clocks.length) errs.push('执行时刻不能重复');
    }
    if (model.type === 'weekly' && !model.weekdays.length) errs.push('每周任务必须选择至少一个星期');
    if (model.type === 'monthly' && !model.month_days.length) errs.push('每月任务必须选择至少一个日期');
    if (model.type === 'recurring' && (!Number.isInteger(model.interval_seconds) || model.interval_seconds < 60)) {
      errs.push('间隔必须是 ≥60 的整数秒');
    }
    if (['daily', 'weekly', 'monthly'].includes(model.type) && model.start_date && model.end_date && model.start_date > model.end_date) errs.push('开始日期不能晚于结束日期');
    if (model.type !== 'once' && model.max_runs !== '' && (!Number.isInteger(Number(model.max_runs)) || Number(model.max_runs) < 1)) {
      errs.push('最大执行次数必须是 ≥1 的整数');
    }

    if (errs.length) {
      fill(errorHost, h('div.banner.banner-danger',
        h('span', { html: icon('alertCircle') }),
        h('div', errs.map((e) => h('div', e))),
      ));
      errorHost.style.display = 'block';
    } else {
      errorHost.style.display = 'none';
    }
    const btn = modal ? modal.overlay.querySelector('.btn-primary') : null;
    if (btn) btn.disabled = errs.length > 0;
    return errs.length === 0;
  }

  body.append(titleField, promptField, typeRow, scheduleHost, errorHost);
  drawSchedule();
  validate();

  modal = Overlay.modal({
    title: isEdit ? '编辑定时任务' : '创建定时任务',
    sub: isEdit ? task.task_id : '提交后由服务端重算 next_run_at',
    body,
    size: 'lg',
    foot: [
      h('button.btn.btn-ghost', { onclick: () => Overlay.stack[Overlay.stack.length - 1].close() }, '取消'),
      h('button.btn.btn-primary', {
        onclick: () => {
          if (!validate()) return;
          const payload = {title: model.title.trim(), prompt: model.prompt.trim(), type: model.type};
          if (!isEdit) payload.status = 'enabled';
          if (model.type === 'once') payload.next_run_at = model.next_run_at
            ? model.next_run_at.slice(0, 19) + (model.next_run_at.length === 16 ? ':00' : '') + '+08:00' : '';
          if (['daily','weekly','monthly'].includes(model.type)) {
            if (model.times.length) payload.times = [...model.times].sort(); else payload.time = model.time;
            payload.start_date = model.start_date || null; payload.end_date = model.end_date || null;
          }
          if (model.type === 'weekly') payload.weekdays = model.weekdays;
          if (model.type === 'monthly') payload.month_days = model.month_days;
          if (model.type === 'recurring') payload.interval_seconds = model.interval_seconds;
          if (model.type !== 'once') payload.max_runs = model.max_runs === '' ? null : Number(model.max_runs);
          void mutate(() => isEdit ? Api.updateCron(Store.user, task.task_id, payload) : Api.createCron(Store.user, payload),
            isEdit ? '定时任务已更新' : '定时任务已创建', () => closeOverlays());

        },
      }, isEdit ? '保存修改' : '创建任务'),
    ],
  });
  validate();
}

/* ============================ 执行记录 ============================ */

export function renderExecutionsView() {
  let selectedId = Current.viewState.executionId || LIVE_TASKS.executions[0]?.record_id;
  const wrap = h('div.split.task-hub-split');
  const listCol = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-2)' } });
  const detailCol = h('div');

  const drawList = () => {
    fill(listCol);
    listCol.appendChild(h('div.toolbar.task-cleanup-toolbar',
      makeSearchBox({ placeholder: '搜索执行记录…', onInput: (v) => {
        const q = v.toLowerCase();
        $$('.entry', listCol).forEach((el) => { el.style.display = el.dataset.text.includes(q) ? '' : 'none'; });
      } }),
      h('div.spacer'),
      taskCleanupButton('executions'),
      h('div.segments',
        ['all', 'plan_step', 'cron'].map((k) => h('button.segment' + (k === 'all' ? '.is-active' : ''), {
          onclick: (ev) => {
            $$('.segment', ev.currentTarget.parentElement).forEach((x) => x.classList.remove('is-active'));
            ev.currentTarget.classList.add('is-active');
            $$('.entry', listCol).forEach((el) => {
              el.style.display = (k === 'all' || el.dataset.kind === k) ? '' : 'none';
            });
          },
        }, k === 'all' ? '全部' : k === 'plan_step' ? '计划步骤' : '调度')),
      ),
    ));

    LIVE_TASKS.executions.slice().sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at)).forEach((ex) => {
      const el = h('div.entry' + (ex.record_id === selectedId ? '.is-active' : ''), {
        onclick: () => { selectedId = ex.record_id; Current.viewState.executionId=selectedId; drawList(); drawDetail(); },
        dataset: { text: (ex.title + ex.task_id).toLowerCase(), kind: ex.kind },
      },
        h('div.entry-icon' + (ex.status === 'completed' ? '.success' : ex.status === 'failed' ? '.warning' : ''), {
          style: { width: '28px', height: '28px' },
        }, h('span', { html: icon(ex.kind === 'cron' ? 'clock' : 'play', 'icon-sm') })),
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div.entry-title', ex.title),
          h('div.entry-meta',
            statusBadge(ex.status),
            h('span', ex.kind === 'cron' ? '定时调度' : '计划步骤'),
            ex.duration_ms ? h('span', fmtDuration(ex.duration_ms)) : null,
            h('span', fmtRelative(ex.updated_at)),
          ),
        ),
      );
      listCol.appendChild(el);
    });
    paginateTasks(listCol, 'executions');
  };

  const drawDetail = () => {
    fill(detailCol);
    const ex = LIVE_TASKS.executions.find((x) => x.record_id === selectedId);
    if (!ex) { detailCol.appendChild(emptyState({ title: '选择一条执行记录', desc: '点击左侧记录查看只读详情。', icon: 'list' })); return; }

    detailCol.appendChild(h('div.editor-panel',
      h('div.editor-head',
        h('div.editor-title', ex.title),
        statusBadge(ex.status),
      ),
      h('div.editor-body',
        h('div.desc-list',
          h('div.desc-row', h('span.desc-key', '记录类型'), h('span.desc-val', ex.kind === 'cron' ? '定时任务执行' : '计划步骤执行')),
          h('div.desc-row', h('span.desc-key', '任务 ID'), h('span.desc-val.mono', ex.task_id)),
          h('div.desc-row', h('span.desc-key', '状态'), h('span.desc-val', statusBadge(ex.status))),
          h('div.desc-row', h('span.desc-key', '耗时'), h('span.desc-val', ex.duration_ms ? fmtDuration(ex.duration_ms) : '—')),
          h('div.desc-row', h('span.desc-key', '执行时间'), h('span.desc-val', fmtDateTime(ex.updated_at))),
          h('div.desc-row', h('span.desc-key', '记录来源'), h('span.desc-val', ex.user_defined ? '用户创建' : '系统维护')),
        ),
        ex.error ? h('div', { style: { marginTop: 'var(--s-4)' } },
          h('div.doc-label', { style: { marginBottom: '6px' } }, '错误信息'),
          h('div.tool-pre.is-error', typeof ex.error === 'string' ? ex.error : JSON.stringify(ex.error,null,2)),
        ) : null,
        ex.result ? h('div', { style: { marginTop: 'var(--s-4)' } },
          h('div.doc-label', { style: { marginBottom: '6px' } }, '结果摘要'),
          h('div.doc-content', renderMarkdownWithWidgets(typeof ex.result === 'string' ? ex.result : '```json\n' + JSON.stringify(ex.result, null, 2) + '\n```', { readOnly: true })),
        ) : null,
      ),
      h('div.editor-foot',
        h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', flex: '1' } },
          '详情只读；可清理已完成的列表记录，原始日志和计划步骤结果保留。'),
      ),
    ));
  };

  drawList();
  drawDetail();
  wrap.append(listCol, detailCol);
  return wrap;
}


