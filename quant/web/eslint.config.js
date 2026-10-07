import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

// Paper Tape discipline (Ship plan, Lane P2 checklist item 3): every number goes through
// lib/format.ts, and no inline style= except CSS custom properties ({ "--pos": "40%" }).
const NO_TOFIXED = [
  { selector: "CallExpression[callee.property.name='toFixed']", message: "Format numbers with lib/format.ts, not .toFixed." },
];
const NO_INLINE_STYLE = [
  {
    selector: "JSXAttribute[name.name='style'] > JSXExpressionContainer > ObjectExpression > Property:not([key.type='Literal'][key.value=/^--/])",
    message: "No inline style: use a class, or pass a CSS custom property ({ \"--x\": v }).",
  },
  { selector: "JSXAttribute[name.name='style'] > JSXExpressionContainer > ObjectExpression > SpreadElement", message: "No inline style: use a class, or pass a CSS custom property ({ \"--x\": v })." },
  {
    selector: "JSXAttribute[name.name='style'] > JSXExpressionContainer > :not(ObjectExpression, TSAsExpression)",
    message: "No inline style: use a class, or pass a CSS custom property ({ \"--x\": v }).",
  },
  {
    selector: "JSXAttribute[name.name='style'] > JSXExpressionContainer > TSAsExpression > :not(ObjectExpression, TSTypeReference, TSAsExpression)",
    message: "No inline style: use a class, or pass a CSS custom property ({ \"--x\": v }).",
  },
  {
    selector: "JSXAttribute[name.name='style'] > JSXExpressionContainer > TSAsExpression > ObjectExpression > Property:not([key.type='Literal'][key.value=/^--/])",
    message: "No inline style: use a class, or pass a CSS custom property ({ \"--x\": v }).",
  },
];

// Files not yet migrated by Lane P2. Each P2 task deletes its pages from these lists; a file
// not listed (every new file included) gets the full rule. The Flight Deck is Lane F's.
const LEGACY_INLINE_STYLE = [
  "src/App.tsx",
  "src/charts/UPlot.tsx",
  "src/components/Chart.tsx",
  "src/components/Controls.tsx",
  "src/components/DataTable.tsx",
  "src/components/Icon.tsx",
  "src/components/InfoTip.tsx",
  "src/components/ParamForm.tsx",
  "src/components/PortfolioBuilder.tsx",
  "src/components/StatTile.tsx",
  "src/components/States.tsx",
  "src/components/TickerChips.tsx",
  "src/pages/company/DcfTab.tsx",
  "src/pages/company/shared.tsx",
  "src/pages/engine/Live.tsx",
  "src/pages/macro/ExplorerTab.tsx",
  "src/pages/macro/PolicyTab.tsx",
  "src/pages/macro/RecessionTab.tsx",
  "src/pages/macro/RegimesTab.tsx",
  "src/pages/macro/shared.tsx",
  "src/pages/research/BacktestTab.tsx",
  "src/pages/research/Setup.tsx",
  "src/pages/research/SweepTab.tsx",
];
const LEGACY_TOFIXED = [
  "src/charts/scales.ts",
  "src/components/Controls.tsx",
  "src/components/ParamForm.tsx",
  "src/components/Sparkline.tsx",
  "src/pages/macro/BondsTab.tsx",
  "src/pages/research/BacktestTab.tsx",
  "src/pages/research/CostsTab.tsx",
  "src/pages/research/Guardrails.tsx",
  "src/pages/research/SweepTab.tsx",
  "src/pages/research/config.ts",
  "src/pages/research/shared.tsx",
  "src/pages/macro/shared.tsx",
];
const DECK = ["src/pages/deck/**", "src/pages/Deck.tsx"];

const only = (files, selectors) => ({ files, rules: { "no-restricted-syntax": selectors.length ? ["error", ...selectors] : "off" } });
const legacy = [...new Set([...LEGACY_INLINE_STYLE, ...LEGACY_TOFIXED])].map((f) =>
  only([f], [...(LEGACY_TOFIXED.includes(f) ? [] : NO_TOFIXED), ...(LEGACY_INLINE_STYLE.includes(f) ? [] : NO_INLINE_STYLE)]),
);

export default tseslint.config(
  { ignores: ["dist", "node_modules", "scripts"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "no-restricted-syntax": ["error", ...NO_TOFIXED, ...NO_INLINE_STYLE],
    },
  },
  only(["src/lib/format.ts"], NO_INLINE_STYLE),
  ...legacy,
  only(DECK, []),
);
