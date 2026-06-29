import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pbi from "powerbi-client";
import {
  BarChart3,
  ChevronDown,
  Folder,
  Info,
  LayoutList,
  Search,
  Upload,
  X,
} from "lucide-react";
import type { ReportData, ReportItem } from "../bootstrap";
import { fetchEmbed, fetchUploadStatus } from "../api";

// PowerBI 서비스 싱글턴 (탭 전체가 공유)
const powerbi = new pbi.service.Service(
  pbi.factories.hpmFactory,
  pbi.factories.wpmpFactory,
  pbi.factories.routerFactory,
);

type View = "my" | "all" | "upload";
interface OpenTab {
  id: number;
  name: string;
}

const TABS_KEY = "open-tabs";
const ACTIVE_KEY = "active-tab";
const GROUPS_KEY = "sb-groups";

function loadTabs(): OpenTab[] {
  try {
    return JSON.parse(sessionStorage.getItem(TABS_KEY) || "[]");
  } catch {
    return [];
  }
}

export default function ReportPage({ data }: { data: ReportData }) {
  const { user, reports, csrf_token } = data;

  const [view, setView] = useState<View>("my");
  const [tabs, setTabs] = useState<OpenTab[]>(() => loadTabs());
  const [active, setActive] = useState<number | null>(
    () => Number(sessionStorage.getItem(ACTIVE_KEY)) || null,
  );

  useEffect(() => {
    sessionStorage.setItem(TABS_KEY, JSON.stringify(tabs));
    sessionStorage.setItem(ACTIVE_KEY, String(active ?? ""));
  }, [tabs, active]);

  const openReport = useCallback((report: ReportItem) => {
    setTabs((prev) =>
      prev.some((t) => t.id === report.id)
        ? prev
        : [...prev, { id: report.id, name: report.name }],
    );
    setActive(report.id);
    setView("my");
  }, []);

  const closeTab = useCallback((id: number) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      setActive((cur) =>
        cur !== id ? cur : next.length ? next[next.length - 1].id : null,
      );
      return next;
    });
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <a href="/" className="topbar-brand" title="홈으로">
          <span className="brand">
            <span className="b-quali">quali</span>
            <span className="b-soft">soft</span>
          </span>
        </a>
        <span className="topbar-section">데이터 시각화</span>
        <div className="topbar-spacer" />
        <div className="topbar-right">
          <span className="topbar-user">{user.display_name}</span>
          {user.is_admin && (
            <a href="/admin" className="topbar-btn">
              관리자 포털
            </a>
          )}
          <a
            href="/logout"
            className="topbar-btn primary"
            onClick={() => sessionStorage.clear()}
          >
            로그아웃
          </a>
        </div>
      </header>

      <div className="app-body">
        <Sidebar
          reports={reports}
          view={view}
          activeId={active}
          onSelectView={setView}
          onOpen={openReport}
        />
        <main className="app-main">
          {view === "my" && (
            <MyReportsView
              reports={reports}
              displayName={user.display_name}
              tabs={tabs}
              active={active}
              onActivate={setActive}
              onClose={closeTab}
              onOpen={openReport}
              onGoUpload={() => setView("upload")}
            />
          )}
          {view === "all" && (
            <AllReportsView reports={reports} onOpen={openReport} />
          )}
          {view === "upload" && <UploadView csrf={csrf_token} />}
        </main>
      </div>
    </div>
  );
}

/* ── 사이드바 ─────────────────────────────────────────── */
function Sidebar({
  reports,
  view,
  activeId,
  onSelectView,
  onOpen,
}: {
  reports: ReportItem[];
  view: View;
  activeId: number | null;
  onSelectView: (v: View) => void;
  onOpen: (r: ReportItem) => void;
}) {
  const { grouped, uncategorized } = useMemo(() => {
    const g = new Map<string, ReportItem[]>();
    const u: ReportItem[] = [];
    for (const r of reports) {
      if (r.category) {
        if (!g.has(r.category)) g.set(r.category, []);
        g.get(r.category)!.push(r);
      } else u.push(r);
    }
    return { grouped: g, uncategorized: u };
  }, [reports]);

  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(GROUPS_KEY) || "{}");
    } catch {
      return {};
    }
  });
  const toggle = (cat: string) =>
    setCollapsed((prev) => {
      const next = { ...prev, [cat]: !prev[cat] };
      sessionStorage.setItem(GROUPS_KEY, JSON.stringify(next));
      return next;
    });

  return (
    <nav className="app-sidebar">
      <div className="app-sidebar-title">데이터 시각화</div>
      <div className="app-sidebar-scroll">
        <div
          className={`app-nav-item${view === "my" ? " active" : ""}`}
          onClick={() => onSelectView("my")}
        >
          <Folder size={17} className="icn" /> 내 보고서
        </div>

        {view === "my" && (
          <div className="rp-tree">
            {[...grouped.entries()].map(([cat, items]) => (
              <div
                key={cat}
                className={`rp-group${collapsed[cat] ? " collapsed" : ""}`}
              >
                <div className="rp-group-header" onClick={() => toggle(cat)}>
                  <ChevronDown size={13} className="icn rp-group-arrow" /> {cat}
                </div>
                <div className="rp-group-body">
                  {items.map((r) => (
                    <TreeItem
                      key={r.id}
                      report={r}
                      active={r.id === activeId}
                      onOpen={onOpen}
                      indent
                    />
                  ))}
                </div>
              </div>
            ))}
            {uncategorized.map((r) => (
              <TreeItem
                key={r.id}
                report={r}
                active={r.id === activeId}
                onOpen={onOpen}
              />
            ))}
            {reports.length === 0 && (
              <div className="rp-tree-empty">열람 가능한 보고서가 없습니다</div>
            )}
          </div>
        )}

        <div
          className={`app-nav-item${view === "all" ? " active" : ""}`}
          onClick={() => onSelectView("all")}
        >
          <LayoutList size={17} className="icn" /> 전체 보고서
        </div>
        <div
          className={`app-nav-item${view === "upload" ? " active" : ""}`}
          onClick={() => onSelectView("upload")}
        >
          <Upload size={17} className="icn" /> 보고서 등록
        </div>
      </div>
    </nav>
  );
}

