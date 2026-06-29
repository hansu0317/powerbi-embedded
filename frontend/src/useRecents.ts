import { useCallback, useState } from "react";

// 최근 연 보고서 ID 목록 (최신 우선, 최대 8개) — localStorage 유지, 백엔드 무관.
const KEY = "recent-reports";
const MAX = 8;

function load(): number[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "number") : [];
  } catch {
    return [];
  }
}

export function useRecents() {
  const [recents, setRecents] = useState<number[]>(load);

  const push = useCallback((id: number) => {
    setRecents((prev) => {
      const next = [id, ...prev.filter((x) => x !== id)].slice(0, MAX);
      localStorage.setItem(KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  return { recents, push };
}
