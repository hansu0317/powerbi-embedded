import { useCallback, useState } from "react";

// 즐겨찾기 보고서 ID 목록을 localStorage 에 저장한다 (세션을 넘어 유지, 백엔드 무관).
const KEY = "fav-reports";

function load(): number[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "number") : [];
  } catch {
    return [];
  }
}

export function useFavorites() {
  const [favs, setFavs] = useState<number[]>(load);

  const toggle = useCallback((id: number) => {
    setFavs((prev) => {
      const next = prev.includes(id)
        ? prev.filter((x) => x !== id)
        : [...prev, id];
      localStorage.setItem(KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const isFav = useCallback((id: number) => favs.includes(id), [favs]);

  return { favs, isFav, toggle };
}
