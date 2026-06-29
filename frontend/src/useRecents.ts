import { useCallback, useState } from "react";
import { recordRecent } from "./api";

// 최근 본 보고서는 DB에 저장한다(기기 간 유지). 초기값은 서버 부트스트랩에서 받고,
// 보고서를 열 때 최신순으로 갱신하며 API로 기록한다.
const MAX = 8;

export function useRecents(initial: number[], csrf: string) {
  const [recents, setRecents] = useState<number[]>(initial);

  const push = useCallback(
    (id: number) => {
      setRecents((prev) => [id, ...prev.filter((x) => x !== id)].slice(0, MAX));
      recordRecent(id, csrf).catch(() => {});
    },
    [csrf],
  );

  return { recents, push };
}
