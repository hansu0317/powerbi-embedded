import { useEffect, useState } from "react";
import type { LoginData } from "../lib/bootstrap";
import { setAuthToken } from "../lib/api";

// /auth/callback이 로그인 실패 시 돌려보내는 사유 코드 → 화면 문구.
// routes/auth.py의 auth_callback()이 붙이는 코드와 1:1로 맞춰야 한다.
const SSO_ERROR_MESSAGES: Record<string, string> = {
  flow_missing: "로그인 세션이 만료됐습니다. 다시 시도해 주세요.",
  denied: "Microsoft 로그인이 취소되었거나 실패했습니다.",
  no_email: "Microsoft 계정에서 이메일 정보를 받지 못했습니다.",
  nomatch: "이 Microsoft 계정과 연결된 사용자를 찾을 수 없습니다. 관리자에게 문의하세요.",
  inactive: "계정이 비활성화되었습니다. 관리자에게 문의하세요.",
};

type LoginTab = "sso" | "password";

// 로그인은 fetch로 처리한다 — 성공 시 서버가 발급하는 탭 전용 토큰(token)을
// sessionStorage에 저장해야 하는데, 일반 form POST(전체 페이지 이동)는 그 응답을
// JS가 가로챌 기회가 없어서 토큰을 받을 수 없다. 그래서 fetch 기반으로 바꿨다
// (탭마다 독립된 로그인을 지원하기 위한 변경 — deps.py의 issue_tab_token 참고).
// MS 계정 로그인(SSO)은 반대로 반드시 전체 페이지 이동이어야 한다(Microsoft 로그인
// 화면으로 나갔다 와야 하므로) — 그래서 버튼은 fetch가 아니라 그냥 <a href>다.
export default function LoginPage({ data }: { data: LoginData }) {
  const [error, setError] = useState<string | null>(
    data.error || SSO_ERROR_MESSAGES[new URLSearchParams(window.location.search).get("sso_error") || ""] || null,
  );
  const [busy, setBusy] = useState(false);
  // 두 로그인 방법을 위아래로 같이 늘어놓으면 "둘 다 해야 하나?"로 보인다(2026-08-20
  // 사용자 피드백) — 탭으로 완전히 분리한다. SSO가 켜져 있으면 회사가 밀고 있는
  // 방법을 기본 탭으로 먼저 보여준다.
  const [tab, setTab] = useState<LoginTab>(data.sso_enabled ? "sso" : "password");

  useEffect(() => {
    sessionStorage.clear();
  }, []);

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      const res = await fetch("/login", {
        method: "POST",
        body: new URLSearchParams(form as any),
      });
      const j = await res.json().catch(() => ({ ok: false, error: "로그인 응답을 읽을 수 없습니다." }));
      if (!res.ok || !j.ok) {
        setError(j.error || "로그인에 실패했습니다.");
        setBusy(false);
        return;
      }
      setAuthToken(j.token, j.username);
      window.location.href = "/";
    } catch {
      setError("네트워크 오류로 로그인하지 못했습니다.");
      setBusy(false);
    }
  };

  return (
    <div className="login-bg">
      <div className="login-card">
        <div className="login-logo">
          <span className="brand">
            <span className="b-quali">quali</span>
            <span className="b-soft">soft</span>
          </span>
          <p className="login-sub">사내 통합 BI 포털</p>
        </div>

        {error && <div className="login-error">{error}</div>}

        {data.sso_enabled && (
          <div className="login-tabs">
            <button
              type="button"
              className={`login-tab${tab === "sso" ? " active" : ""}`}
              onClick={() => setTab("sso")}
            >
              Microsoft
            </button>
            <button
              type="button"
              className={`login-tab${tab === "password" ? " active" : ""}`}
              onClick={() => setTab("password")}
            >
              아이디/비밀번호
            </button>
          </div>
        )}

        {data.sso_enabled && tab === "sso" ? (
          <div className="login-sso-panel">
            <p className="login-sso-desc">회사 Microsoft 계정으로 로그인합니다.</p>
            <a href="/login/sso" className="btn btn-primary login-submit login-sso-btn">
              Microsoft 계정으로 로그인
            </a>
          </div>
        ) : (
          <form onSubmit={submit}>
            <input type="hidden" name="csrf" value={data.csrf_token} />
            <label htmlFor="username">아이디</label>
            <input
              id="username"
              name="username"
              type="text"
              placeholder="사용자 아이디"
              required
              autoFocus={!data.sso_enabled}
            />
            <label htmlFor="password">비밀번호</label>
            <input
              id="password"
              name="password"
              type="password"
              placeholder="비밀번호"
              required
            />
            <button type="submit" className="btn btn-primary login-submit" disabled={busy}>
              {busy ? "로그인 중..." : "로그인"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
