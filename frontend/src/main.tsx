import React from "react";
import { createRoot } from "react-dom/client";
import { readBootstrap, type Bootstrap } from "./bootstrap";
import { getAuthToken, getAuthUser } from "./api";
import LoginPage from "./pages/LoginPage";
import ReportPage from "./pages/ReportPage";
import AdminPage from "./pages/AdminPage";

import "./theme.css";
import "./pages/login.css";
import "./pages/report.css";
import "./pages/admin.css";

// 서버가 __BOOTSTRAP__에 심어준 데이터는 "이 요청 순간 쿠키가 가리키던 사람" 기준이다.
// 같은 브라우저의 다른 탭에서 다른 계정으로 로그인하면 그 쿠키가 덮어써지므로,
// 이 탭을 새로고침하면 SSR이 엉뚱한 사람 데이터를 내려줄 수 있다. 이 탭이 sessionStorage에
// 들고 있는 진짜 주인(auth-user)과 SSR이 준 user.username이 다르면, 탭 토큰으로 다시
// 받아온 데이터로 뒤엎는다. 토큰이 없거나 일치하면(대부분의 경우) 아무 것도 안 하고
// SSR 결과를 그대로 쓴다 — 추가 네트워크 왕복이 없다.
async function resolveBootstrap(): Promise<Bootstrap> {
  const boot = readBootstrap();
  if (boot.page === "login") return boot;

  const token = getAuthToken();
  const tokenUser = getAuthUser();
  if (!token || !tokenUser || tokenUser === boot.data.user.username) {
    return boot;
  }

  const path = boot.page === "admin" ? "/api/admin/bootstrap" : "/api/bootstrap";
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) {
    // 탭 토큰도 만료/무효 — 로그인부터 다시.
    window.location.href = "/login";
    return new Promise(() => {}); // 리다이렉트 진행 중이므로 렌더 안 함
  }
  const data = await res.json();
  return { page: boot.page, data } as Bootstrap;
}

const container = document.getElementById("root");
if (!container) throw new Error("#root 엘리먼트가 없습니다.");

function App({ boot }: { boot: Bootstrap }) {
  switch (boot.page) {
    case "login":
      return <LoginPage data={boot.data} />;
    case "report":
      return <ReportPage data={boot.data} />;
    case "admin":
      return <AdminPage data={boot.data} />;
  }
}

resolveBootstrap().then((boot) => {
  createRoot(container).render(
    <React.StrictMode>
      <App boot={boot} />
    </React.StrictMode>,
  );
});
