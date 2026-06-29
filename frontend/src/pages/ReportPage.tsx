import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pbi from "powerbi-client";
import type { ReportData, ReportItem } from "../bootstrap";
import { fetchEmbed, uploadPbix, fetchUploadStatus } from "../api";

// PowerBI 서비스 싱글턴 (탭 전체가 공유)
const powerbi = new pbi.service.Service(
  pbi.factories.hpmFactory,
  pbi.factories.wpmpFactory,
  pbi.factories.routerFactory,
);

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
function loadActive(): number | null {
  const v = Number(sessionStorage.getItem(ACTIVE_KEY) || "0");
  return v || null;
}

export default function ReportPage({ data }: { data: ReportData }) {
  const { user, reports, csrf_token } = data;

  const [tabs, setTabs] = useState<OpenTab[]>(() => loadTabs());
  const [active, setActive] = useState<number | null>(() => loadActive());

  // 탭 상태를 세션에 영속화 (새로고침 후 복원)
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
  }, []);

  const closeTab = useCallback(
    (id: number) => {
      setTabs((prev) => {
        const next = prev.filter((t) => t.id !== id);
        setActive((cur) => {
          if (cur !== id) return cur;
          return next.length ? next[next.length - 1].id : null;
        });
        return next;
      });
    },
    [],
  );

  return (
    <div className="rp-shell">
      <Header user={user} />
      <div className="rp-layout">
        <Sidebar
          reports={reports}
          activeId={active}
          onOpen={openReport}
          csrf={csrf_token}
        />
        <main className="rp-content">
          <div className="rp-panels">
            {tabs.length === 0 && (
              <div className="rp-empty">
                <div className="rp-empty-icon">📊</div>
                <h2>왼쪽에서 보고서를 선택하세요</h2>
                <p>여러 보고서를 탭으로 동시에 열 수 있습니다</p>
              </div>
            )}
            {tabs.map((t) => (
              <ReportPanel key={t.id} id={t.id} active={t.id === active} />
            ))}
          </div>
          <div className="rp-tabsbar">
            {tabs.length === 0 ? (
              <div className="rp-no-tabs">← 보고서를 선택하세요</div>
            ) : (
              tabs.map((t) => (
                <div
                  key={t.id}
                  className={`rp-tab${t.id === active ? " active" : ""}`}
                  onClick={() => setActive(t.id)}
                >
                  <span>📊</span>
                  <span className="rp-tab-label">{t.name}</span>
                  <button
                    className="rp-tab-close"
                    title="닫기"
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(t.id);
                    }}
                  >
                    ×
                  </button>
                </div>
              ))
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

function Header({ user }: { user: ReportData["user"] }) {
  return (
    <header className="rp-header">
      <span className="brand on-dark rp-brand">
        <span className="b-quali">quali</span>
        <span className="b-soft">soft</span>
        <span className="b-dot">.</span>
        <span className="rp-brand-tag">BI 포털</span>
      </span>
      <div className="rp-user">
        <span className="rp-username">{user.display_name}</span>
        {user.is_admin && (
          <a href="/admin" className="rp-hdr-btn admin">
            관리자 포털
          </a>
        )}
        <a
          href="/logout"
          className="rp-hdr-btn"
          onClick={() => sessionStorage.clear()}
        >
          로그아웃
        </a>
      </div>
    </header>
  );
}

function Sidebar({
  reports,
  activeId,
  onOpen,
  csrf,
}: {
  reports: ReportItem[];
  activeId: number | null;
  onOpen: (r: ReportItem) => void;
  csrf: string;
}) {
  const { grouped, uncategorized } = useMemo(() => {
    const g = new Map<string, ReportItem[]>();
    const u: ReportItem[] = [];
    for (const r of reports) {
      if (r.category) {
        if (!g.has(r.category)) g.set(r.category, []);
        g.get(r.category)!.push(r);
      } else {
        u.push(r);
      }
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

  const toggle = (cat: string) => {
    setCollapsed((prev) => {
      const next = { ...prev, [cat]: !prev[cat] };
      sessionStorage.setItem(GROUPS_KEY, JSON.stringify(next));
      return next;
    });
  };

  return (
    <nav className="rp-sidebar">
      <div className="rp-sidebar-title">내 보고서</div>
      <div className="rp-sidebar-scroll">
        {[...grouped.entries()].map(([cat, items]) => (
          <div
            key={cat}
            className={`rp-group${collapsed[cat] ? " collapsed" : ""}`}
          >
            <div className="rp-group-header" onClick={() => toggle(cat)}>
              <span className="rp-group-arrow">▼</span> 📁 {cat}
            </div>
            <div className="rp-group-body">
              {items.map((r) => (
                <SidebarItem
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
          <SidebarItem
            key={r.id}
            report={r}
            active={r.id === activeId}
            onOpen={onOpen}
          />
        ))}
      </div>
      <UploadBox csrf={csrf} />
    </nav>
  );
}

function SidebarItem({
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
      className={`rp-item${active ? " active" : ""}${indent ? " indent" : ""}`}
      onClick={() => onOpen(report)}
    >
      <span>📊</span>
      <span className="rp-item-label">{report.name}</span>
    </div>
  );
}

const DONE_STATES = new Set(["completed", "failed", "conflict", "unknown"]);
const STATUS_LABELS: Record<string, string> = {
  accepted: "PBI 접수됨",
  publishing: "PBI 변환 중",
  pbi_succeeded: "DB 등록 중",
};

function UploadBox({ csrf }: { csrf: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("파일명이 보고서 이름이 됩니다");
  const [tone, setTone] = useState<"" | "ok" | "err">("");

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setBusy(true);
    setTone("");
    setStatus(`'${file.name}' 전송 중...`);

    try {
      const { job_id, report_name } = await uploadPbix(file, csrf);
      setStatus(`'${report_name}' PBI 게시 중... (보통 30초~2분)`);

      for (;;) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await fetchUploadStatus(job_id, csrf);
        if (s.status === "completed") {
          setTone("ok");
          setStatus(`'${report_name}' 게시 완료! 목록을 새로고침합니다...`);
          sessionStorage.removeItem(TABS_KEY);
          sessionStorage.removeItem(ACTIVE_KEY);
          setTimeout(() => location.reload(), 1500);
          return;
        }
        if (DONE_STATES.has(s.status)) {
          throw new Error(s.error || `게시 실패 (${s.status})`);
        }
        setStatus(
          `'${report_name}' ${STATUS_LABELS[s.status] || s.status}... (보통 30초~2분)`,
        );
      }
    } catch (err) {
      setTone("err");
      setStatus(`업로드 실패: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rp-upload">
      <input
        ref={inputRef}
        type="file"
        accept=".pbix"
        hidden
        onChange={onPick}
      />
      <button
        className="rp-upload-btn"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        ⬆ 내 보고서 올리기 (.pbix)
      </button>
      <div className={`rp-upload-status${tone ? " " + tone : ""}`}>{status}</div>
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
