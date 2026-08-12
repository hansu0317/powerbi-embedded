import type { ReportFolder } from "./api";

// 서버는 폴더를 평평한 목록으로 내려준다(parent_id만 있음). 부모 바로 아래 자식이
// 오도록 순서를 잡고 들여쓰기 깊이(depth)를 계산한다 — 업로드 화면 폴더 선택기와
// 관리자 "보고서 폴더" 트리가 공유하는 로직(2026-08-12, 예전엔 각자 따로 있었음).
// 부모가 목록에 없는(권한상 안 보이는) 폴더는 최상위 취급해 목록에서 사라지지
// 않게 한다. 계층이 몇 단계든 그대로 동작한다.
export function orderFoldersAsTree(folders: ReportFolder[]): { folder: ReportFolder; depth: number }[] {
  const ids = new Set(folders.map((f) => f.id));
  const byParent = new Map<number | null, ReportFolder[]>();
  for (const f of folders) {
    const parentKey = f.parent_id != null && ids.has(f.parent_id) ? f.parent_id : null;
    const siblings = byParent.get(parentKey) ?? [];
    siblings.push(f);
    byParent.set(parentKey, siblings);
  }
  const ordered: { folder: ReportFolder; depth: number }[] = [];
  const visit = (parentId: number | null, depth: number) => {
    for (const f of byParent.get(parentId) ?? []) {
      ordered.push({ folder: f, depth });
      visit(f.id, depth + 1);
    }
  };
  visit(null, 0);
  return ordered;
}
