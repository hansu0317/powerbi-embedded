import { useEffect } from "react";
import type { LoginData } from "../bootstrap";

// 로그인은 기존 백엔드 동작을 그대로 유지한다:
//  - 성공 시 서버가 303 으로 / 리다이렉트
//  - 실패 시 서버가 login.html 을 error 와 함께 다시 렌더
// 따라서 fetch 가 아니라 일반 form POST 를 사용한다.
export default function LoginPage({ data }: { data: LoginData }) {
  useEffect(() => {
    sessionStorage.clear();
  }, []);

  return (
    <div className="login-bg">
      <div className="login-card">
        <div className="login-logo">
          <span className="brand">
            <span className="b-quali">quali</span>
            <span className="b-soft">soft</span>
            <span className="b-dot">.</span>
          </span>
          <p className="login-sub">사내 통합 BI 포털</p>
        </div>

        {data.error && <div className="login-error">{data.error}</div>}

        <form method="post" action="/login">
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
          <button type="submit" className="btn btn-primary login-submit">
            로그인
          </button>
        </form>
      </div>
    </div>
  );
}