function TreeItem({
  report,
  active,
  onOpen,
  indent,
}: {
  report: ReportItem;
  active: boolean;
  onOpen: (r: ReportItem) => void;
  indent?: boolean;
}) {
  return (
    <div
      className={`rp-tree-item${active ? " active" : ""}${indent ? " indent" : ""}`}
      onClick={() => onOpen(report)}
      title={report.name}
    >
      <BarChart3 size={15} className="icn" />
      <span className="rp-tree-label">{report.name}</span>
    </div>
  );
}

/* ── 내 보고서 (랜딩 / 탭 + 임베드, 탭은 하단) ─────────── */
function MyReportsView({
  reports,
  displayName,
  tabs,
  active,
  onActivate,
  onClose,
  onOpen,
  onGoUpload,
}: {
  reports: ReportItem[];
  displayName: string;
  tabs: OpenTab[];
  active: number | null;
  onActivate: (id: number) => void;
  onClose: (id: number) => void;
  onOpen: (r: ReportItem) => void;
  onGoUpload: () => void;
}) {
  if (tabs.length === 0) {
    return (
      <ReportLanding
        reports={reports}
        displayName={displayName}
        onOpen={onOpen}
        onGoUpload={onGoUpload}
      />
    );
  }
  return (
    <div className="rp-workarea">
      <div className="rp-panels">
        {tabs.map((t) => (
          <ReportPanel key={t.id} id={t.id} active={t.id === active} />
        ))}
      </div>
      {/* 탭 바 — 하단 */}
      <div className="rp-tabsbar">
        {tabs.map((t) => (
          <div
            key={t.id}
            className={`rp-tab${t.id === active ? " active" : ""}`}
            onClick={() => onActivate(t.id)}
          >
            <BarChart3 size={14} className="icn" />
            <span className="rp-tab-label">{t.name}</span>
            <button
              className="rp-tab-close"
              title="닫기"
              onClick={(e) => {
                e.stopPropagation();
                onClose(t.id);
              }}
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function ReportLanding({
  reports,
  displayName,
  onOpen,
  onGoUpload,
}: {
  reports: ReportItem[];
  displayName: string;
  onOpen: (r: ReportItem) => void;
  onGoUpload: () => void;
}) {
  return (
    <div className="rp-landing">
      <div className="rp-landing-head">
        <div>
          <h1 className="rp-landing-hi">{displayName}님, 안녕하세요 👋</h1>
          <p className="rp-landing-sub">열람할 보고서를 선택해 시작하세요</p>
        </div>
        <button className="btn btn-ghost" onClick={onGoUpload}>
          <Upload size={16} className="icn" /> 새 보고서 등록
        </button>
      </div>

      {reports.length === 0 ? (
        <div className="rp-landing-empty">
          <BarChart3 size={52} className="icn" />
          <p>아직 열람 가능한 보고서가 없습니다</p>
        </div>
      ) : (
        <div className="rp-card-grid">
          {reports.map((r) => (
            <button key={r.id} className="rp-card" onClick={() => onOpen(r)}>
              <div className="rp-card-icon">
                <BarChart3 size={22} className="icn" />
              </div>
              <div className="rp-card-body">
                <div className="rp-card-name" title={r.name}>
                  {r.name}
                </div>
                <div className="rp-card-meta">
                  {r.category ? (
                    <span className="rp-card-tag">{r.category}</span>
                  ) : (
                    <span className="rp-card-tag muted">미분류</span>
                  )}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ReportPanel({ id, active }: { id: number; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const el = ref.current;
    (async () => {
      try {
        const d = await fetchEmbed(id);
        if (cancelled || !el) return;
        const s = d.settings || {};
        const config: pbi.IEmbedConfiguration = {
          type: "report",
          id: d.report_id,
          embedUrl: d.embed_url,
          accessToken: d.embed_token,
          tokenType: pbi.models.TokenType.Embed,
          settings: {
            navContentPaneEnabled: Boolean(s.enable_page_nav),
            filterPaneEnabled: Boolean(s.enable_filter),
            layoutType: pbi.models.LayoutType.Custom,
            customLayout: {
              displayOption: pbi.models.DisplayOption.FitToWidth,
            },
            panes: {
              pageNavigation: { visible: Boolean(s.enable_page_nav) },
              filters: { visible: Boolean(s.enable_filter), expanded: false },
            },
          },
        };
        if (s.default_page) config.pageName = s.default_page;
        const report = powerbi.embed(el, config);
        report.on("loaded", () => !cancelled && setLoading(false));
        report.on("error", (ev: any) => {
          if (cancelled) return;
          setLoading(false);
          setError(`오류: ${JSON.stringify(ev.detail)}`);
        });
      } catch (e) {
        if (!cancelled) {
          setLoading(false);
          setError(`보고서 로드 실패: ${(e as Error).message}`);
        }
      }
    })();
    return () => {
      cancelled = true;
      if (el) powerbi.reset(el);
    };
  }, [id]);

  return (
    <div className={`rp-panel${active ? " active" : ""}`}>
      {loading && !error && (
        <div className="rp-panel-loading">보고서 불러오는 중...</div>
      )}
      {error && <div className="rp-panel-error">{error}</div>}
      <div className="rp-embed" ref={ref} />
    </div>
  );
}

/* ── 전체 보고서 (검색 + 표) ───────────────────────────── */
function AllReportsView({
  reports,
  onOpen,
}: {
  reports: ReportItem[];
  onOpen: (r: ReportItem) => void;
}) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const k = q.trim().toLowerCase();
    if (!k) return reports;
    return reports.filter(
      (r) =>
        r.name.toLowerCase().includes(k) ||
        (r.category || "").toLowerCase().includes(k),
    );
  }, [q, reports]);

  return (
    <div className="rp-page">
      <h1 className="rp-page-title">전체 보고서</h1>
      <div className="rp-search">
        <Search size={17} className="icn rp-search-icon" />
        <input
          placeholder="검색어를 입력하세요"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      <div className="card-table rp-all-table">
        <table>
          <colgroup>
            <col style={{ width: "42%" }} />
            <col style={{ width: "23%" }} />
            <col style={{ width: "15%" }} />
            <col style={{ width: "20%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>보고서 명</th>
              <th>카테고리</th>
              <th style={{ textAlign: "center" }}>권한 보유</th>
              <th>소유자명</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr
                key={r.id}
                className="rp-all-row"
                onClick={() => onOpen(r)}
                title="클릭하여 열기"
              >
                <td className="rp-all-name">
                  <BarChart3 size={15} className="icn" /> {r.name}
                </td>
                <td>{r.category || "-"}</td>
                <td style={{ textAlign: "center", color: "var(--sage-deep)" }}>
                  ✓
                </td>
                <td>
                  {r.owner_username ||
                    (r.report_type === "managed" ? "공용" : "-")}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={4} className="rp-all-empty">
                  표시할 보고서가 없습니다
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="rp-total">Total {filtered.length} records</div>
    </div>
  );
}

/* ── 보고서 등록 (업로드 페이지) ───────────────────────── */
const DONE_STATES = new Set(["completed", "failed", "conflict", "unknown"]);
const STATUS_LABELS: Record<string, string> = {
  accepted: "PBI 접수됨",
  publishing: "PBI 변환 중",
  pbi_succeeded: "DB 등록 중",
};

function UploadView({ csrf }: { csrf: string }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");
  const [reportName, setReportName] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ msg: string; tone: "" | "ok" | "err" }>(
    { msg: "", tone: "" },
  );

  const submit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setStatus({ msg: ".pbix 파일을 선택해 주세요.", tone: "err" });
      return;
    }
    setBusy(true);
    setStatus({ msg: `'${file.name}' 전송 중...`, tone: "" });
    try {
      const accepted = await uploadWithName(file, reportName.trim(), csrf);
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
      <div className="rp-form-card">
        <div className="rp-field">
          <label>보고서 명</label>
          <input
            placeholder="비워 두면 파일명이 보고서 이름이 됩니다"
            value={reportName}
            onChange={(e) => setReportName(e.target.value)}
            disabled={busy}
          />
        </div>

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
        </div>

        <div className="rp-upload-note">
          <Info size={15} className="icn" /> 업로드한 보고서는 본인 폴더로 자동
          분류됩니다. RLS가 필요하면 관리자에게 요청하세요.
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
  );
}

// report_name 을 함께 전송하는 업로드 (api.uploadPbix 는 file 만 전송)
async function uploadWithName(file: File, reportName: string, csrf: string) {
  const fd = new FormData();
  fd.append("file", file);
  if (reportName) fd.append("report_name", reportName);
  const res = await fetch("/api/upload", {
    method: "POST",
    body: fd,
    headers: { "X-CSRF-Token": csrf },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as any).detail;
    const msg =
      (typeof detail === "object" ? detail?.message : detail) || res.statusText;
    throw new Error(msg);
  }
  return data as { job_id: number; report_name: string; status: string };
}
