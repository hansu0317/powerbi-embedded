import React from "react";
import { createRoot } from "react-dom/client";
import { readBootstrap, type Bootstrap } from "./bootstrap";
import { getAuthToken, getAuthUser } from "./api";
import LoginPage from "./pages/LoginPage";
import ReportPage from "./pages/ReportPage";
import AdminPage from "./pages/AdminPage";

import "./theme.css";
import "./appshell.css";
import "./pages/login.css";
import "./pages/report.css";
import "./pages/admin.css";

// 서버가 __BOOTSTRAP__에 심어준 데이터는 "이 요청 순간 공유 쿠키가 가리키던 사람" 기준이다
// (일반 페이지 이동은 커스텀 헤더를 못 실어서 탭 토큰을 서버가 볼 방법이 없다 — 쿠키만 본다).
// 이 공유 쿠키를 그대로 믿으면 두 가지 문제가 생긴다:
//   1) 이 탭에서 로그인한 적이 없는데(sessionStorage에 토큰 없음) 다른 탭이 이미
//      로그인해놨다면, 새 창을 열기만 해도 "이미 로그인됨"으로 보인다.
//   2) 반대로 다른 탭에서 로그아웃하면 공유 쿠키가 사라져서, 이 탭이 자기 토큰을
//      갖고 있어도 새로고침하면 "로그인 안 됨"으로 보인다.
// 그래서 판단 기준을 공유 쿠키가 아니라 "이 탭이 sessionStorage에 들고 있는 자기 토큰"
// 하나로 통일한다:
//   - 토큰이 아예 없으면(1번 상황) 쿠키가 뭐라 하든 무조건 로그인을 요구한다.
//   - 토큰이 있는데 SSR 결과와 안 맞으면(1·2번 모두 해당) 이 탭 토큰으로 다시 물어
//     진짜 상태를 확인한다.
//   - 토큰과 SSR 결과가 일치하면(대부분의 정상 상황) 추가 네트워크 왕복 없이 그대로 쓴다.
async function resolveBootstrap(): Promise<Bootstrap> {
  const boot = readBootstrap();
  const token = getAuthToken();
  const tokenUser = getAuthUser();

  if (!token || !tokenUser) {
    if (boot.page === "login") return boot;
    window.location.href = "/login";
    return new Promise(() => {}); // 리다이렉트 진행 중이므로 렌더 안 함
  }

  if (boot.page !== "login" && tokenUser === boot.data.user.username) {
    return boot;
  }

  // 이 탭 토큰으로 실제 상태를 재확인 — boot.page가 "login"이면 어느 쪽(report/admin)인지
  // SSR만으로는 알 수 없으니 report 쪽으로 시도한다(관리자가 이 경로를 타면 report
  // 화면으로 뜨는 정도의 사소한 오차는 감수 — 다시 /admin으로 이동하면 정상 동작).
  const path = boot.page === "admin" ? "/api/admin/bootstrap" : "/api/bootstrap";
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) {
    // 탭 토큰도 만료/무효 — 로그인부터 다시.
    window.location.href = "/login";
    return new Promise(() => {}); // 리다이렉트 진행 중이므로 렌더 안 함
  }
  const data = await res.json();
  return { page: boot.page === "admin" ? "admin" : "report", data } as Bootstrap;
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
