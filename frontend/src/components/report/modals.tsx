// 보고서 화면의 작은 모달 3종 — 콘텐츠 업데이트, 내 활동, 보고서 미리보기.
// pages/ReportPage.tsx에서 2026-08-27에 분리했다(분리 배경은
// components/report/tabState.ts 상단 주석 참고).
import { useEffect, useState } from "react";
import { BarChart3, Star, X } from "lucide-react";

import type { ReportItem } from "../../lib/bootstrap";
import {
  MyActivityRow, fetchMyActivity, fetchUploadStatus, startReportUpdate,
} from "../../lib/api";

/** 보고서 콘텐츠 업데이트 모달 (v7) — 새 pbix로 페이지·시각화만 교체, 데이터셋은 유지.
 * 소유자·admin만 열 수 있다(toolbar에서 canEditActive로 이미 걸러짐). */
export function UpdateReportModal({
  reportId,
  reportName,
  csrf,
  onClose,
}: {
  reportId: number;
  reportName: string;
  csrf: string;
  onClose: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ msg: string; tone: "" | "ok" | "err" }>({ msg: "", tone: "" });

  // 업데이트는 "같은 보고서의 수정본"만 받는다 — 엉뚱한 pbix를 잘못 고르면 관계없는
  // 페이지·시각화가 기존 데이터셋(RLS·관계·DAX) 위에 그대로 얹히므로, 파일명이 보고서
  // 이름과 일치할 때만 진행하게 막는다. 최종 확인은 서버가 한 번 더 한다(routes/report.py).
  const fileStem = file ? file.name.replace(/\.pbix$/i, "") : "";
  const nameMismatch = !!file && fileStem.toLowerCase() !== reportName.trim().toLowerCase();

  const submit = async () => {
    if (!file || nameMismatch) return;
    if (
      !confirm(
        `"${reportName}"의 페이지·시각화를 "${file.name}" 내용으로 완전히 교체합니다.\n` +
          "되돌릴 수 없습니다 (데이터셋·RLS 설정은 유지됩니다). 계속할까요?",
      )
    )
      return;
    setBusy(true);
    setStatus({ msg: "업로드 중...", tone: "" });
    try {
      const accepted = await startReportUpdate(reportId, file, csrf);
      const jobId = accepted.job_id;
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await fetchUploadStatus(jobId, csrf);
        if (s.status === "completed") {
          setStatus({ msg: "업데이트 완료! 잠시 후 새로고침됩니다.", tone: "ok" });
          setTimeout(() => location.reload(), 1200);
          return;
        }
        if (["failed", "unknown", "conflict", "db_failed"].includes(s.status)) {
          setStatus({ msg: s.error || "업데이트 실패", tone: "err" });
          setBusy(false);
          return;
        }
        setStatus({ msg: `처리 중 (${s.status})...`, tone: "" });
      }
      setStatus({ msg: "시간 초과 — 관리자에게 문의하세요.", tone: "err" });
      setBusy(false);
    } catch (e) {
      setStatus({ msg: (e as Error).message, tone: "err" });
      setBusy(false);
    }
  };

  return (
    <div className="rp-modal-overlay" onClick={busy ? undefined : onClose}>
      <div className="rp-modal" onClick={(e) => e.stopPropagation()}>
        {!busy && (
          <button className="rp-modal-x" onClick={onClose}>
            <X size={18} />
          </button>
        )}
        <div className="rp-modal-name">보고서 업데이트 — {reportName}</div>
        <p className="rp-landing-sub" style={{ marginBottom: 16 }}>
          기존 보고서의 화면만 새 PBIX 내용으로 교체합니다. 아래 조건을 모두 확인하세요.
        </p>
        <ul className="rp-update-rules">
          <li>파일명은 반드시 <b>{reportName}.pbix</b>여야 합니다.</li>
          <li>데이터셋, 관계, DAX와 RLS 역할은 기존 보고서의 것을 유지합니다.</li>
          <li>페이지와 시각화는 새 파일 내용으로 교체되며 자동으로 되돌릴 수 없습니다.</li>
          <li>대시보드는 업데이트할 수 없고, 보고서 소유자 또는 관리자만 실행할 수 있습니다.</li>
        </ul>
        <div className="rp-filepick" style={{ marginBottom: 16 }}>
          <input
            type="file"
            accept=".pbix"
            disabled={busy}
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </div>
        {nameMismatch && (
          <div className="rp-upload-feedback err">
            파일명이 "{reportName}.pbix"와 다릅니다 ("{file?.name}"). 같은 보고서의 수정본만 업데이트할 수 있어요.
          </div>
        )}
        {status.msg && <div className={`rp-upload-feedback ${status.tone}`}>{status.msg}</div>}
        <div className="rp-form-actions" style={{ marginTop: 16 }}>
          <button className="btn btn-primary" disabled={!file || nameMismatch || busy} onClick={submit}>
            {busy ? "처리 중..." : "업데이트 시작"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 내 활동 로그 모달 (v6) — 관리자 로그 화면과 별개로, 일반 사용자가 자기 이력만 본다. */
export function MyActivityModal({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<MyActivityRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchMyActivity()
      .then(setRows)
      .catch(() => setError("활동 로그 조회 실패"));
  }, []);

  return (
    <div className="rp-modal-overlay" onClick={onClose}>
      <div className="rp-modal rp-activity-modal" onClick={(e) => e.stopPropagation()}>
        <button className="rp-modal-x" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="rp-modal-name">내 활동</div>
        <div className="rp-activity-list">
          {error && <div className="rp-panel-error">{error}</div>}
          {!error && !rows && <div className="rp-landing-sub">불러오는 중...</div>}
          {rows && rows.length === 0 && <div className="rp-landing-sub">활동 기록이 없습니다.</div>}
          {rows?.map((r) => (
            <div key={r.id} className="rp-activity-row">
              <span className={`pill ${r.event === "report_upload" ? "pending" : "active"}`}>
                {r.event === "report_upload" ? "업로드" : "열람"}
              </span>
              <span className="rp-activity-name">{r.report_name || "-"}</span>
              <span className="rp-activity-time">{String(r.created_at).replace("T", " ").slice(0, 16)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

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
