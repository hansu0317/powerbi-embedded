import { useEffect, useState } from "react";

// 컨테이너(표 영역) 높이에 맞춰 한 화면에 들어갈 행 수를 계산한다(스크롤 없이).
//
// RefObject를 그냥 받는 방식(예전 버전)은 표가 처음부터 렌더돼 있을 때만 동작했다 —
// 로그 화면처럼 데이터를 비동기로 받아온 뒤에야 표 div가 생기는 경우, 마운트 시점엔
// ref.current가 아직 null이라 측정이 빈손으로 끝나고, ref 객체 자체는 나중에 바뀌지
// 않으니(useEffect 의존성으로 못 잡음) 표가 생겨도 다시 측정할 계기가 없어 최소값(3행)에
// 영원히 갇힌다. 콜백 ref로 바꾸면 React가 DOM에 실제로 붙는 그 순간(늦게 붙어도) 콜백이
// 호출되니 이 문제가 없다 — setState 함수는 리렌더 사이에도 항상 같은 참조라 그대로
// 콜백 ref로 써도 안전하다. 호출부는 `const [rows, tableRef] = useFitRows(...)` 형태로
// 쓰고 `ref={tableRef}`를 그대로 붙이면 된다.
// SAFETY_MARGIN_PX — rowHeight 파라미터가 실제 렌더링 높이(패딩·줄간격·셀 안의
// 뱃지 등)와 완전히 일치할 거라고 믿지 않는다. 1px만 어긋나도 여러 행에 걸쳐
// 누적되면 마지막 행이 페이지네이션 바에 겹쳐 잘려 보인다("보고서 관리" 표에서
// 실제로 발생). 계산 오차가 어느 쪽에서 나든 항상 "덜 채우는" 쪽으로 안전하게
// 반올림되도록 여유 공간을 미리 빼둔다 — 행 하나 덜 보이는 것보다 겹쳐 잘리는
// 게 훨씬 나쁘다.
const SAFETY_MARGIN_PX = 10;

export function useFitRows(rowHeight = 42, headerHeight = 42, min = 3) {
  const [rows, setRows] = useState(min);
  const [node, setNode] = useState<HTMLElement | null>(null);

  useEffect(() => {
    if (!node) return;
    const calc = () => {
      const avail = node.clientHeight - headerHeight - SAFETY_MARGIN_PX;
      setRows(Math.max(min, Math.floor(avail / rowHeight)));
    };
    calc();
    const ro = new ResizeObserver(calc);
    ro.observe(node);
    window.addEventListener("resize", calc);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", calc);
    };
  }, [node, rowHeight, headerHeight, min]);

  return [rows, setNode] as const;
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
