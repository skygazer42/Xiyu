/**
 * 本地存储工具
 * Plan 8: 配置预设与模板
 */

const STORAGE_PREFIX = 'xiyu_'

// 存储键
export const STORAGE_KEYS = {
  CONFIG_PRESETS: 'config_presets',
  ACTIVE_PRESET: 'active_preset',
  THEME: 'theme',
  HISTORY: 'history',
  USER_PREFERENCES: 'user_preferences',
} as const

type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS]

// 安全的 JSON 解析
function safeJsonParse<T>(value: string | null, defaultValue: T): T {
  if (!value) return defaultValue
  try {
    return JSON.parse(value) as T
  } catch {
    return defaultValue
  }
}

// 存储工具
export const storage = {
  get<T>(key: StorageKey, defaultValue: T): T {
    if (typeof window === 'undefined') return defaultValue
    const value = localStorage.getItem(`${STORAGE_PREFIX}${key}`)
    return safeJsonParse(value, defaultValue)
  },

  set<T>(key: StorageKey, value: T): void {
    if (typeof window === 'undefined') return
    localStorage.setItem(`${STORAGE_PREFIX}${key}`, JSON.stringify(value))
  },

  remove(key: StorageKey): void {
    if (typeof window === 'undefined') return
    localStorage.removeItem(`${STORAGE_PREFIX}${key}`)
  },

  clear(): void {
    if (typeof window === 'undefined') return
    Object.keys(localStorage)
      .filter((key) => key.startsWith(STORAGE_PREFIX))
      .forEach((key) => localStorage.removeItem(key))
  },
}

// 配置预设类型
export interface ConfigPreset {
  id: string
  name: string
  description?: string
  icon?: string
  isBuiltin?: boolean
  createdAt: number
  config: {
    with_speaker?: boolean
    apply_hotword?: boolean
    apply_llm?: boolean
    llm_role?: string
    hotwords?: string
    [key: string]: unknown
  }
}

// 内置预设
export const BUILTIN_PRESETS: ConfigPreset[] = [
  {
    id: 'meeting',
    name: '会议记录',
    description: '适合会议录音，开启说话人识别',
    icon: '📋',
    isBuiltin: true,
    createdAt: 0,
    config: {
      with_speaker: true,
      apply_hotword: true,
      apply_llm: false,
    },
  },
  {
    id: 'interview',
    name: '采访模式',
    description: '适合采访录音，开启说话人识别和 LLM 润色',
    icon: '🎤',
    isBuiltin: true,
    createdAt: 0,
    config: {
      with_speaker: true,
      apply_hotword: true,
      apply_llm: true,
      llm_role: 'default',
    },
  },
  {
    id: 'lecture',
    name: '讲座/演讲',
    description: '适合讲座、演讲等单人场景',
    icon: '🎓',
    isBuiltin: true,
    createdAt: 0,
    config: {
      with_speaker: false,
      apply_hotword: true,
      apply_llm: true,
      llm_role: 'default',
    },
  },
  {
    id: 'code',
    name: '代码讲解',
    description: '适合编程教程，优化代码术语识别',
    icon: '💻',
    isBuiltin: true,
    createdAt: 0,
    config: {
      with_speaker: false,
      apply_hotword: true,
      apply_llm: true,
      llm_role: 'code',
    },
  },
  {
    id: 'quick',
    name: '快速转写',
    description: '关闭所有增强功能，最快速度完成转写',
    icon: '⚡',
    isBuiltin: true,
    createdAt: 0,
    config: {
      with_speaker: false,
      apply_hotword: false,
      apply_llm: false,
    },
  },
]

// 预设管理
export const presetManager = {
  getAll(): ConfigPreset[] {
    const userPresets = storage.get<ConfigPreset[]>(STORAGE_KEYS.CONFIG_PRESETS, [])
    return [...BUILTIN_PRESETS, ...userPresets]
  },

  getUserPresets(): ConfigPreset[] {
    return storage.get<ConfigPreset[]>(STORAGE_KEYS.CONFIG_PRESETS, [])
  },

  getById(id: string): ConfigPreset | undefined {
    return this.getAll().find((p) => p.id === id)
  },

  save(preset: Omit<ConfigPreset, 'id' | 'createdAt'>): ConfigPreset {
    const userPresets = this.getUserPresets()
    const newPreset: ConfigPreset = {
      ...preset,
      id: `custom_${Date.now()}`,
      createdAt: Date.now(),
      isBuiltin: false,
    }
    userPresets.push(newPreset)
    storage.set(STORAGE_KEYS.CONFIG_PRESETS, userPresets)
    return newPreset
  },

  update(id: string, updates: Partial<ConfigPreset>): boolean {
    const userPresets = this.getUserPresets()
    const index = userPresets.findIndex((p) => p.id === id)
    if (index === -1) return false

    userPresets[index] = { ...userPresets[index], ...updates }
    storage.set(STORAGE_KEYS.CONFIG_PRESETS, userPresets)
    return true
  },

  delete(id: string): boolean {
    const userPresets = this.getUserPresets()
    const filtered = userPresets.filter((p) => p.id !== id)
    if (filtered.length === userPresets.length) return false

    storage.set(STORAGE_KEYS.CONFIG_PRESETS, filtered)
    return true
  },

  getActive(): string | null {
    return storage.get<string | null>(STORAGE_KEYS.ACTIVE_PRESET, null)
  },

  setActive(id: string | null): void {
    if (id) {
      storage.set(STORAGE_KEYS.ACTIVE_PRESET, id)
    } else {
      storage.remove(STORAGE_KEYS.ACTIVE_PRESET)
    }
  },

  exportPresets(): string {
    const presets = this.getUserPresets()
    return JSON.stringify(presets, null, 2)
  },

  importPresets(json: string): { success: number; failed: number } {
    let success = 0
    let failed = 0

    try {
      const presets = JSON.parse(json) as ConfigPreset[]
      const userPresets = this.getUserPresets()

      for (const preset of presets) {
        if (preset.name && preset.config) {
          userPresets.push({
            ...preset,
            id: `imported_${Date.now()}_${success}`,
            createdAt: Date.now(),
            isBuiltin: false,
          })
          success++
        } else {
          failed++
        }
      }

      storage.set(STORAGE_KEYS.CONFIG_PRESETS, userPresets)
    } catch {
      failed++
    }

    return { success, failed }
  },
}
