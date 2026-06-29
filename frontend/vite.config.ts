import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

// 빌드 결과물은 FastAPI의 StaticFiles(/static) 아래 dist/ 로 출력한다.
// 백엔드 라우트는 건드리지 않고, Jinja 템플릿이 /static/dist/app.js · app.css 를 로드한다.
// 파일명을 고정(app.*)해 템플릿에서 안정적으로 참조한다.
export default defineConfig({
  plugins: [react()],
  base: "/static/dist/",
  build: {
    outDir: resolve(__dirname, "../static/dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, "src/main.tsx"),
      output: {
        inlineDynamicImports: true,
        entryFileNames: "app.js",
        assetFileNames: "app.[ext]",
      },
    },
  },
});
