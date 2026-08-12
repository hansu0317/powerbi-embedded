import { useMemo, useState } from "react";
import { ChevronDown, Folder, LayoutList, Star, Upload } from "lucide-react";
import type { ReportItem } from "../lib/bootstrap";
import type { ReportFolder } from "../lib/api";

export type ReportView = "my" | "all" | "upload";
export type BrowserMode = "tree" | "folders";
type FolderNode = { id: number; name: string; path: string; children: FolderNode[]; reports: ReportItem[] };
type Props = {
  reports: ReportItem[]; myReports: ReportItem[]; folders: ReportFolder[]; view: ReportView;
  activeId: number | null; isAdmin: boolean; canUpload: boolean;
  browserMode: BrowserMode; isFav: (id:number)=>boolean; onSelectView:(view:ReportView)=>void;
  onSelectFolder:(path:string|null)=>void; onOpen:(report:ReportItem)=>void;
};
const STORAGE_KEY = "sb-groups-v2";
const FAVORITES_KEY = "__favorites";

export function ReportSidebar({ reports, myReports, folders, view, activeId, isAdmin, canUpload, isFav, onSelectView, onSelectFolder, onOpen }: Props) {
  const favoriteReports = reports.filter((report) => isFav(report.id));
  const { tree, uncategorized } = useMemo(() => buildFolderTree(myReports, folders), [myReports, folders]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadCollapsed);
  function toggle(path:string) {
    setCollapsed((previous) => {
      const currentlyCollapsed = previous[path] ?? true;
      const next = {...previous, [path]:!currentlyCollapsed};
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }
  return <nav className="app-sidebar rp-sidepanel">
    <div className="app-sidebar-title">보고서</div>
    <div className="app-sidebar-scroll">
      <NavItem active={view === "my"} onClick={() => onSelectView("my")}><Folder size={17}/> 열람 보고서</NavItem>
      {view === "my" && <div className="rp-tree">
        {favoriteReports.length > 0 && <div className={`rp-group${(collapsed[FAVORITES_KEY] ?? true) ? " collapsed" : ""}`}><div className="rp-group-header rp-group-fav" onClick={() => toggle(FAVORITES_KEY)}><ChevronDown size={13} className="rp-group-arrow"/><Star size={12} fill="currentColor"/> 즐겨찾기</div><div className="rp-group-body">{favoriteReports.map((report) => <TreeItem key={`fav-${report.id}`} report={report} active={report.id === activeId} onOpen={onOpen} indent/>)}</div></div>}
        {tree.map((node) => <TreeGroup key={node.path} node={node} depth={0} collapsed={collapsed} onToggle={toggle} onSelectFolder={onSelectFolder} activeId={activeId} onOpen={onOpen}/>) }
        {uncategorized.map((report) => <TreeItem key={report.id} report={report} active={report.id === activeId} onOpen={onOpen}/>) }
        {myReports.length === 0 && <div className="rp-tree-empty">열람 가능한 보고서가 없습니다</div>}
      </div>}
      {isAdmin && <NavItem active={view === "all"} onClick={() => onSelectView("all")}><LayoutList size={17}/> 전체 보고서</NavItem>}
      {canUpload && <NavItem active={view === "upload"} onClick={() => onSelectView("upload")}><Upload size={17}/> 보고서 등록</NavItem>}
    </div>
  </nav>;
}

// report_folders의 실제 parent_id 계층으로 트리를 만든다(예전엔 report.category를 "/"로
// 쪼개 계층을 흉내냈는데, 폴더 이름 자체에 구분자를 넣어야 했고 관리자 화면의 진짜
// parent_id 트리와 서로 다른 걸 보여주는 문제가 있었다 — 2026-08-12, docs/02 참고).
// 보고서는 report.portal_folder_id로 자기 폴더를 찾고, 없거나 안 보이는 폴더를
// 가리키면 category 이름으로 한 번 더 매칭해보고, 그래도 없으면 미분류로 뺀다.
//
// category 폴백은 "전체경로" 문자열로 매칭해야 한다 — Fabric에서 가져온(관리자
// "가져오기") 보고서는 category에 하위 폴더까지 포함한 전체경로("공장/생산", "/"로
// join — services/fabric.py::_fetch_fabric_folder_map과 동일 규칙)가 들어있는데,
// 예전엔 폴더 "이름 하나"만 키로 쓰는 맵에 그 전체경로로 조회해서 depth 0(최상위) 폴더
// 말고는 절대 못 찾았다 — 하위 폴더 안의 보고서가 전부 미분류로 떨어져 트리 밖에
// 낱개로 나오던 버그였다(2026-08-12). namePathToFolderId는 각 폴더의 부모 이름까지
// 이어 붙인 전체경로를 키로 쓰므로 깊이 상관없이 매칭된다.
function buildFolderTree(reports: ReportItem[], folders: ReportFolder[]) {
  const byId = new Map<number, FolderNode>();
  for (const f of folders) byId.set(f.id, { id: f.id, name: f.name, path: String(f.id), children: [], reports: [] });
  const roots: FolderNode[] = [];
  for (const f of folders) {
    const node = byId.get(f.id)!;
    const parent = f.parent_id != null ? byId.get(f.parent_id) : undefined;
    if (parent) { node.path = `${parent.path}/${node.id}`; parent.children.push(node); }
    else roots.push(node);
  }
  // id-path(node.path)는 부모가 자식보다 먼저 처리된단 보장이 없어(Map 순회 순서) 위
  // 루프에서 얕게 잡힐 수 있다 — 완성된 트리를 다시 훑으며 depth-first로 확정한다.
  // 같은 순회에서 이름 기준 전체경로(namePathToFolderId)도 함께 만든다.
  const namePathToFolderId = new Map<string, number>();
  const fixPaths = (node: FolderNode, idPrefix: string, namePrefix: string) => {
    node.path = idPrefix ? `${idPrefix}/${node.id}` : String(node.id);
    const namePath = namePrefix ? `${namePrefix}/${node.name}` : node.name;
    namePathToFolderId.set(namePath, node.id);
    for (const child of node.children) fixPaths(child, node.path, namePath);
  };
  for (const root of roots) fixPaths(root, "", "");

  const uncategorized: ReportItem[] = [];
  for (const report of reports) {
    let node = report.portal_folder_id != null ? byId.get(report.portal_folder_id) : undefined;
    if (!node && report.category) {
      const fallbackId = namePathToFolderId.get(report.category);
      if (fallbackId != null) node = byId.get(fallbackId);
    }
    if (node) node.reports.push(report);
    else uncategorized.push(report);
  }
  return { tree: roots, uncategorized };
}

function loadCollapsed() { try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "{}"); } catch { return {}; } }
function NavItem({active,onClick,children}:{active:boolean;onClick:()=>void;children:React.ReactNode}) { return <div className={`app-nav-item${active ? " active" : ""}`} onClick={onClick}>{children}</div>; }
function TreeGroup({node,depth,collapsed,onToggle,onSelectFolder,activeId,onOpen}:{node:FolderNode;depth:number;collapsed:Record<string,boolean>;onToggle:(path:string)=>void;onSelectFolder:(path:string|null)=>void;activeId:number|null;onOpen:(report:ReportItem)=>void}) {
  return <div className={`rp-group${(collapsed[node.path] ?? true) ? " collapsed" : ""}`}><div className="rp-group-header" style={{paddingLeft:30+depth*12}} onClick={() => {onSelectFolder(node.path);onToggle(node.path);}}><ChevronDown size={13} className="rp-group-arrow"/> {node.name}</div><div className="rp-group-body">{node.children.map((child) => <TreeGroup key={child.path} node={child} depth={depth+1} collapsed={collapsed} onToggle={onToggle} onSelectFolder={onSelectFolder} activeId={activeId} onOpen={onOpen}/>)}{node.reports.map((report) => <TreeItem key={report.id} report={report} active={report.id===activeId} onOpen={onOpen} indent depth={depth}/>)}</div></div>;
}
function TreeItem({report,active,onOpen,indent,depth=0}:{report:ReportItem;active:boolean;onOpen:(report:ReportItem)=>void;indent?:boolean;depth?:number}) { return <div className={`rp-tree-item${active ? " active" : ""}${indent ? " indent" : ""}`} style={depth ? {paddingLeft:46+depth*12}:undefined} onClick={() => onOpen(report)} title={report.name}><span className="rp-tree-label">{report.name}</span></div>; }
