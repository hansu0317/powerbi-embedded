// csrfToken 은 템플릿에서 인라인으로 선언됨
// const csrfToken = ...;  ← report.html 참고

const pbi = new window['powerbi-client'].service.Service(
  window['powerbi-client'].factories.hpmFactory,
  window['powerbi-client'].factories.wpmpFactory,
  window['powerbi-client'].factories.routerFactory
);

// reportId -> { tabEl, panelEl }
const openTabs = {};
let activeTab = null;

// ── 사이드바 폴더 그룹 접기/펴기 ──────────────────────────────────────────────
function toggleGroup(id) {
  document.getElementById(id).classList.toggle('collapsed');
  _saveGroupStates();
}

function _saveGroupStates() {
  const states = {};
  document.querySelectorAll('.sidebar-group').forEach(g => {
    if (g.id) states[g.id] = g.classList.contains('collapsed');
  });
  sessionStorage.setItem('sb-groups', JSON.stringify(states));
}

function _restoreGroupStates() {
  try {
    const saved = JSON.parse(sessionStorage.getItem('sb-groups') || '{}');
    Object.entries(saved).forEach(([id, collapsed]) => {
      const el = document.getElementById(id);
      if (el) el.classList.toggle('collapsed', collapsed);
    });
  } catch (_) {}
}

// ── 탭 상태 세션 저장/복원 ───────────────────────────────────────────────────
function _saveTabState() {
  const tabs = Object.entries(openTabs).map(([id, { tabEl }]) => ({
    id: Number(id),
    name: tabEl.querySelector('.tab-label').textContent,
  }));
  sessionStorage.setItem('open-tabs', JSON.stringify(tabs));
  sessionStorage.setItem('active-tab', String(activeTab ?? ''));
}

function _restoreTabState() {
  try {
    const tabs = JSON.parse(sessionStorage.getItem('open-tabs') || '[]');
    const savedActive = Number(sessionStorage.getItem('active-tab') || '0');
    for (const { id, name } of tabs) {
      createTab(id, name);
      fetchAndEmbed(id, name);
    }
    if (savedActive && openTabs[savedActive]) switchTab(savedActive);
  } catch (_) {}
}

// ── 탭 동작 ──────────────────────────────────────────────────────────────────
function openReport(reportId, name) {
  if (openTabs[reportId]) {
    switchTab(reportId);
    _saveTabState();
    return;
  }
  createTab(reportId, name);
  fetchAndEmbed(reportId, name);
  _saveTabState();
}

function createTab(reportId, name) {
  const tab = document.createElement('div');
  tab.className = 'tab';
  tab.dataset.reportId = reportId;

  const tabIcon = document.createElement('span');
  tabIcon.textContent = '📊';
  const tabLabel = document.createElement('span');
  tabLabel.className = 'tab-label';
  tabLabel.textContent = name;
  const closeButton = document.createElement('button');
  closeButton.className = 'close-btn';
  closeButton.title = '닫기';
  closeButton.textContent = '×';

  tab.append(tabIcon, tabLabel, closeButton);
  tabLabel.onclick  = () => switchTab(reportId);
  closeButton.onclick = (e) => { e.stopPropagation(); closeTab(reportId); };
  tab.onclick = () => switchTab(reportId);
  document.getElementById('tabs-bar').appendChild(tab);

  const panel = document.createElement('div');
  panel.className = 'tab-panel';
  panel.id = `panel-${reportId}`;
  panel.innerHTML = `
    <div class="panel-loading" id="loading-${reportId}">보고서 불러오는 중...</div>
    <div class="report-embed" id="embed-${reportId}"></div>
  `;
  document.getElementById('tab-panels').appendChild(panel);

  openTabs[reportId] = { tabEl: tab, panelEl: panel };

  document.getElementById('no-tabs-hint').style.display = 'none';
  document.getElementById('empty-state').style.display  = 'none';

  switchTab(reportId);
}

