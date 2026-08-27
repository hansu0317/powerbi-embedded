// 보고서 등록(업로드) 화면 — pages/ReportPage.tsx에서 2026-08-27에 분리했다
// (분리 배경은 components/report/tabState.ts 상단 주석 참고).
import { useEffect, useRef, useState } from "react";
import { Folder, Info } from "lucide-react";

import {
  ReportFolder, fetchUploadStatus, fetchWritableFolders, uploadPbix,
} from "../../lib/api";
import { orderFoldersAsTree } from "../../lib/folderTree";
import { ACTIVE_KEY, TABS_KEY } from "./tabState";

const DONE_STATES = new Set(["completed", "failed", "conflict", "unknown"]);
const STATUS_LABELS: Record<string, string> = {
  accepted: "PBI 접수됨",
  publishing: "PBI 변환 중",
  pbi_succeeded: "DB 등록 중",
};

// 파일명에서 확장자만 뗀 값이 그대로 보고서 명이 된다(서버도 동일 로직 —
// routes/report_upload.py의 _read_and_validate_pbix). 여기서는 미리보기 용도로만 쓴다.
function deriveReportName(fileName: string): string {
  return fileName.toLowerCase().endsWith(".pbix") ? fileName.slice(0, -5) : fileName;
}

export function UploadView({ csrf, isAdmin }: { csrf: string; isAdmin: boolean }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");
  const [description, setDescription] = useState("");
  const [folders,setFolders]=useState<ReportFolder[]>([]);
  const [folderId,setFolderId]=useState<number|null>(null);
  const visibility = "personal" as const;
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ msg: string; tone: "" | "ok" | "err" }>(
    { msg: "", tone: "" },
  );
  // 폴더 생성·이름변경·삭제는 이 앱에서 다루지 않는다(2026-08-12~) — 이미 있는 폴더
  // 중에서 고르기만 한다. 새 폴더가 필요하면 Power BI/Fabric 쪽 구조를 먼저 정리한다.
  // 업로드 대상은 "쓸 수 있는" 폴더 전부(빈 공용 폴더 포함) — 사이드바 탐색 트리(열람
  // 가능 기준, fetchReportFolders)와 의도적으로 다른 엔드포인트를 쓴다.
  useEffect(()=>{fetchWritableFolders().then((f)=>{setFolders(f);setFolderId(f[0]?.id??null)}).catch(()=>{})},[]);

  const submit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setStatus({ msg: ".pbix 파일을 선택해 주세요.", tone: "err" });
      return;
    }
    const folderName = folders.find((f) => f.id === folderId)?.name;
    if (folderName && !confirm(`'${folderName}' 폴더에 등록합니다. 맞습니까?`)) {
      return;
    }
    setBusy(true);
    setStatus({ msg: `'${file.name}' 전송 중...`, tone: "" });
    try {
      const accepted = await uploadPbix(file, csrf, description.trim(), folderId, visibility);
      const jobId = accepted.job_id;
      const name = accepted.report_name;
      setStatus({ msg: `'${name}' PBI 게시 중... (보통 30초~2분)`, tone: "" });

      for (;;) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await fetchUploadStatus(jobId, csrf);
        if (s.status === "completed") {
          setStatus({
            msg: `'${name}' 게시 완료! 목록을 새로고침합니다...`,
            tone: "ok",
          });
          sessionStorage.removeItem(TABS_KEY);
          sessionStorage.removeItem(ACTIVE_KEY);
          setTimeout(() => location.reload(), 1500);
          return;
        }
        if (DONE_STATES.has(s.status))
          throw new Error(s.error || `게시 실패 (${s.status})`);
        setStatus({
          msg: `'${name}' ${STATUS_LABELS[s.status] || s.status}... (보통 30초~2분)`,
          tone: "",
        });
      }
    } catch (e) {
      setStatus({ msg: `업로드 실패: ${(e as Error).message}`, tone: "err" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rp-page">
      <h1 className="rp-page-title">보고서 등록</h1>
      <div className="rp-upload-workspace">
       <aside className="rp-upload-folders">
        <div className="rp-upload-side-title">저장 위치</div>
        {orderFoldersAsTree(folders).map(({ folder: f, depth }) => (
          <button
            key={f.id}
            className={`rp-upload-folder${folderId===f.id?" active":""}${depth>0?" rp-upload-folder--child":""}`}
            style={{ paddingLeft: 9 + depth * 16 }}
            onClick={()=>setFolderId(f.id)}
          >
            <Folder size={depth>0?13:15}/><span>{f.name}</span><small>{f.report_count}</small>
          </button>
        ))}
        {folders.length === 0 && <span className="rp-upload-side-empty">등록 가능한 폴더가 없습니다 — 관리자에게 문의하세요.</span>}
        <span className="rp-upload-side-hint">필요한 폴더가 없나요? 이 목록은 자동으로 늘어나지 않습니다 — 개발 담당자에게 Fabric 폴더 반영을 요청하세요.</span>
       </aside>
      <div className="rp-form-card">
        <div className="rp-field">
          <label>
            Report 파일 선택 <span className="rp-req">(.pbix) *</span>
          </label>
          <div className="rp-filepick">
            <button
              type="button"
              className="btn btn-ghost"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              파일 선택
            </button>
            <span className="rp-filename">{fileName || "선택된 파일 없음"}</span>
            <input
              ref={fileRef}
              type="file"
              accept=".pbix"
              hidden
              onChange={(e) => setFileName(e.target.files?.[0]?.name || "")}
            />
          </div>
          {fileName && (
            <span className="rp-field-hint">
              보고서 명 : {deriveReportName(fileName)} (파일명 그대로 사용됩니다)
            </span>
          )}
        </div>

        <div className="rp-upload-policy">
          <strong>신규 보고서는 비공개로 등록됩니다.</strong>
          <span>{isAdmin ? "등록 후 관리자 보고서 권한에서 부서 또는 공용으로 공개할 수 있습니다." : "공유가 필요하면 관리자에게 부서 또는 공용 공개를 요청하세요."}</span>
        </div>

        <div className="rp-field">
          <label>보고서 설명 (선택)</label>
          <input
            placeholder="어떤 데이터를 보여주는 보고서인지 적어두면 검색·발견이 쉬워집니다"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={500}
            disabled={busy}
          />
        </div>

        <div className="rp-upload-note">
          <Info size={15} className="icn" />
          <div>
            <b>새 보고서를 등록하는 화면입니다.</b>
            <span>PBIX 파일명이 보고서 이름이 됩니다. 예: <code>test0101.pbix</code> → <code>test0101</code></span>
            <span>이미 등록된 보고서를 수정하려면 새로 등록하지 말고, 해당 보고서를 연 뒤 <b>업데이트</b>를 사용하세요.</span>
          </div>
        </div>

        {status.msg && (
          <div className={`rp-upload-feedback ${status.tone}`}>{status.msg}</div>
        )}

        <div className="rp-form-actions">
          <button className="btn btn-primary" disabled={busy} onClick={submit}>
            {busy ? "처리 중..." : "파일 업로드"}
          </button>
        </div>
      </div>
      </div>
    </div>
  );
}
