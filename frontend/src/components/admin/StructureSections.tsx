import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import { fetchReportFolders, type ReportFolder } from "../../lib/api";
import { orderFoldersAsTree } from "../../lib/folderTree";

type ToastFn = (message: string, tone?: "ok" | "err" | "") => void;
type Props = { csrf: string; showToast: ToastFn };
const COLLAPSED_KEY = "ad-folder-tree-collapsed";

// 읽기 전용 조회 화면이다(2026-08-12~) — 폴더를 새로 만들거나 이름을 바꾸거나
// 지우는 기능은 이 앱에 두지 않는다. 포털이 Fabric과 별개로 자기만의 폴더 구조를
// 스스로 만들고 관리하는 부담을 지지 않기로 했다 — 구조 자체는 Power BI/Fabric에서
// 관리하고, 여기서는 report_folders에 이미 있는 걸 그대로 보여주기만 한다.
export function ReportFoldersSection({ showToast }: Props) {
  const [folders, setFolders] = useState<ReportFolder[]>([]);
  const [collapsed, setCollapsed] = useState<Record<number, boolean>>(loadCollapsed);
  const load = useCallback(() => fetchReportFolders().then(setFolders).catch((error) => showToast((error as Error).message, "err")), [showToast]);
  useEffect(() => { load(); }, [load]);

  // 하위 폴더가 있는 id 집합 — 화살표는 이 집합에 있는 행에만 그린다.
  const parentIds = useMemo(() => new Set(folders.map((f) => f.parent_id).filter((id): id is number => id != null)), [folders]);

  // report_count는 그 폴더에 "직접" 배정된 보고서만 센다(DB 쿼리 기준) — 상위 폴더
  // 자체엔 보고서가 없고 하위 폴더에만 있는 경우(예: "공장" 밑에 "생산"/"품질") 상위가
  // 0으로 보여서 실제보다 적어 보인다. 화면에서는 하위까지 합산한 값을 보여준다.
  const rollupCounts = useMemo(() => {
    const childrenOf = new Map<number, ReportFolder[]>();
    for (const f of folders) {
      if (f.parent_id == null) continue;
      const list = childrenOf.get(f.parent_id) ?? [];
      list.push(f);
      childrenOf.set(f.parent_id, list);
    }
    const totals = new Map<number, number>();
    const compute = (f: ReportFolder): number => {
      const cached = totals.get(f.id);
      if (cached != null) return cached;
      const childTotal = (childrenOf.get(f.id) ?? []).reduce((sum, c) => sum + compute(c), 0);
      const total = f.report_count + childTotal;
      totals.set(f.id, total);
      return total;
    };
    folders.forEach(compute);
    return totals;
  }, [folders]);
  const toggle = (id: number) => setCollapsed((previous) => {
    const next = { ...previous, [id]: !(previous[id] ?? true) };
    sessionStorage.setItem(COLLAPSED_KEY, JSON.stringify(next));
    return next;
  });

  // orderFoldersAsTree는 깊이우선 평탄 목록을 준다 — 접힌 조상 밑에서 나온 행은
  // depth로 걸러낸다(같은 depth 이하로 돌아오면 그 조상 구간을 벗어난 것).
  const rows: { folder: ReportFolder; depth: number; hasChildren: boolean }[] = [];
  let hideBelowDepth: number | null = null;
  for (const { folder, depth } of orderFoldersAsTree(folders)) {
    if (hideBelowDepth != null) {
      if (depth > hideBelowDepth) continue;
      hideBelowDepth = null;
    }
    const hasChildren = parentIds.has(folder.id);
    rows.push({ folder, depth, hasChildren });
    if (hasChildren && (collapsed[folder.id] ?? true)) hideBelowDepth = depth;
  }

  return <section>
    <div className="ad-section-head">
      <div><h2>보고서 폴더</h2><span className="muted">보고서의 표시 위치와 공유 범위를 확인합니다(조회 전용). 폴더 구조 자체는 Power BI/Fabric에서 관리합니다 — Fabric에 새 폴더/하위 폴더를 만들었는데 여기 안 보이면, 이 화면에서는 추가할 수 없으니 개발 담당자에게 DB 반영을 요청하세요.</span></div>
    </div>
    <div className="ad-folder-tree">
      <div className="ad-folder-node ad-folder-node--head">
        <span className="ad-folder-node-name">폴더</span>
        <span className="ad-folder-node-scope">범위</span>
        <span className="ad-folder-node-owner">소유자</span>
        <span className="ad-folder-node-count">보고서</span>
      </div>
      {rows.map(({ folder, depth, hasChildren }) => (
        <div
          key={folder.id}
          className={`ad-folder-node${hasChildren ? " ad-folder-node--parent" : ""}`}
          style={{ paddingLeft: 14 + depth * 20 }}
          onClick={hasChildren ? () => toggle(folder.id) : undefined}
        >
          <span className="ad-folder-node-name">
            {hasChildren
              ? <ChevronDown size={13} className={`ad-folder-arrow${(collapsed[folder.id] ?? true) ? " collapsed" : ""}`} />
              : <span className="ad-folder-arrow-spacer" />}
            {folder.name}
          </span>
          <span className="ad-folder-node-scope">{visibilityLabel(folder.visibility)}</span>
          <span className="ad-folder-node-owner">{folder.owner_username || "포털"}</span>
          <span className="ad-folder-node-count">{rollupCounts.get(folder.id) ?? folder.report_count}</span>
        </div>
      ))}
      {folders.length === 0 && <div className="ad-folder-node-empty">폴더가 없습니다.</div>}
    </div>
  </section>;
}

function loadCollapsed(): Record<number, boolean> {
  try { return JSON.parse(sessionStorage.getItem(COLLAPSED_KEY) || "{}"); } catch { return {}; }
}

function visibilityLabel(value: ReportFolder["visibility"]) { return value === "shared" ? "공용" : "개인"; }
