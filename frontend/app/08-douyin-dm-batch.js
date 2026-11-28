// Excel import, eligibility review and progress for single-account Douyin DM batches.
const DOUYIN_DM_BATCH_PATH = "/api/douyin/dm-batches";
const DOUYIN_DM_TASK_LABELS = {
  pending: "待发送", doing: "发送中", done: "已发送",
  failed: "未发送", uncertain: "待确认", canceled: "已取消",
};
const DOUYIN_DM_BATCH_LABELS = {
  running: "进行中", paused: "暂停待核对", completed: "已结束", canceled: "已取消",
};
let douyinDmBatchView = null;
let douyinDmBatchTimer = null;

function currentDouyinDmBatchView(view) {
  return view && douyinDmBatchView === view && PLATFORM === "douyin" && String(HUB_ACC) === view.accountId;
}

function refreshDouyinDmBatchPanel() {
  const panel = $("dm-batch-panel");
  const enabled = PLATFORM === "douyin" && Boolean(HUB_ACC);
  panel.hidden = !enabled;
  clearTimeout(douyinDmBatchTimer);
  if (!enabled || String(HUB_ACC) !== douyinDmBatchView?.accountId) {
    douyinDmBatchView = enabled ? { accountId: String(HUB_ACC), batches: [], revision: 0 } : null;
    $("dm-batch-file").value = "";
    $("dm-batch-content").value = "";
    $("dm-batch-recipient-list").replaceChildren();
    $("dm-batch-recipients").hidden = true;
    $("dm-batch-import-status").textContent = "";
    $("dm-batch-history-status").textContent = "";
    showDouyinDmBatchHistory();
  }
  const account = ACCOUNTS.find(item => String(item.id) === String(HUB_ACC));
  if (enabled) {
    douyinDmBatchView.accountLabel = account?.nickname ? `${account.nickname}（#${HUB_ACC}）` : `账号 #${HUB_ACC}`;
    $("dm-batch-account").textContent = `发送账号：${douyinDmBatchView.accountLabel}`;
  }
  updateDouyinDmBatchSelection();
  if (enabled && panel.open && HUB_TAB === "dm") refreshDouyinDmBatchHistory();
}

function selectedDouyinDmRecipients() {
  return [...$("dm-batch-recipient-list").querySelectorAll("input:checked")].map(item => ({
    sec_uid: item.value,
    nickname: item.dataset.nickname,
  }));
}

function selectAllReviewedDouyinDmRecipients() {
  if (!currentDouyinDmBatchView(douyinDmBatchView) || douyinDmBatchView.busy) return;
  for (const item of $("dm-batch-recipient-list").querySelectorAll('input[type="checkbox"]')) {
    item.checked = true;
  }
  updateDouyinDmBatchSelection();
}

function updateDouyinDmBatchSelection() {
  const view = douyinDmBatchView;
  const count = selectedDouyinDmRecipients().length;
  const activeBatch = view?.batches.some(batch => ["running", "paused"].includes(batch.status));
  $("dm-batch-file").disabled = !view || Boolean(view.busy);
  $("dm-batch-content").disabled = !view || Boolean(view.busy);
  $("dm-batch-recipients").disabled = Boolean(view?.busy);
  $("dm-batch-select-reviewed").disabled = !view || Boolean(view.busy) ||
    !$("dm-batch-recipient-list").querySelector('input[type="checkbox"]:not(:checked)');
  $("dm-batch-submit").disabled = !view || view.busy || activeBatch || !count || !$("dm-batch-content").value.trim();
  $("dm-batch-selection-status").textContent = activeBatch
    ? "请先处理当前批次，再创建新批次"
    : count ? `已确认 ${count} 位收件人` : "";
}

