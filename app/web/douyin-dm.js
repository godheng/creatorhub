// Session-only comment recipient draft. Adding never creates a task.
const dyDmDraft = new Map(), dyDmSelected = new Map();
let dyDmCommentRows = [];
const dyDmCompose = {account:'', links:'', content:''};
function dyDmSaveCompose() {
  if (!$('dy-dm-content')) return;
  dyDmCompose.account=$('dy-dm-account').value; dyDmCompose.links=$('dy-dm-links').value; dyDmCompose.content=$('dy-dm-content').value;
}
function dyDmSec(comment) {
  const value = String(comment.user_sec_uid || '').trim();
  return /^[A-Za-z0-9_-]+$/.test(value) && !/^\d+$/.test(value) ? value : '';
}
function dyDmCommentActions(comment) {
  const key = dyDmSec(comment);
  if (!key) return '<p class="mut">缺少有效 sec_uid，无法加入私信名单</p>';
  return `<div><label><input type="checkbox" data-dm-select="${esc(key)}" ${dyDmSelected.has(key)?'checked':''} onchange="dyDmSelectComment(this)">选择用户</label> <a href="https://www.douyin.com/user/${encodeURIComponent(key)}" target="_blank" rel="noopener noreferrer">查看主页</a> <button class="ghost sm" data-dm-add="${esc(key)}" onclick="dyDmAddComments([this.dataset.dmAdd])">${dyDmDraft.has(key)?'已加入':'加入私信名单'}</button></div>`;
}
function dyDmPageUsers() {
  return new Map(dyDmCommentRows.map(row=>[dyDmSec(row),row]).filter(([key])=>key));
}
function dyDmSelectPage(checked) {
  for (const [key,row] of dyDmPageUsers()) {
    if (checked) dyDmSelected.set(key,row); else dyDmSelected.delete(key);
  }
  dyDmSyncComments();
  document.querySelectorAll('[data-dm-select]').forEach(e=>e.checked=dyDmSelected.has(e.dataset.dmSelect));
}
function dyDmCommentToolbar() {
  return `<div><label><input type="checkbox" data-dm-select-page onchange="dyDmSelectPage(this.checked)" ${dyDmPageUsers().size?'':'disabled'}>全选本页</label> <button class="ghost sm" onclick="dyDmSelectPage(false)" ${dyDmPageUsers().size?'':'disabled'}>取消本页选择</button> <span data-dm-selected>已选 ${dyDmSelected.size} 人</span> <button class="btn sm" onclick="dyDmAddComments([...dyDmSelected.keys()])">批量加入私信名单</button> <button onclick="openDouyinDm(true)">查看名单 / 去编辑私信（<span data-dm-count>${dyDmDraft.size}</span> 人）</button><p data-dm-feedback role="status"></p></div>`;
}
function dyDmSelectComment(input) {
  const key=input.dataset.dmSelect;
  if(input.checked) dyDmSelected.set(key,dyDmCommentRows.find(r=>dyDmSec(r)===key)); else dyDmSelected.delete(key);
  dyDmSyncComments();
  document.querySelectorAll('[data-dm-select]').forEach(e=>e.checked=dyDmSelected.has(e.dataset.dmSelect));
}
function dyDmSyncComments(message='') {
  const pageKeys=[...dyDmPageUsers().keys()], count=pageKeys.filter(key=>dyDmSelected.has(key)).length;
  document.querySelectorAll('[data-dm-select-page]').forEach(e=>{
    e.disabled=!pageKeys.length; e.checked=pageKeys.length>0 && count===pageKeys.length;
    e.indeterminate=count>0 && count<pageKeys.length;
  });
  document.querySelectorAll('[data-dm-add]').forEach(e=>e.textContent=dyDmDraft.has(e.dataset.dmAdd)?'已加入':'加入私信名单');
  document.querySelectorAll('[data-dm-selected]').forEach(e=>e.textContent=`已选 ${dyDmSelected.size} 人`);
  document.querySelectorAll('[data-dm-count]').forEach(e=>e.textContent=dyDmDraft.size);
  document.querySelectorAll('[data-dm-feedback]').forEach(e=>e.textContent=message);
}
function dyDmAddComments(keys) {
  let added=0,duplicates=0;
  for(const key of new Set(keys)) {
    if(dyDmDraft.has(key)){duplicates++;continue;}
    const row=dyDmSelected.get(key)||dyDmCommentRows.find(r=>dyDmSec(r)===key);
    if(row && dyDmSec(row)){dyDmDraft.set(key,{...row});added++;}
  }
  dyDmSyncComments(keys.length?`新增 ${added} 人，重复 ${duplicates} 人，尚未发送消息`:'请先选择评论用户');
}
function dyDmRenderDraft() {
  const el=$('dy-dm-draft'); if(!el)return;
  el.innerHTML=!dyDmState.batch?'单发模式：请手动输入一个主页链接或选择一个联系人':dyDmDraft.size?[...dyDmDraft].map(([key,r])=>`<div><b>${esc(r.user_nickname||'匿名用户')}</b><p>${esc(r.source_desc||r.source_keyword||'来源素材 #'+(r.content_id||''))}</p><p>${esc(r.text||'（空评论）')}</p><button data-key="${esc(key)}" onclick="dyDmRemoveDraft(this.dataset.key)">移除</button></div>`).join(''):'私信名单为空，可返回评论选择用户或手动输入主页链接';
}
function dyDmRemoveDraft(key) {dyDmDraft.delete(key);dyDmState.payload=null;$('dy-dm-preview').innerHTML='';dyDmRenderDraft();dyDmSyncComments();}
// Explicit preview/confirmation; all writes remain in the existing serialized queue.
let dyDmDialog, dyDmState, dyDmTimer, dyDmGeneration = 0, dyDmContactRequest = 0;
const dyDmCurrent = generation => generation === dyDmGeneration && dyDmDialog?.open;
function dyDmShell(title) {
  ++dyDmGeneration;
  if (!dyDmDialog) {
    dyDmDialog = document.createElement('dialog');
    dyDmDialog.id = 'douyin-dm-dialog';
    dyDmDialog.style.cssText = 'width:min(720px,94vw);max-height:90vh;overflow:auto;padding:24px;border:1px solid var(--border,#ddd);border-radius:12px;background:var(--bg,#fff);color:var(--text,#222)';
    dyDmDialog.addEventListener('close', () => { if (!dyDmDialog.open) { dyDmSaveCompose(); ++dyDmGeneration; clearInterval(dyDmTimer); } });
    document.body.appendChild(dyDmDialog);
  }
  clearInterval(dyDmTimer);
  dyDmDialog.innerHTML = `<h2>${title}</h2><div id="dy-dm-body"></div><p id="dy-dm-error" role="alert"></p><button type="button" onclick="dyDmDialog.close()">关闭</button>`;
  if (!dyDmDialog.open) dyDmDialog.showModal();
}
function dyDmError(e) { $('dy-dm-error').textContent = e.message || String(e); }
async function openDouyinDm(batch) {
  batch = Boolean(batch);
  dyDmSaveCompose();
  dyDmShell(batch ? '批量私信' : '新建私信');
  dyDmState = {batch, busy:false};
  const accounts = ACCOUNTS.filter(a => (!a.platform || a.platform === 'douyin') && a.status === 'active');
  $('dy-dm-body').innerHTML = `<label>发送账号<select id="dy-dm-account" onchange="dyDmContacts()">${accounts.map(a => `<option value="${a.id}">${esc(a.nickname || '账号#'+a.id)}</option>`).join('')}</select></label>
    <section><h3>评论用户私信名单</h3><div id="dy-dm-draft"></div><button onclick="dyDmDialog.close()">返回评论</button></section><p><label>用户主页链接（${batch ? '每行一个' : '一个接收人'}）<textarea id="dy-dm-links" rows="3" style="width:100%" placeholder="https://www.douyin.com/user/..."></textarea></label></p>
    <fieldset><legend>或选择已有联系人</legend><div id="dy-dm-contacts" style="max-height:160px;overflow:auto"></div></fieldset>
    <p><label>文字内容<textarea id="dy-dm-content" rows="4" style="width:100%"></textarea></label></p><button class="btn" id="dy-dm-preview-button" onclick="dyDmPreview()">解析并预览</button><div id="dy-dm-preview"></div>`;
  if (accounts.some(a => String(a.id) === HUB_ACC)) $('dy-dm-account').value = HUB_ACC;
  if(accounts.some(a=>String(a.id)===dyDmCompose.account)) $('dy-dm-account').value=dyDmCompose.account;
  $('dy-dm-links').value=dyDmCompose.links; $('dy-dm-content').value=dyDmCompose.content;
  $('dy-dm-body').addEventListener('input', () => { dyDmSaveCompose(); if (!dyDmState.busy) { dyDmState.payload = null; $('dy-dm-preview').innerHTML = ''; } });
  dyDmRenderDraft();
  await dyDmContacts();
}
async function dyDmContacts() {
  const generation = dyDmGeneration, request = ++dyDmContactRequest;
  dyDmState.payload = null; $('dy-dm-preview').innerHTML = '';
  const id = $('dy-dm-account').value;
  if (!id) { $('dy-dm-contacts').textContent = '无可用账号，请先登录'; return; }
  try {
    const rows = await api('/api/dm/conversations?account_id='+id);
    if (!dyDmCurrent(generation) || request !== dyDmContactRequest || $('dy-dm-account')?.value !== id) return;
    $('dy-dm-contacts').innerHTML = rows.map(c => `<label style="display:block"><input type="${dyDmState.batch ? 'checkbox' : 'radio'}" name="dy-dm-contact" value="${c.id}"> ${esc(c.peer_nickname || c.peer_uid)}</label>`).join('') || '暂无联系人，可粘贴主页链接';
  } catch(e) { if (dyDmCurrent(generation)) dyDmError(e); }
}
async function dyDmPreview() {
  const generation = dyDmGeneration, state = dyDmState;
  if (dyDmState.busy) return;
  dyDmState.payload=null; $('dy-dm-preview').innerHTML='';
  const content = $('dy-dm-content').value.trim();
  if (!content) return dyDmError('请填写文字内容');
  const body = {account_id:Number($('dy-dm-account').value),comment_sec_uids:dyDmState.batch?[...dyDmDraft.keys()]:[],links:$('dy-dm-links').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean),contact_ids:[...document.querySelectorAll('[name="dy-dm-contact"]:checked')].map(e=>Number(e.value))};
  dyDmSaveCompose();
  dyDmState.busy=true; $('dy-dm-body').querySelectorAll('input,textarea,select,button').forEach(e=>e.disabled=true);
  try {
    const result = await api('/api/douyin/dm/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if (!dyDmCurrent(generation)) return;
    if (!dyDmState.batch && result.recipients.length > 1) throw new Error('单发仅允许一个接收人，请使用批量私信');
    dyDmState.payload={account_id:body.account_id,recipients:result.recipients,content};
    $('dy-dm-error').textContent='';
    $('dy-dm-preview').innerHTML=`<h3>确认创建发送任务</h3><p>发送账号：${esc($('dy-dm-account').selectedOptions[0]?.textContent || '未选择')}</p><p>有效 ${result.recipients.length} 人 · 重复 ${result.duplicates} 项 · 无效 ${result.invalid.length} 项</p><ul>${result.recipients.map(r=>`<li>${esc(r.target_nick || r.target_uid)} (${esc(r.target_uid)})</li>`).join('')}</ul><ul>${result.invalid.map(r=>`<li>${esc(r.input)}：${esc(r.error)}</li>`).join('')}</ul><pre style="white-space:pre-wrap">${esc(content)}</pre><button class="btn" id="dy-dm-submit" onclick="dyDmSubmit()" ${result.recipients.length?'':'disabled'}>确认创建发送任务</button>`;
  } catch(e) { if (dyDmCurrent(generation)) dyDmError(e); }
  finally { state.busy=false; if (dyDmCurrent(generation)) { $('dy-dm-body').querySelectorAll('input,textarea,select,button').forEach(e=>e.disabled=false); $('dy-dm-preview-button').disabled=false; if($('dy-dm-submit')) $('dy-dm-submit').disabled=!dyDmState.payload?.recipients.length; } }
}
async function dyDmSubmit() {
  const generation = dyDmGeneration, state = dyDmState;
  if (dyDmState.busy || !dyDmState.payload?.recipients.length) return;
  dyDmState.busy=true; $('dy-dm-body').querySelectorAll('input,textarea,select,button').forEach(e=>e.disabled=true);
  try {
    const result=await api('/api/douyin/dm/batches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(dyDmState.payload)});
    if (!dyDmCurrent(generation)) return;
    dyDmState.payload=null;
    await openDouyinDmResults(result.batch_id);
  } catch(e) { if (dyDmCurrent(generation)) { dyDmError(e); $('dy-dm-body').querySelectorAll('input,textarea,select,button').forEach(e=>e.disabled=false); } }
  finally { state.busy=false; }
}
const dyDmLabels={pending:'待发送',doing:'发送中',done:'成功',failed:'失败',uncertain:'结果待核对',canceled:'已取消'};
async function openDouyinDmResults(batchId) {
  dyDmShell('私信发送任务');
  const generation = dyDmGeneration, accountId = HUB_ACC;
  let loading = false;
  async function refresh() {
    if (!dyDmCurrent(generation) || loading) return;
    loading = true;
    try {
      const data = await api(batchId ? '/api/douyin/dm/batches/'+encodeURIComponent(batchId) : '/api/douyin/dm/batches?account_id='+accountId);
      if (!dyDmCurrent(generation)) return;
      const rows = batchId ? [data] : (Array.isArray(data) ? data : data.batches || []);
      $('dy-dm-body').innerHTML=rows.map(b=>`<section><h3>批次 ${esc(b.batch_id)}</h3><p>总数 ${b.total} · ${Object.entries(dyDmLabels).map(([k,v])=>v+' '+(b.counts[k]||0)).join(' · ')}</p><button data-dy-batch="${esc(b.batch_id)}" onclick="dyDmCancel(this.dataset.dyBatch)">取消尚未执行</button><ul>${(b.tasks||[]).map(t=>`<li>${esc(t.target_nick||t.target_uid)}：${dyDmLabels[t.status]||esc(t.status)} ${esc(t.error||t.blocked_reason||'')}</li>`).join('')}</ul></section>`).join('')||'暂无发送任务';
    } catch(e) { if (dyDmCurrent(generation)) dyDmError(e); }
    finally { loading = false; }
  }
  await refresh(); if (dyDmCurrent(generation)) dyDmTimer=setInterval(refresh,3000);
}
async function dyDmCancel(id) {
  const generation = dyDmGeneration;
  try { await api('/api/douyin/dm/batches/'+encodeURIComponent(id)+'/cancel',{method:'POST'}); if (dyDmCurrent(generation)) await openDouyinDmResults(id); }
  catch(e) { if (dyDmCurrent(generation)) dyDmError(e); }
}
