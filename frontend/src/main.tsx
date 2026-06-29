import React from "react";
import { createRoot } from "react-dom/client";
import { readBootstrap } from "./bootstrap";
import LoginPage from "./pages/LoginPage";
import ReportPage from "./pages/ReportPage";
import AdminPage from "./pages/AdminPage";

import "./theme.css";
import "./pages/login.css";
import "./pages/report.css";
import "./pages/admin.css";

const boot = readBootstrap();
const container = document.getElementById("root");
if (!container) throw new Error("#root 엘리먼트가 없습니다.");

function App() {
  switch (boot.page) {
    case "login":
      return <LoginPage data={boot.data} />;
    case "report":
      return <ReportPage data={boot.data} />;
    case "admin":
      return <AdminPage data={boot.data} />;
  }
}

createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
