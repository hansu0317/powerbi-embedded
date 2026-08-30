// 보고서 미리보기 모달. pages/ReportPage.tsx에서 2026-08-27에 분리했다(분리 배경은
// components/report/tabState.ts 상단 주석 참고).
//
// 2026-08-27: 여기 같이 있던 UpdateReportModal(v7 콘텐츠 교체)과 MyActivityModal
// (내 활동)은 학습용 코드 축소 과정에서 제거했다 — git 이력(v2 태그)에 남아있음.
import { BarChart3, Star, X } from "lucide-react";

import type { ReportItem } from "../../lib/bootstrap";

export function ReportInfoModal({
  report,
  fav,
  onToggleFav,
  onOpen,
  onClose,
}: {
  report: ReportItem;
  fav: boolean;
  onToggleFav: () => void;
  onOpen: () => void;
  onClose: () => void;
}) {
  return (
    <div className="rp-modal-overlay" onClick={onClose}>
      <div className="rp-modal" onClick={(e) => e.stopPropagation()}>
        <button className="rp-modal-x" title="닫기" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="rp-modal-hero">
          <BarChart3 size={30} className="icn" />
        </div>
        <h2 className="rp-modal-name">{report.name}</h2>
        <dl className="rp-modal-info">
          <div>
            <dt>폴더</dt>
            <dd>{report.category || "미분류"}</dd>
          </div>
          <div>
            <dt>구분</dt>
            <dd>
              {report.report_type === "managed"
                ? "공용 보고서"
                : `개인 보고서 · ${report.owner_username || "-"}`}
            </dd>
          </div>
          {report.description && (
            <div>
              <dt>설명</dt>
              <dd>{report.description}</dd>
            </div>
          )}
        </dl>
        <div className="rp-modal-actions">
          <button
            className={`btn btn-ghost rp-modal-fav${fav ? " on" : ""}`}
            onClick={onToggleFav}
          >
            <Star size={15} className="icn" fill={fav ? "currentColor" : "none"} />{" "}
            {fav ? "즐겨찾기 해제" : "즐겨찾기"}
          </button>
          <button className="btn btn-primary" onClick={onOpen}>
            보고서 열기
          </button>
        </div>
      </div>
    </div>
  );
}
