import { useEffect, useMemo, useState } from "react";
import { BarChart3, ChevronRight, Folder, FolderOpen } from "lucide-react";
import type { ReportItem } from "../lib/bootstrap";
import { fetchReportFolders, type ReportFolder } from "../lib/api";

type Props = { reports: ReportItem[]; onOpen: (report: ReportItem) => void };

export function ReportExplorer({ reports, onOpen }: Props) {
  const [folders, setFolders] = useState<ReportFolder[]>([]);
  const [folderId, setFolderId] = useState<number | null>(null);

  useEffect(() => {
    fetchReportFolders().then(setFolders).catch(() => setFolders([]));
  }, []);

  const current = folders.find((folder) => folder.id === folderId);
  const children = folders.filter((folder) => folder.parent_id === folderId);
  const folderReports = reports.filter(
    (report) => (report.portal_folder_id ?? null) === folderId,
  );
  const breadcrumbs = useMemo(() => {
    const result: ReportFolder[] = [];
    let node = current;
    while (node) {
      result.unshift(node);
      node = folders.find((folder) => folder.id === node?.parent_id);
    }
    return result;
  }, [current, folders]);

  return <>
    <div className="rp-explorer-crumbs">
      <button onClick={() => setFolderId(null)}>전체 보고서</button>
      {breadcrumbs.map((folder) => <span key={folder.id}>
        <ChevronRight size={13}/>
        <button onClick={() => setFolderId(folder.id)}>{folder.name}</button>
      </span>)}
    </div>
    <div className="rp-explorer">
      {children.length > 0 && <section className="rp-explorer-section">
        <h2>폴더</h2>
        <div className="rp-folder-grid">
          {children.map((folder) => <button key={folder.id} className="rp-folder-card" onClick={() => setFolderId(folder.id)}>
            <Folder size={34}/>
            <span><b>{folder.name}</b><small>{folder.report_count}개 보고서</small></span>
            <ChevronRight size={16}/>
          </button>)}
        </div>
      </section>}
      <section className="rp-explorer-section rp-explorer-files">
        <h2>PBIX 보고서 <span>{folderReports.length}</span></h2>
        <div className="rp-file-list">
          {folderReports.map((report) => <button key={report.id} className="rp-file-row" onClick={() => onOpen(report)}>
            <span className="rp-file-type"><BarChart3 size={18}/></span>
            <span className="rp-file-copy"><b>{report.name}</b><small>{report.description || report.category || "보고서 설명이 없습니다."}</small></span>
            <span className="rp-file-meta">{visibilityLabel(report.visibility)}</span>
            <span className="rp-file-open">열기 <ChevronRight size={15}/></span>
          </button>)}
          {folderReports.length === 0 && <div className="rp-folder-empty">
            <FolderOpen size={38}/>
            <p>{reports.length === 0 ? "아직 열람 가능한 보고서가 없습니다." : "이 폴더에는 보고서가 없습니다."}</p>
          </div>}
        </div>
      </section>
    </div>
  </>;
}

export function ReportCategoryList({ reports, category, onOpen }: Props & { category: string | null }) {
  const visible = category
    ? reports.filter((report) => report.category === category || report.category?.startsWith(`${category}/`))
    : reports;
  return <div className="rp-explorer rp-category-browser">
    <section className="rp-explorer-section rp-explorer-files">
      <h2>{category || "전체 보고서"} <span>{visible.length}</span></h2>
      <div className="rp-file-list">
        {visible.map((report) => <button key={report.id} className="rp-file-row" onClick={() => onOpen(report)}>
          <span className="rp-file-type"><BarChart3 size={18}/></span>
          <span className="rp-file-copy"><b>{report.name}</b><small>{report.description || report.category || "보고서 설명이 없습니다."}</small></span>
          <span className="rp-file-meta">{visibilityLabel(report.visibility)}</span>
          <span className="rp-file-open">열기 <ChevronRight size={15}/></span>
        </button>)}
        {!visible.length && <div className="rp-folder-empty"><FolderOpen size={38}/><p>이 분류에는 보고서가 없습니다.</p></div>}
      </div>
    </section>
  </div>;
}

function visibilityLabel(visibility?: ReportItem["visibility"]) {
  return visibility === "shared" ? "공용" : visibility === "group" ? "그룹" : "개인";
}
