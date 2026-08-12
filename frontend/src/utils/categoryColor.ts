// 카테고리마다 다른 색을 자동 배정하던 걸 없앴다 — 색이 너무 다채로워서 오히려
// 아무것도 눈에 안 띈다는 피드백 반영. 화면 전체를 흰색/잉크(검정)/연한 파란색(accent)
// 세 톤으로만 쓴다. 이 함수는 그대로 남겨서(호출부를 다 안 고쳐도 되게) 값만
// 항상 accent 하나로 고정한다 — 나중에 다시 구분색이 필요해지면 여기만 바꾸면 됨.
export function categoryColor(_category: string | null | undefined): string {
  return "#3452E8"; // theme.css의 --sage와 같은 값
}

/** hex → "hex + alpha" 연한 배경톤 (pill·아이콘 배경 등에 사용) */
export function withAlpha(hex: string, alphaHex: string): string {
  return hex + alphaHex;
}
