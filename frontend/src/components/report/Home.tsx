// 홈(메인 랜딩) 화면 — pages/ReportPage.tsx에서 2026-08-27에 분리했다(분리 배경은
// components/report/tabState.ts 상단 주석 참고).
// CyberClinic(헬스케어 CRM) + slothui(파일매니저) 참고: 큰 숫자 통계 → 색 채운 액션
// 타일 4개 → 최근 열람 아이콘 카드 → 필터 가능한 표(파스텔 행) + 오른쪽 상세 패널.
// 벤토 카드 대신 전부 실제 데이터 기준.
import { useEffect, useMemo, useState } from "react";
import { BarChart3, LayoutDashboard, Search, Star } from "lucide-react";

import type { ReportItem } from "../../lib/bootstrap";
import { Pager, useFitRows } from "../Pager";
import { categoryColor, withAlpha } from "../../utils/categoryColor";

type HomeFilter = "all" | "fav" | "recent" | "managed" | "personal";

export function Home({
  reports,
  isAdmin,
  viewerName,
  recentIds,
  isFav,
  onOpen,
  onSearch,
  onGoAll,
  onToggleFav,
}: {
  reports: ReportItem[];
  isAdmin: boolean;
  viewerName: string;
  recentIds: number[];
  isFav: (id: number) => boolean;
  onOpen: (r: ReportItem) => void;
  onSearch: (q: string) => void;
  onGoAll: () => void;
  onToggleFav: (id: number) => void;
}) {
  const [q, setQ] = useState("");
  const [openSuggest, setOpenSuggest] = useState(false);
  const [filter, setFilter] = useState<HomeFilter>("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const byId = useMemo(() => new Map(reports.map((r) => [r.id, r])), [reports]);
  const favReports = useMemo(() => reports.filter((r) => isFav(r.id)), [reports, isFav]);
  // recentIds(useRecents의 recents)는 이미 서버 app_config.recents_limit만큼만 들어있다
  // (useRecents의 max로 매 push마다 잘림) — 여기서 또 다른 숫자로 재차 자르면 두 상한이
  // 어긋날 때 항목이 조용히 사라지는 문제가 생기므로(2026-08-12) 여기선 그대로 쓴다.
  const recentReports = useMemo(
    () =>
      recentIds
        .map((id) => byId.get(id))
        .filter((r): r is ReportItem => Boolean(r)),
    [recentIds, byId],
  );
  const suggestions = useMemo(() => {
    const k = q.trim().toLowerCase();
    if (!k) return [];
    return reports
      .filter(
        (r) =>
          r.name.toLowerCase().includes(k) ||
          (r.category || "").toLowerCase().includes(k) ||
          (r.description || "").toLowerCase().includes(k),
      )
      .slice(0, 8);
  }, [q, reports]);

  const filtered = useMemo(() => {
    switch (filter) {
      case "fav":
        return favReports;
      case "recent":
        return recentReports;
      case "managed":
        return reports.filter((r) => r.report_type !== "personal");
      case "personal":
        return reports.filter((r) => r.report_type === "personal");
      default:
        return reports;
    }
  }, [filter, reports, favReports, recentReports]);

  const detail = (selectedId && byId.get(selectedId)) || filtered[0] || null;

  // 고정 개수 대신 화면 높이에 맞춰 실제로 들어가는 행 수를 계산한다 — 스크롤이
  // 아예 안 생기는 걸 페이지네이션 하나로 보장하는 유일한 방법(고정 개수면 화면이
  // 작을 때 넘치고, 화면이 크면 남는 공간이 그냥 빈다).
  // 45 = --row-h(theme.css, .card-table와 공유하는 표준 행 높이). 이 표는 아이콘(24px)이
  // 낀 셀이 있어 실제로는 45px보다 살짝 더 자라므로 46으로 여유를 둔다 — useFitRows는
  // 과소추정(덜 채움)이 항상 더 안전하다(Pager.tsx SAFETY_MARGIN_PX 주석 참고).
  const [pageSize, tableRef] = useFitRows(46, 38, 5);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [filter, pageSize]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const curPage = Math.min(page, totalPages);
  const pageItems = filtered.slice((curPage - 1) * pageSize, curPage * pageSize);

  return (
    <main className="home home-fit">
      <div className="home-fixed-top">
      <div className="home-heading">
        <div>
          <span className="home-eyebrow">{isAdmin ? "ADMIN WORKSPACE" : "MY WORKSPACE"}</span>
          <h1>{isAdmin ? "운영 현황" : `${viewerName}님의 보고서`}</h1>
          <p>{isAdmin ? "보고서 사용 현황을 살펴보고 필요한 관리 화면으로 이동하세요." : "최근 사용한 보고서와 즐겨찾기를 한곳에서 확인하세요."}</p>
        </div>
        {isAdmin && <button className="btn btn-ghost" onClick={onGoAll}>전체 보고서 관리</button>}
      </div>
      <div className="home-toprow">
        <div className="home-stats">
          <div className="home-stat">
            <b>{reports.length}</b>전체 보고서
          </div>
          <div className="home-stat">
            <b>{favReports.length}</b>즐겨찾기
          </div>
          <div className="home-stat">
            <b>{recentReports.length}</b>최근 열람
          </div>
        </div>
        <form
          className="home-cmdbar"
          onSubmit={(e) => {
            e.preventDefault();
            onSearch(q.trim());
            setOpenSuggest(false);
          }}
        >
          <Search size={16} className="icn home-cmdbar-icon" />
          <input
            placeholder="보고서, 카테고리를 검색하세요"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOpenSuggest(true);
            }}
            onFocus={() => setOpenSuggest(true)}
            onBlur={() => setTimeout(() => setOpenSuggest(false), 120)}
          />
          <button type="submit" className="btn btn-primary">
            검색
          </button>
          {openSuggest && suggestions.length > 0 && (
            <ul className="home-suggest">
              {suggestions.map((r) => (
                <li
                  key={r.id}
                  className="home-suggest-item"
                  onMouseDown={() => {
                    onOpen(r);
                    setOpenSuggest(false);
                  }}
                >
                  <BarChart3 size={15} className="icn" />
                  <span className="home-suggest-name">{r.name}</span>
                  {r.category && <span className="home-suggest-cat">{r.category}</span>}
                </li>
              ))}
            </ul>
          )}
        </form>
      </div>

      {!isAdmin && recentReports.length > 0 && (
        <>
          <div className="home-section-label">이어서 보기</div>
          <div className="home-recent-grid">
            {recentReports.map((r) => (
              <FileCard key={r.id} report={r} selected={detail?.id === r.id} onClick={() => setSelectedId(r.id)} onOpen={onOpen} />
            ))}
          </div>
        </>
      )}

      <div className="home-filterrow">
        <FilterChip active={filter === "all"} onClick={() => setFilter("all")}>전체</FilterChip>
        <FilterChip active={filter === "fav"} onClick={() => setFilter("fav")}>★ 즐겨찾기</FilterChip>
        <FilterChip active={filter === "recent"} onClick={() => setFilter("recent")}>최근 열람</FilterChip>
        <FilterChip active={filter === "managed"} onClick={() => setFilter("managed")}>공용</FilterChip>
        <FilterChip active={filter === "personal"} onClick={() => setFilter("personal")}>개인</FilterChip>
      </div>
      </div>

      <div className="home-main">
        <div className="home-tablewrap card-table" ref={tableRef}>
          <table className="home-table">
            {/* 네 컬럼 폭 합이 정확히 100%여야 한다 — px 고정값(예: 36px)을 하나라도 섞으면
                table-layout:fixed 아래서 표 전체 폭이 "100% + 36px"가 되어 컨테이너보다
                넓어지고, home-tablewrap의 overflow:hidden이 그 초과분을 조용히 잘라낸다.
                이때 셀 안 텍스트 길이에 따라 잘리는 지점이 달라져 줄마다 오른쪽 경계가
                들쭉날쭉해 보인다("줄이 어긋나 보임") — 그래서 전부 %로만 맞춘다. */}
            <colgroup>
              <col style={{ width: "4%" }} />
              <col style={{ width: "42%" }} />
              <col style={{ width: "29%" }} />
              <col style={{ width: "25%" }} />
            </colgroup>
            <thead>
              <tr>
                <th></th>
                <th>이름</th>
                <th>카테고리</th>
                <th>유형</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((r) => (
                <tr
                  key={r.id}
                  className={`home-table-row${detail?.id === r.id ? " sel" : ""}`}
                  onClick={() => setSelectedId(r.id)}
                >
                  <td>
                    <button
                      type="button"
                      className={`fav-star${isFav(r.id) ? " on" : ""}`}
                      title={isFav(r.id) ? "즐겨찾기 해제" : "즐겨찾기 추가"}
                      onClick={(e) => {
                        e.stopPropagation();
                        onToggleFav(r.id);
                      }}
                    >
                      <Star size={14} className="icn" fill={isFav(r.id) ? "currentColor" : "none"} />
                    </button>
                  </td>
                  <td>
                    <div className="home-table-name">
                      <span className="home-mini-ic" style={{ background: categoryColor(r.category) }}>
                        {r.report_type === "dashboard" ? (
                          <LayoutDashboard size={12} />
                        ) : (
                          <BarChart3 size={12} />
                        )}
                      </span>
                      <span className="home-table-name-text" title={r.name}>{r.name}</span>
                    </div>
                  </td>
                  <td>
                    <span
                      className="home-cat-pill"
                      style={{
                        background: withAlpha(categoryColor(r.category), "22"),
                        color: categoryColor(r.category),
                      }}
                    >
                      {r.category || "미분류"}
                    </span>
                  </td>
                  <td className="home-table-type">
                    {r.report_type === "personal" ? `개인 · ${r.owner_username || "-"}` : "공용"}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={4} className="home-table-empty">
                    표시할 보고서가 없습니다
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {detail && (
          <div className="home-detail">
            <span
              className="home-detail-icon"
              style={{ background: categoryColor(detail.category) }}
            >
              {detail.report_type === "dashboard" ? (
                <LayoutDashboard size={20} />
              ) : (
                <BarChart3 size={20} />
              )}
            </span>
            <div className="home-detail-name">{detail.name}</div>
            <div className="home-detail-cat">
              {detail.category || "미분류"} ·{" "}
              {detail.report_type === "personal" ? `개인 · ${detail.owner_username || "-"}` : "공용"}
            </div>
            <div className="home-detail-section-title">보고서 정보</div>
            <div className="home-detail-desc">{detail.description || "등록된 소개가 없습니다. 관리자는 보고서 설명을 추가해 사용자에게 변경 내용이나 활용 방법을 안내할 수 있습니다."}</div>
            <div className="home-detail-actions">
              <button className="btn btn-ghost" onClick={() => onToggleFav(detail.id)}>
                <Star size={14} className="icn" fill={isFav(detail.id) ? "currentColor" : "none"} />{" "}
                {isFav(detail.id) ? "즐겨찾기 해제" : "즐겨찾기"}
              </button>
              <button className="btn btn-primary" onClick={() => onOpen(detail)}>
                보고서 열기
              </button>
            </div>
          </div>
        )}
      </div>
      <Pager page={curPage} totalPages={totalPages} total={filtered.length} onPage={setPage} />
    </main>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button type="button" className={`home-chip${active ? " on" : ""}`} onClick={onClick}>
      {children}
    </button>
  );
}

function FileCard({
  report,
  selected,
  onClick,
  onOpen,
}: {
  report: ReportItem;
  selected: boolean;
  onClick: () => void;
  onOpen: (r: ReportItem) => void;
}) {
  return (
    <div
      className={`home-filecard${selected ? " sel" : ""}`}
      onClick={onClick}
      onDoubleClick={() => onOpen(report)}
      title={`${report.name} — 더블클릭하면 바로 열립니다`}
    >
      <span className="home-filecard-icon" style={{ background: categoryColor(report.category) }}>
        {report.report_type === "dashboard" ? <LayoutDashboard size={18} /> : <BarChart3 size={18} />}
      </span>
      <div className="home-filecard-name">{report.name}</div>
      <div className="home-filecard-cat">{report.category || "미분류"}</div>
    </div>
  );
}
