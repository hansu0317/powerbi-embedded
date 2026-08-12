import { useEffect, useState } from "react";
import type { LoginData } from "../lib/bootstrap";
import { setAuthToken } from "../lib/api";

// 로그인은 fetch로 처리한다 — 성공 시 서버가 발급하는 탭 전용 토큰(token)을
// sessionStorage에 저장해야 하는데, 일반 form POST(전체 페이지 이동)는 그 응답을
// JS가 가로챌 기회가 없어서 토큰을 받을 수 없다. 그래서 fetch 기반으로 바꿨다
// (탭마다 독립된 로그인을 지원하기 위한 변경 — deps.py의 issue_tab_token 참고).
export default function LoginPage({ data }: { data: LoginData }) {
  const [error, setError] = useState<string | null>(data.error);
  const [busy, setBusy] = useState(false);

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

        <form onSubmit={submit}>
          <input type="hidden" name="csrf" value={data.csrf_token} />
          <label htmlFor="username">아이디</label>
          <input
            id="username"
            name="username"
            type="text"
            placeholder="사용자 아이디"
            required
            autoFocus
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
      </div>
    </div>
  );
}
