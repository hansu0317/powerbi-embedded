// csrfToken 은 템플릿에서 인라인으로 선언됨
// const csrfToken = ...;  ← admin.html 참고

function showSection(name, el) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.sidebar-item').forEach(s => s.classList.remove('active'));
  document.getElementById('section-' + name).classList.add('active');
  el.classList.add('active');
}

function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(() => { t.classList.remove('show'); }, 3000);
}

// ── PBI에서 공용 보고서 가져오기 ─────────────────────────────────────────────
async function importFromPbi() {
  const btn    = document.getElementById('import-pbi-btn');
  const result = document.getElementById('import-result');
  btn.disabled = true;
  btn.textContent = '가져오는 중...';
  result.textContent = '';
  try {
    const res = await fetch('/api/admin/import-pbi', {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.detail || res.statusText);
    result.textContent = `신규 ${j.registered}개 등록 / 건너뜀 ${j.skipped}개`;
    result.style.color = j.registered > 0 ? '#0a7c42' : '#888';
    showToast(
      j.registered > 0
        ? `${j.registered}개 보고서가 새로 등록됐습니다. 권한 설정 후 사용자에게 노출됩니다.`
        : '새로 등록된 보고서가 없습니다.',
      j.registered > 0 ? 'ok' : ''
    );
    if (j.registered > 0) setTimeout(() => location.reload(), 2000);
  } catch (e) {
    result.textContent = '오류: ' + e.message;
    result.style.color = '#b91c1c';
    showToast('가져오기 실패: ' + e.message, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = '⬇ PBI에서 가져오기';
  }
}

async function submitAddUser(e) {
  e.preventDefault();
  const form = e.target;
  const data = new FormData(form);
  const res = await fetch('/api/admin/users/add', { method: 'POST', body: data });
  if (res.ok) {
    showToast('사용자가 추가되었습니다. 페이지를 새로고침해 확인하세요.', 'ok');
    form.reset();
    form.querySelector('[name=roles]').value = '도메인';
  } else {
    const j = await res.json().catch(() => ({}));
    showToast('오류: ' + (j.detail || res.statusText), 'err');
  }
}

async function toggleUser(userId) {
  const res = await fetch('/api/admin/users/' + userId + '/toggle-active', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken },
  });
  if (res.ok) {
    const j = await res.json();
    const pill = document.getElementById('user-status-' + userId);
    pill.textContent = j.is_active ? '활성' : '비활성';
    pill.className = 'pill ' + (j.is_active ? 'active' : 'inactive');
    showToast(j.is_active ? '계정이 활성화됐습니다.' : '계정이 비활성화됐습니다.', 'ok');
  } else {
    const j = await res.json().catch(() => ({}));
    showToast('오류: ' + (j.detail || res.statusText), 'err');
  }
}

async function deleteReport(reportId, name) {
  if (!confirm('"' + name + '" 보고서를 삭제하시겠습니까?\nPower BI 워크스페이스에서도 완전히 삭제됩니다.')) return;
  const res = await fetch('/api/admin/reports/' + reportId + '/delete', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken },
  });
  const j = await res.json().catch(() => ({}));
  if (res.ok) {
    const row = document.getElementById('report-row-' + reportId);
    row.cells[5].innerHTML = '<span class="pill inactive">삭제됨</span>';
    row.cells[7].innerHTML = '<button class="btn btn-primary btn-sm" data-rid="' + reportId + '" data-rname="' + name.replace(/&/g,'&amp;').replace(/"/g,'&quot;') + '" onclick="openAccessModal(+this.dataset.rid, this.dataset.rname)">권한</button>';
    const msg = j.pbi_warning
      ? '"' + name + '" DB 삭제 완료. PBI 경고: ' + j.pbi_warning
      : '"' + name + '" 보고서가 PBI와 DB에서 삭제됐습니다.';
    showToast(msg, j.pbi_warning ? 'err' : 'ok');
  } else {
    showToast('오류: ' + ((j.detail && j.detail.message) || res.statusText), 'err');
  }
}

// ── 권한 관리 모달 ────────────────────────────────────────────────────────────
let _accessReportId = null;

async function openAccessModal(reportId, name) {
  _accessReportId = reportId;
  document.getElementById('access-modal-title').textContent = '열람 권한 — ' + name;
  document.getElementById('access-modal-body').innerHTML =
    '<div style="text-align:center;padding:24px;color:#aaa">불러오는 중...</div>';
  document.getElementById('access-modal').style.display = 'flex';
  await _loadAccessModal(reportId);
}

async function _loadAccessModal(reportId) {
  const res = await fetch('/api/admin/reports/' + reportId + '/access');
  if (!res.ok) {
    document.getElementById('access-modal-body').innerHTML =
      '<div style="color:#b91c1c;padding:12px">권한 목록 조회 실패</div>';
    return;
  }
  const { users } = await res.json();
  _renderAccessModal(users);
}