async function importDouyinDmBatch(input) {
  const view = douyinDmBatchView;
  const file = input.files[0];
  if (!currentDouyinDmBatchView(view) || view.busy || !file) return;
  view.busy = true;
  updateDouyinDmBatchSelection();
  const status = $("dm-batch-import-status");
  status.textContent = "正在读取 Excel…";
  try {
    const data = new FormData();
    data.append("file", file);
    const result = await api(DOUYIN_DM_BATCH_PATH + "/import", { method: "POST", body: data });
    if (!currentDouyinDmBatchView(view)) return;
    $("dm-batch-recipient-list").innerHTML = result.recipients.map(recipient => {
      const uid = esc(recipient.sec_uid);
      const nickname = esc(recipient.nickname || "");
      return `<label class="dm-batch-recipient"><input type="checkbox" value="${uid}" data-nickname="${nickname}">
        <span>${nickname ? `${nickname} <small>${uid}</small>` : uid}</span></label>`;
    }).join("");
    $("dm-batch-recipients").hidden = result.recipients.length === 0;
    status.textContent = `${result.recipients.length} 位可识别用户，合并 ${result.duplicates} 条重复记录，跳过 ${result.skipped} 条无效记录。`;
  } catch (error) {
    if (currentDouyinDmBatchView(view)) {
      $("dm-batch-recipient-list").replaceChildren();
      $("dm-batch-recipients").hidden = true;
      status.textContent = error.message;
    }
  } finally {
    if (currentDouyinDmBatchView(view)) {
      view.busy = false;
      input.value = "";
      updateDouyinDmBatchSelection();
    }
  }
}

async function submitDouyinDmBatch(event) {
  event.preventDefault();
  const view = douyinDmBatchView;
  if (!currentDouyinDmBatchView(view) || $("dm-batch-submit").disabled) return;
  const recipients = selectedDouyinDmRecipients();
  const content = $("dm-batch-content").value.trim();
  view.busy = true;
  updateDouyinDmBatchSelection();
  try {
    const approved = await uiConfirm({
      title: "确认批量发送",
      message: `账号「${view.accountLabel}」将向 ${recipients.length} 位已选收件人逐条发送。请确认他们已同意由此账号联系。`,
      detail: content,
      okText: "确认入队",
    });
    if (!approved || !currentDouyinDmBatchView(view)) return;
    const batch = await api(DOUYIN_DM_BATCH_PATH, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: Number(view.accountId), recipients, content, confirmed_eligible: true }),
    });
    if (!currentDouyinDmBatchView(view)) return;
    applyDouyinDmBatch(view, batch);
    $("dm-batch-recipient-list").replaceChildren();
    $("dm-batch-recipients").hidden = true;
    $("dm-batch-content").value = "";
    $("dm-batch-import-status").textContent = "";
    toast(`批次 #${batch.id} 已入队`, "ok");
  } catch (error) {
    if (currentDouyinDmBatchView(view)) toast(error.message, "err");
  } finally {
    if (currentDouyinDmBatchView(view)) {
      view.busy = false;
      updateDouyinDmBatchSelection();
      refreshDouyinDmBatchHistory();
    }
  }
}

function applyDouyinDmBatch(view, batch) {
  view.revision++;
  view.batches = [batch, ...view.batches.filter(item => item.id !== batch.id)];
  view.selected = String(batch.id);
  showDouyinDmBatchHistory();
}

async function refreshDouyinDmBatchHistory() {
  const view = douyinDmBatchView;
  const panel = $("dm-batch-panel");
  clearTimeout(douyinDmBatchTimer);
  if (!panel.open || panel.hidden || HUB_TAB !== "dm" || !panel.getClientRects().length
      || !currentDouyinDmBatchView(view) || view.loading) return;
  view.loading = true;
  const revision = view.revision;
  const status = $("dm-batch-history-status");
  if (!view.loaded) status.textContent = "正在读取批次…";
  try {
    const batches = await api(`${DOUYIN_DM_BATCH_PATH}?account_id=${encodeURIComponent(view.accountId)}`);
    if (!currentDouyinDmBatchView(view) || view.revision !== revision) return;
    view.batches = batches;
    view.loaded = true;
    status.textContent = batches.length ? "" : "暂无发送批次";
    showDouyinDmBatchHistory();
    updateDouyinDmBatchSelection();
  } catch (error) {
    if (currentDouyinDmBatchView(view) && view.revision === revision) status.textContent = error.message;
  } finally {
    view.loading = false;
    if (currentDouyinDmBatchView(view) && panel.open
        && (view.revision !== revision || view.batches.some(batch => ["running", "paused"].includes(batch.status)))) {
      douyinDmBatchTimer = setTimeout(refreshDouyinDmBatchHistory, 5000);
    }
  }
}

