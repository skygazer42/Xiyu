import { create } from 'zustand'
import { storage, STORAGE_KEYS } from '@/lib/storage'
import type { TranscribeResponse } from '@/lib/api/types'

export interface HistoryItem {
  id: string
  filename: string
  text: string
  textAccu?: string | null
  sentences: TranscribeResponse['sentences']
  speakerTurns?: TranscribeResponse['speaker_turns']
  transcript?: string
  srt?: string | null
  rawText?: string
  timestamp: number
  duration?: number
  fileSize?: number
  options?: {
    withSpeaker?: boolean
    applyHotword?: boolean
    applyLlm?: boolean
    llmRole?: string
  }
}

// 最大存储条目数
const MAX_HISTORY_ITEMS = 100

interface HistoryState {
  items: HistoryItem[]
  searchQuery: string
  isLoaded: boolean

  load: () => void
  addItem: (item: Omit<HistoryItem, 'id' | 'timestamp'>) => void
  removeItem: (id: string) => void
  clearAll: () => void
  setSearchQuery: (query: string) => void
  getFilteredItems: () => HistoryItem[]
  getStorageSize: () => number
}

export const useHistoryStore = create<HistoryState>((set, get) => ({
  items: [],
  searchQuery: '',
  isLoaded: false,

  load: () => {
    const items = storage.get<HistoryItem[]>(STORAGE_KEYS.HISTORY, [])
    set({ items, isLoaded: true })
  },

  addItem: (item) => {
    const newItem: HistoryItem = {
      ...item,
      id: `history_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      timestamp: Date.now(),
    }

    const items = [newItem, ...get().items]

    // 限制条目数
    if (items.length > MAX_HISTORY_ITEMS) {
      items.splice(MAX_HISTORY_ITEMS)
    }

    set({ items })
    storage.set(STORAGE_KEYS.HISTORY, items)
  },

  removeItem: (id) => {
    const items = get().items.filter((item) => item.id !== id)
    set({ items })
    storage.set(STORAGE_KEYS.HISTORY, items)
  },

  clearAll: () => {
    set({ items: [] })
    storage.remove(STORAGE_KEYS.HISTORY)
  },

  setSearchQuery: (query) => {
    set({ searchQuery: query })
  },

  getFilteredItems: () => {
    const { items, searchQuery } = get()
    if (!searchQuery) return items

    const query = searchQuery.toLowerCase()
    return items.filter(
      (item) =>
        item.filename.toLowerCase().includes(query) ||
        item.text.toLowerCase().includes(query)
    )
  },

  getStorageSize: () => {
    const items = get().items
    const json = JSON.stringify(items)
    return new Blob([json]).size / (1024 * 1024) // MB
  },
}))
