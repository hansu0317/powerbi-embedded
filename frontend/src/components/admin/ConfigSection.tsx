// 관리자 포털 "설정" 패널 — 런타임 설정(app_config) 조회/저장.
// pages/AdminPage.tsx에서 2026-08-27에 분리했다(분리 배경은 components/admin/shared.tsx
// 상단 주석 참고).
import { useEffect, useState } from "react";

import { AppConfigRow, adminGetConfig, adminSetConfig } from "../../lib/api";

export function ConfigSection({
  csrf,
  showToast,
}: {
  csrf: string;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
}) {
  const [rows, setRows] = useState<AppConfigRow[] | null>(null);
  const [edited, setEdited] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    adminGetConfig()
      .then(setRows)
      .catch(() => showToast("설정 조회 실패", "err"));
  }, [showToast]);

  const [showAdvanced, setShowAdvanced] = useState(false);

  // 기본 노출은 "실제 장애/민원 상황에서 즉시 만지는 값"만 남긴다 — 그 외 정책·구현
  // 세부값은 전부 "고급 설정"으로 뺐다. 예: 파일이 안 올라간다(용량 초과), 오늘 한도
  // 찼다, 로그인이 잠겼다 — 이 셋은 관리자가 바로 이 화면에서 풀어줘야 하는 실제 민원이다.
  const categories: { title: string; hint?: string; keys: string[] }[] = [
    {
      title: "업로드",
      keys: ["max_pbix_size_mb", "max_uploads_per_day", "max_personal_reports"],
    },
    {
      title: "로그인 · 보안",
      hint: "직원이 로그인이 안 된다고 하면 여기 두 값을 확인하세요.",
      keys: ["login_block_max_fail", "login_block_minutes"],
    },
  ];
  const advancedKeys = [
    "password_min_len", "pbi_sync_interval", "pbi_token_cache_margin_sec",
    "activity_log_retention_days", "error_log_retention_days", "refresh_auto_retry_max",
    "report_name_max_len", "import_poll_interval_sec", "import_poll_max",
    "embed_token_lifetime_min",
    "recents_limit", "activity_log_max_rows", "admin_upload_jobs_limit",
  ];
  const byKey = new Map((rows ?? []).map((r) => [r.key, r]));
  const dirtyKeys = Object.keys(edited).filter((key) => {
    const row = byKey.get(key);
    return row && edited[key].trim() && edited[key] !== row.value;
  });
  const saveAll = async () => {
    if (!dirtyKeys.length) return;
    setSaving(true);
    const saved: Record<string, string> = {};
    try {
      for (const key of dirtyKeys) {
        const value = edited[key].trim();
        await adminSetConfig(key, value, csrf);
        saved[key] = value;
      }
      setRows((prev) => prev?.map((r) => saved[r.key] !== undefined ? { ...r, value: saved[r.key] } : r) ?? prev);
      setEdited((prev) => {
        const next = { ...prev };
        Object.keys(saved).forEach((key) => delete next[key]);
        return next;
      });
      showToast(`${dirtyKeys.length}개 설정을 저장했습니다.`, "ok");
    } catch (e) {
      showToast("오류: " + (e as Error).message, "err");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="cfg-page">
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>런타임 설정</h2>
      </div>
      {!rows && <div className="ad-modal-loading">불러오는 중...</div>}
      {rows &&
        categories.map((cat) => (
          <div key={cat.title} className="cfg-group">
            <h3 className="cfg-group-title">
              {cat.title}
              {cat.hint && <span className="cfg-group-hint">{cat.hint}</span>}
            </h3>
            <div className="cfg-grid">
              {cat.keys.map((key) => {
                const r = byKey.get(key);
                if (!r) return null;
                const dirty = edited[key] !== undefined && edited[key] !== r.value;
                return (
                  <div key={key} className={`cfg-card${dirty ? " dirty" : ""}`}>
                    <div className="cfg-key">{key}</div>
                    <div className="cfg-desc">{r.description || "-"}</div>
                    <div className="cfg-row">
                      <input
                        inputMode="numeric"
                        value={edited[key] ?? r.value}
                        onChange={(e) =>
                          setEdited((prev) => ({ ...prev, [key]: e.target.value }))
                        }
                        onKeyDown={(e) => e.key === "Enter" && dirty && saveAll()}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      {rows && (
        <div className="cfg-group">
          <button
            type="button"
            className="cfg-advanced-toggle"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "▾" : "▸"} 고급 설정 ({advancedKeys.length})
          </button>
          {showAdvanced && (
            <div className="cfg-grid">
              {advancedKeys.map((key) => {
                const r = byKey.get(key);
                if (!r) return null;
                const dirty = edited[key] !== undefined && edited[key] !== r.value;
                return (
                  <div key={key} className={`cfg-card${dirty ? " dirty" : ""}`}>
                    <div className="cfg-key">{key}</div>
                    <div className="cfg-desc">{r.description || "-"}</div>
                    <div className="cfg-row">
                      <input
                        inputMode="numeric"
                        value={edited[key] ?? r.value}
                        onChange={(e) =>
                          setEdited((prev) => ({ ...prev, [key]: e.target.value }))
                        }
                        onKeyDown={(e) => e.key === "Enter" && dirty && saveAll()}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
      <div className="cfg-savebar">
        <span>{dirtyKeys.length ? `${dirtyKeys.length}개 변경됨` : "변경 사항 없음"}</span>
        <button className="btn btn-primary" disabled={saving || !dirtyKeys.length} onClick={saveAll}>
          {saving ? "저장 중..." : "변경사항 저장"}
        </button>
      </div>
    </section>
  );
}