function showDouyinDmBatchHistory(selection = null) {
  const view = douyinDmBatchView;
  const history = $("dm-batch-history");
  if (view && selection !== null) view.selected = selection;
  const batches = view?.batches || [];
  const batch = batches.find(item => String(item.id) === view.selected) || batches[0];
  const options = batches.map(item => `<option value="${esc(item.id)}">批次 #${esc(item.id)} · ${Object.values(item.counts).reduce((sum, count) => sum + count, 0)} 人</option>`).join("");
  setHtmlIfChanged(history, options || '<option value="">暂无批次</option>');
  history.value = batch ? String(batch.id) : "";
  history.disabled = !batch;
  history._csSync?.();
  const progress = $("dm-batch-progress");
  if (!batch) { setHtmlIfChanged(progress, ""); return; }
  const countText = Object.entries(DOUYIN_DM_TASK_LABELS)
    .map(([status, label]) => `${label} ${batch.counts[status] || 0}`).join(" · ");
  const canCancel = batch.counts.pending > 0;
  const batchId = Number(batch.id);
  const tasks = batch.tasks.map(task => {
    const taskId = Number(task.id);
    const error = String(task.error || "").replace(/^write_uncertain:/, "");
    return `<div class="dm-batch-task">
      <div><b>${esc(task.target_nick || task.target_sec_uid)}</b> · ${esc(DOUYIN_DM_TASK_LABELS[task.status] || task.status)}</div>
      ${error ? `<small>${esc(error)}</small>` : ""}
      ${task.status === "uncertain" ? `<div class="dm-batch-actions">
        <button type="button" class="ghost sm" onclick="resolveDouyinDmBatchTask(${batchId},${taskId},true)">确认已发送</button>
        <button type="button" class="ghost sm" onclick="resolveDouyinDmBatchTask(${batchId},${taskId},false)">确认未发送</button>
      </div>` : ""}
    </div>`;
  }).join("");
  setHtmlIfChanged(progress, `<div class="dm-batch-actions"><b>${esc(DOUYIN_DM_BATCH_LABELS[batch.status] || batch.status)}</b>
    <span>${countText}</span>
    ${canCancel ? `<button type="button" class="ghost sm" onclick="cancelDouyinDmBatch(${batchId})">取消待发送</button>` : ""}
    </div>${batch.status === "paused" ? "<p>请在浏览器核对当前会话，再确认这条私信的结果。</p>" : ""}
    <div class="dm-batch-task-list">${tasks}</div>`);
}

async function updateDouyinDmBatch(view, path, options, confirmation) {
  if (!currentDouyinDmBatchView(view) || view.busy) return;
  view.busy = true;
  updateDouyinDmBatchSelection();
  try {
    if (!(await uiConfirm(confirmation)) || !currentDouyinDmBatchView(view)) return;
    const batch = await api(DOUYIN_DM_BATCH_PATH + path, options);
    if (currentDouyinDmBatchView(view)) applyDouyinDmBatch(view, batch);
  } catch (error) {
    if (currentDouyinDmBatchView(view)) toast(error.message, "err");
  } finally {
    if (currentDouyinDmBatchView(view)) {
      view.busy = false;
      updateDouyinDmBatchSelection();
      refreshDouyinDmBatchHistory();
    }
  }
}

function cancelDouyinDmBatch(batchId) {
  return updateDouyinDmBatch(douyinDmBatchView, `/${batchId}/cancel`, { method: "POST" }, {
    title: "取消未发送任务", message: "取消这个批次中尚未开始发送的任务。", okText: "确认取消", danger: true,
  });
}

function resolveDouyinDmBatchTask(batchId, taskId, confirmedSent) {
  return updateDouyinDmBatch(douyinDmBatchView, `/${batchId}/resolve`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId, confirmed_sent: confirmedSent }),
  }, {
    title: confirmedSent ? "确认这条私信已发送" : "确认这条私信未发送",
    message: "请先在抖音会话中核对结果。确认后，剩余任务按原队列继续执行。",
    okText: "保存核对结果",
  });
}