function _renderAccessModal(users) {
  if (!users.length) {
    document.getElementById('access-modal-body').innerHTML =
      '<div style="color:#aaa;padding:12px">사용자가 없습니다.</div>';
    return;
  }
  document.getElementById('access-modal-body').innerHTML = users.map(u => `
    <div class="access-row">
      <div class="access-user-info">
        <span class="access-user-name">${u.display_name}</span>
        <span class="access-user-id">${u.username}</span>
        ${u.is_admin ? '<span class="pill admin">관리자</span>' : ''}
      </div>
      <button class="btn btn-sm ${u.can_view ? 'btn-danger' : 'btn-primary'}"
              onclick="setAccess(${_accessReportId}, ${u.id}, ${!u.can_view})">
        ${u.can_view ? '해제' : '부여'}
      </button>
    </div>
  `).join('');
}

async function setAccess(reportId, userId, canView) {
  const res = await fetch('/api/admin/reports/' + reportId + '/access/' + userId, {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken, 'Content-Type': 'application/json' },
    body: JSON.stringify({ can_view: canView }),
  });
  if (res.ok) {
    showToast(canView ? '열람 권한이 부여됐습니다.' : '열람 권한이 해제됐습니다.', 'ok');
    await _loadAccessModal(reportId);
  } else {
    showToast('오류가 발생했습니다.', 'err');
  }
}

function closeAccessModal(event) {
  if (!event || event.target === document.getElementById('access-modal')) {
    document.getElementById('access-modal').style.display = 'none';
    _accessReportId = null;
  }
}

// ── 데이터셋 새로고침 ──────────────────────────────────────────────────────────
async function refreshDataset(reportId, reportName) {
  if (!confirm(`'${reportName}' 데이터셋을 새로고침 하시겠습니까?`)) return;
  const res = await fetch(`/api/admin/reports/${reportId}/refresh`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken },
  });
  if (res.ok) {
    showToast(`'${reportName}' 새로고침 요청 완료 (PBI가 백그라운드에서 처리합니다)`, 'ok');
  } else {
    const j = await res.json().catch(() => ({}));
    showToast(`새로고침 실패: ${j.detail?.message || j.detail || '알 수 없는 오류'}`, 'err');
  }
}

// ── 열람 통계 로드 ─────────────────────────────────────────────────────────────
let _statsLoaded = false;
async function loadViewStats() {
  if (_statsLoaded) return;
  const el = document.getElementById('stats-content');
  try {
    const res = await fetch('/api/admin/stats/views');
    const d = await res.json();

    const topReports = d.by_report.map(r =>
      `<tr><td>${r.category || '-'}</td><td>${r.name}</td><td style="text-align:right"><b>${r.view_count}</b></td></tr>`
    ).join('');

    const topUsers = d.by_user.map(u =>
      `<tr><td>${u.username}</td><td style="text-align:right"><b>${u.view_count}</b></td></tr>`
    ).join('');

    const maxCnt = Math.max(...d.by_day.map(x => x.view_count), 1);

    el.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
        <div class="table-wrap"><h3 style="padding:12px 16px;margin:0;font-size:0.95rem">보고서별 조회 수 (상위 20)</h3>
          <table><thead><tr><th>폴더</th><th>보고서</th><th style="text-align:right">조회 수</th></tr></thead>
          <tbody>${topReports || '<tr><td colspan="3" style="text-align:center;color:#aaa">데이터 없음</td></tr>'}</tbody></table></div>
        <div class="table-wrap"><h3 style="padding:12px 16px;margin:0;font-size:0.95rem">사용자별 조회 수 (상위 10)</h3>
          <table><thead><tr><th>사용자</th><th style="text-align:right">조회 수</th></tr></thead>
          <tbody>${topUsers || '<tr><td colspan="2" style="text-align:center;color:#aaa">데이터 없음</td></tr>'}</tbody></table></div>
      </div>
      <div class="table-wrap"><h3 style="padding:12px 16px;margin:0;font-size:0.95rem">최근 7일 일별 조회 수</h3>
        <div style="padding:16px;display:flex;align-items:flex-end;gap:8px;height:120px">
          ${d.by_day.map(x => {
            const h = Math.round((x.view_count / maxCnt) * 90);
            return `<div style="display:flex;flex-direction:column;align-items:center;flex:1;gap:4px">
              <span style="font-size:0.72rem;font-weight:600">${x.view_count}</span>
              <div style="width:100%;height:${h}px;background:#0078d4;border-radius:3px 3px 0 0;min-height:4px"></div>
              <span style="font-size:0.68rem;color:#666">${x.day.slice(5)}</span></div>`;
          }).join('') || '<span style="color:#aaa">데이터 없음</span>'}
        </div>
      </div>`;
    _statsLoaded = true;
  } catch (e) {
    el.innerHTML = `<div style="color:#b91c1c">통계 로드 실패: ${e.message}</div>`;
  }
}
