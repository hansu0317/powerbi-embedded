// 열린 보고서 탭 상태 — pages/ReportPage.tsx(탭 바 관리)와
// components/report/ReportViews.tsx·UploadView.tsx(탭 초기화) 양쪽이 공유한다.
// pages/ReportPage.tsx에서 2026-08-27에 분리했다(그 파일이 1600줄까지 커져서
// 화면별로 나눴다 — routes/report.py를 나눌 때와 같은 이유).
export interface OpenTab {
  id: number;
  name: string;
}

export const TABS_KEY = "open-tabs";
export const ACTIVE_KEY = "active-tab";

export function loadTabs(): OpenTab[] {
  try {
    return JSON.parse(sessionStorage.getItem(TABS_KEY) || "[]");
  } catch {
    return [];
  }
}
