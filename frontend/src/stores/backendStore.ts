import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { getApiBaseUrl, setApiBaseUrl } from '@/lib/api/client'

interface BackendState {
  baseUrl: string
  setBaseUrl: (baseUrl: string) => void
}

export const useBackendStore = create<BackendState>()(
  persist(
    (set) => ({
      baseUrl: getApiBaseUrl(),
      setBaseUrl: (baseUrl) => {
        setApiBaseUrl(baseUrl)
        set({ baseUrl: getApiBaseUrl() })
      },
    }),
    {
      name: 'xiyu-backend-storage',
      partialize: (state) => ({ baseUrl: state.baseUrl }),
      onRehydrateStorage: () => (state) => {
        if (state?.baseUrl !== undefined) {
          setApiBaseUrl(state.baseUrl)
        }
      },
    }
  )
)
