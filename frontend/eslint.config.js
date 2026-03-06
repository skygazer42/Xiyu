import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    rules: {
      // The default Vite fast-refresh rule is too strict for shadcn-style UI modules
      // (they often export variants/helpers alongside components). Keep lint focused
      // on correctness instead of forcing large refactors.
      'react-refresh/only-export-components': 'off',

      // This is a performance hint; we keep it as a warning so it doesn't block CI/lint.
      'react-hooks/set-state-in-effect': 'warn',
    },
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
])
