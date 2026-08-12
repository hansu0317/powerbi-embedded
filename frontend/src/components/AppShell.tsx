import type { ReactNode } from "react";

export interface RailItem {
  key: string;
  icon: ReactNode;
  label: string;
  active?: boolean;
  onClick?: () => void;
  href?: string;
}

/** 아이콘 전용 좌측 레일 — 화면 전체 공통 최상위 내비게이션(홈/보고서/관리자).
 * 다크로 고정해 방향감만 주는 얇은 기준선 역할만 한다 — 넓은 면적을 다크로
 * 덮지 않기 때문에 매일 오래 봐도 눈이 편하다. 예전의 상단바+사이드바 이중
 * 구성을 이 레일 하나로 통합한다. */
export function Rail({
  logo,
  items,
  footer,
}: {
  logo?: ReactNode;
  items: RailItem[];
  footer?: ReactNode;
}) {
  return (
    <nav className="as-rail">
      {logo && <div className="as-rail-logo">{logo}</div>}
      {items.map((it) =>
        it.href ? (
          <a
            key={it.key}
            href={it.href}
            className={`as-rail-item${it.active ? " active" : ""}`}
            title={it.label}
          >
            {it.icon}
          </a>
        ) : (
          <button
            key={it.key}
            type="button"
            className={`as-rail-item${it.active ? " active" : ""}`}
            title={it.label}
            onClick={it.onClick}
          >
            {it.icon}
          </button>
        ),
      )}
      <div className="as-rail-spacer" />
      {footer}
    </nav>
  );
}

/** 화면별 컨텍스트 바 — 예전 상단바를 대신한다. 화면마다 다른 breadcrumb과
 * 그 화면에서만 쓰는 동작(검색·즐겨찾기·로그아웃 등)을 오른쪽에 둔다. */
export function ContextBar({
  crumb,
  right,
}: {
  crumb: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="as-ctxbar">
      <div className="as-ctxbar-crumb">{crumb}</div>
      <div className="as-ctxbar-spacer" />
      {right}
    </div>
  );
}
