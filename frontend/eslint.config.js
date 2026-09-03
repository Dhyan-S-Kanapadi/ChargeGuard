import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
export default tseslint.config({ ignores: ["dist", "playwright-report", "test-results"] }, js.configs.recommended, ...tseslint.configs.recommended, { files: ["**/*.{ts,tsx}"], plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh }, rules: { ...reactHooks.configs.recommended.rules, ...reactRefresh.configs.vite.rules, "react-refresh/only-export-components": "off" } });
