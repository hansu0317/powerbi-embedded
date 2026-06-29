import { useCallback, useState } from "react";
import { setFavorite } from "./api";

// 즐겨찾기는 DB에 저장한다(기기 간 유지). 초기값은 서버 부트스트랩에서 받고,
// 토글 시 낙관적으로 화면을 갱신한 뒤 API를 호출한다(실패하면 되돌림).
export function useFavorites(initial: number[], csrf: string) {
  const [favs, setFavs] = useState<number[]>(initial);

  const toggle = useCallback(
    (id: number) => {
      const on = !favs.includes(id);
      setFavs((prev) =>
        on ? [...prev, id] : prev.filter((x) => x !== id),
      );
      setFavorite(id, on, csrf).catch(() => {
        // 실패 시 롤백
        setFavs((prev) => (on ? prev.filter((x) => x !== id) : [...prev, id]));
      });
    },
    [favs, csrf],
  );

  const isFav = useCallback((id: number) => favs.includes(id), [favs]);

  return { favs, isFav, toggle };
}
