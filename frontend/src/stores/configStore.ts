import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface ConfigState {
  // 服务器配置缓存
  serverConfig: Record<string, unknown>
  setServerConfig: (config: Record<string, unknown>) => void

  // 可变配置键列表
  mutableKeys: string[]
  setMutableKeys: (keys: string[]) => void

  // 本地暂存的配置修改
  pendingChanges: Record<string, unknown>
  setPendingChange: (key: string, value: unknown) => void
  clearPendingChanges: () => void

  // 配置加载状态
  isConfigLoading: boolean
  setConfigLoading: (loading: boolean) => void
}

export const useConfigStore = create<ConfigState>()(
  persist(
    (set) => ({
      serverConfig: {},
      setServerConfig: (serverConfig) => set({ serverConfig }),

      mutableKeys: [],
      setMutableKeys: (mutableKeys) => set({ mutableKeys }),

      pendingChanges: {},
      setPendingChange: (key, value) =>
        set((state) => ({
          pendingChanges: { ...state.pendingChanges, [key]: value },
        })),
      clearPendingChanges: () => set({ pendingChanges: {} }),

      isConfigLoading: false,
      setConfigLoading: (isConfigLoading) => set({ isConfigLoading }),
    }),
    {
      name: 'xiyu-config-storage',
      partialize: () => ({}), // 不持久化服务器配置
    }
  )
)