function switchTab(reportId) {
  if (!openTabs[reportId]) return;

  if (activeTab && openTabs[activeTab]) {
    openTabs[activeTab].tabEl.classList.remove('active');
    openTabs[activeTab].panelEl.classList.remove('active');
  }

  openTabs[reportId].tabEl.classList.add('active');
  openTabs[reportId].panelEl.classList.add('active');
  activeTab = reportId;
  updateSidebarActive(reportId);
  sessionStorage.setItem('active-tab', String(reportId));
  openTabs[reportId].tabEl.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function closeTab(reportId) {
  if (!openTabs[reportId]) return;

  const embedEl = document.getElementById(`embed-${reportId}`);
  if (embedEl) pbi.reset(embedEl);

  openTabs[reportId].tabEl.remove();
  openTabs[reportId].panelEl.remove();
  delete openTabs[reportId];

  const remaining = Object.keys(openTabs);
  if (remaining.length > 0) {
    switchTab(remaining[remaining.length - 1]);
  } else {
    activeTab = null;
    document.getElementById('no-tabs-hint').style.display = '';
    document.getElementById('empty-state').style.display  = '';
    updateSidebarActive(null);
  }
  _saveTabState();
}

// ── PowerBI embed ─────────────────────────────────────────────────────────────
async function fetchAndEmbed(reportId, name) {
  try {
    const res = await fetch(`/api/embed/${reportId}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = err.detail;
      const msg = (typeof detail === 'object' ? detail?.message : detail) || '알 수 없는 오류';
      throw new Error(msg);
    }
    const data = await res.json();
    embedReport(reportId, data);
  } catch (e) {
    const panel = openTabs[reportId]?.panelEl;
    if (panel) {
      panel.innerHTML = `<div class="panel-error">보고서 로드 실패: ${e.message}</div>`;
    }
  }
}

function embedReport(reportId, data) {
  const container = document.getElementById(`embed-${reportId}`);
  const loading   = document.getElementById(`loading-${reportId}`);
  if (!container) return;

  const s = data.settings || {};
  const config = {
    type: 'report',
    id: data.report_id,
    embedUrl: data.embed_url,
    accessToken: data.embed_token,
    tokenType: window['powerbi-client'].models.TokenType.Embed,
    settings: {
      navContentPaneEnabled: Boolean(s.enable_page_nav),
      filterPaneEnabled:     Boolean(s.enable_filter),
      panes: {
        pageNavigation: { visible: Boolean(s.enable_page_nav) },
        filters:        { visible: Boolean(s.enable_filter), expanded: false },
      },
    },
  };
  if (s.default_page) config.pageName = s.default_page;

  const report = pbi.embed(container, config);
  report.on('loaded', () => { if (loading) loading.style.display = 'none'; });
  report.on('error', (e) => {
    if (loading) loading.style.display = 'none';
    if (container) container.innerHTML = `<div class="panel-error">오류: ${JSON.stringify(e.detail)}</div>`;
  });
}

function updateSidebarActive(activeReportId) {
  document.querySelectorAll('.sidebar-item').forEach(el => {
    el.classList.toggle('active', Number(el.dataset.reportId) === Number(activeReportId));
  });
}

// ── .pbix 업로드 ──────────────────────────────────────────────────────────────
document.getElementById('pbix-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = '';

  const btn    = document.getElementById('upload-btn');
  const status = document.getElementById('upload-status');
  btn.disabled = true;
  status.className   = 'upload-status';
  status.textContent = `'${file.name}' 전송 중...`;

  try {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/upload', {
      method: 'POST',
      body: fd,
      headers: { 'X-CSRF-Token': csrfToken },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      throw new Error((typeof detail === 'object' ? detail?.message : detail) || res.statusText);
    }

    const jobId      = data.job_id;
    const reportName = data.report_name;
    status.textContent = `'${reportName}' PBI 게시 중... (보통 30초~2분)`;

    const DONE = new Set(['completed', 'failed', 'conflict', 'unknown']);
    while (true) {
      await new Promise(r => setTimeout(r, 3000));
      const sr    = await fetch(`/api/upload/status/${jobId}`, { headers: { 'X-CSRF-Token': csrfToken } });
      const sdata = await sr.json().catch(() => ({}));
      if (!sr.ok) throw new Error(sdata.detail?.message || sdata.detail || '상태 조회 실패');

      if (sdata.status === 'completed') {
        status.className   = 'upload-status ok';
        status.textContent = `'${reportName}' 게시 완료! 목록을 새로고침합니다...`;
        sessionStorage.removeItem('open-tabs');
        sessionStorage.removeItem('active-tab');
        setTimeout(() => location.reload(), 1500);
        return;
      }
      if (DONE.has(sdata.status)) {
        throw new Error(sdata.error || `게시 실패 (${sdata.status})`);
      }
      const labels = { accepted: 'PBI 접수됨', publishing: 'PBI 변환 중', pbi_succeeded: 'DB 등록 중' };
      status.textContent = `'${reportName}' ${labels[sdata.status] || sdata.status}... (보통 30초~2분)`;
    }
  } catch (err) {
    status.className   = 'upload-status err';
    status.textContent = `업로드 실패: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

// 페이지 로드 시 상태 복원
_restoreGroupStates();
_restoreTabState();
