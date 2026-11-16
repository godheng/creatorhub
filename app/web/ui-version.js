// Keep the current draft intact when the server is upgraded.
(() => {
  const version=document.querySelector('meta[name="creatorhub-ui-version"]')?.content;
  if(!version) return;
  let stale=false;
  function showUpdate() {
    stale=true;
    const host=document.querySelector('dialog[open]')||document.body;
    const existing=document.getElementById('client-update-notice');
    if(existing) {if(existing.parentNode!==host)host.appendChild(existing);return;}
    const notice=document.createElement('div'); notice.id='client-update-notice'; notice.setAttribute('role','alert');
    notice.style.cssText='position:fixed;top:8px;left:10%;right:10%;z-index:2147483647;background:#fff1f3;color:#8d1532;padding:12px;border:1px solid #d91c45;border-radius:8px';
    notice.textContent='应用已更新。当前草稿仍保留；请先保存需要的内容，再刷新页面。刷新前不能提交操作。';
    host.appendChild(notice);
  }
  async function check() {
    const response=await fetch('/api/ui-version',{cache:'no-store'});
    if(!response.ok) throw new Error('无法核对应用版本，请连接恢复后再提交');
    const data=await response.json(); if(data.version!==version)showUpdate();
    return !stale;
  }
  window.CreatorHubUiVersion={check,async send(path,options={}) {
    const mutation=!['GET','HEAD','OPTIONS'].includes((options.method||'GET').toUpperCase());
    if(mutation && (stale || !await check())) throw new Error('应用已更新，请保存草稿并刷新页面后再提交');
    const headers=new Headers(options.headers||{});headers.set('X-CreatorHub-UI-Version',version);
    const response=await fetch(path,{...options,headers,credentials:'same-origin'});
    if(response.status===409 && response.headers.get('X-CreatorHub-UI-Stale')==='1')showUpdate();
    return response;
  }};
  setInterval(()=>check().catch(()=>{}),30000);
  window.addEventListener('focus',()=>check().catch(()=>{}));
})();
