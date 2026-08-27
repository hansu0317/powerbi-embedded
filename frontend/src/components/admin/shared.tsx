// 관리자 포털 여러 섹션이 공유하는 작은 조각들.
// pages/AdminPage.tsx에서 2026-08-27에 분리했다(그 파일이 1682줄까지 커져서 섹션별로
// 나눴다 — routes/admin.py를 나눌 때와 같은 이유).
import { useState } from "react";
import { AlertTriangle, Bell, X } from "lucide-react";

import type { AdminUser } from "../../lib/bootstrap";
import type { SyncStatus } from "../../lib/api";

/** Power BI 동기화 필요 여부 알림 벨 — 관리자 포털과 홈/보고서 화면(ReportPage.tsx, admin
 * 로그인일 때만) 양쪽에서 쓴다(2026-08-12). sync는 호출부가 (이미 갖고 있거나 직접 조회한)
 * adminSyncStatus() 결과를 그대로 넘긴다 — 여기서 다시 fetch하지 않는 이유는 AdminPage가
 * 이미 같은 값을 배너에도 쓰고 있어서, 여기서도 fetch하면 같은 화면에서 같은 API를 두 번
 * 부르게 되기 때문. onGoReports는 "보고서 확인" 클릭 시 동작 — AdminPage에서는 같은 화면
 * 안에서 섹션만 전환, ReportPage에서는 관리자 포털로 이동. */
export function AdminSyncBell({ sync, onGoReports }: { sync: SyncStatus | null; onGoReports: () => void }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="ad-icon-trigger" aria-label="알림" onClick={() => setOpen(true)}>
      <Bell size={17} />
      {sync?.drift && <span className="ad-notice-dot" />}
    </button>
    {open && <div className="ad-settings-overlay" onClick={() => setOpen(false)}>
      <aside className="ad-settings-panel ad-notifications-panel" onClick={(e) => e.stopPropagation()}>
        <div className="ad-settings-head"><h2>알림</h2><button onClick={() => setOpen(false)}><X size={18} /></button></div>
        {sync?.drift ? <div className="ad-notification-item">
          <AlertTriangle size={18} className="ad-notification-warn" />
          <div><strong>Power BI 동기화가 필요합니다</strong><p>새 보고서나 폴더 변경 사항이 있습니다. 보고서 메뉴에서 가져오기를 실행하세요.</p><button className="btn btn-sm btn-primary" onClick={() => { setOpen(false); onGoReports(); }}>보고서 확인</button></div>
        </div> : <div className="ad-notifications-empty"><Bell size={24} /><p>새 알림이 없습니다.</p></div>}
      </aside>
    </div>}
  </>;
}

/** 이미 쓰이고 있는 department 값 목록(오탈자 방지용 자동완성 제안일 뿐, 강제 아님 — docs/01 참고). */
export function departmentOptions(users: AdminUser[]): string[] {
  return Array.from(new Set(users.map((u) => u.department).filter((d): d is string => !!d))).sort();
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="ad-field">
      <label>{label}</label>
      {children}
    </div>
  );
}

/** 모달 공통 뼈대: 오버레이(바깥 클릭 닫기) + 헤더(제목·× 버튼). 본문·푸터는 children으로 받는다. */
export function Modal({
  title,
  wide,
  onClose,
  children,
}: {
  title: React.ReactNode;
  wide?: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="ad-modal-overlay" onClick={onClose}>
      <div
        className={`ad-modal${wide ? " ad-modal-wide" : ""}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ad-modal-header">
          <h3>{title}</h3>
          <button className="ad-modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
