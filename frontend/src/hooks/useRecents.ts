import { useCallback, useState } from "react";
import { recordRecent } from "../lib/api";

// 최근 본 보고서는 DB에 저장한다(기기 간 유지). 초기값은 서버 부트스트랩에서 받고,
// 보고서를 열 때 최신순으로 갱신하며 API로 기록한다.
// max는 호출부(ReportPage.tsx)가 data.recents_limit(서버 app_config 'recents_limit')을
// 그대로 넘긴다 — 여기서 별도 상수로 하드코딩하면 관리자가 설정을 바꿔도 화면은 예전
// 숫자로 계속 잘리는 불일치가 생긴다(2026-08-12, 예전엔 여기 MAX=10이 고정값이었음).
export function useRecents(initial: number[], csrf: string, max: number) {
  const [recents, setRecents] = useState<number[]>(initial);

  const push = useCallback(
    (id: number) => {
      setRecents((prev) => [id, ...prev.filter((x) => x !== id)].slice(0, max));
      recordRecent(id, csrf).catch(() => {});
    },
    [csrf, max],
  );

  return { recents, push };
}
