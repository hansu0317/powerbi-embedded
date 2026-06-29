import { useEffect, useState, RefObject } from "react";

// 컨테이너(표 영역) 높이에 맞춰 한 화면에 들어갈 행 수를 계산한다(스크롤 없이).
export function useFitRows(
  ref: RefObject<HTMLElement>,
  rowHeight = 42,
  headerHeight = 42,
  min = 3,
) {
  const [rows, setRows] = useState(min);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const calc = () => {
      const avail = el.clientHeight - headerHeight;
      setRows(Math.max(min, Math.floor(avail / rowHeight)));
    };
    calc();
    const ro = new ResizeObserver(calc);
    ro.observe(el);
    window.addEventListener("resize", calc);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", calc);
    };
  }, [ref, rowHeight, headerHeight, min]);
  return rows;
}

// 고정 페이지 크기로 목록을 페이지 단위로 자른다.
export function usePaged<T>(items: T[], pageSize: number) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const cur = Math.min(page, totalPages);
  const pageItems = items.slice((cur - 1) * pageSize, cur * pageSize);
  return { pageItems, page: cur, totalPages, total: items.length, setPage };
}

// 공용 페이지네이션 (Total N + ‹ 1 2 3 ›)
export function Pager({
  page,
  totalPages,
  total,
  onPage,
}: {
  page: number;
  totalPages: number;
  total: number;
  onPage: (n: number) => void;
}) {
  const nums = Array.from({ length: totalPages }, (_, i) => i + 1);
  return (
    <div className="rp-pager">
      <span className="rp-pager-total">Total {total} records</span>
      {totalPages > 1 && (
        <div className="rp-pager-nav">
          <button
            className="rp-pager-btn"
            disabled={page === 1}
            onClick={() => onPage(page - 1)}
          >
            ‹
          </button>
          {nums.map((n) => (
            <button
              key={n}
              className={`rp-pager-btn${n === page ? " active" : ""}`}
              onClick={() => onPage(n)}
            >
              {n}
            </button>
          ))}
          <button
            className="rp-pager-btn"
            disabled={page === totalPages}
            onClick={() => onPage(page + 1)}
          >
            ›
          </button>
        </div>
      )}
    </div>
  );
}
