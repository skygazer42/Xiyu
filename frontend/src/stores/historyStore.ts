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
const DEFAULT_TURN_MERGE_GAP_MS = 800

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

function pad2(n: number): string {
  return String(Math.max(0, Math.floor(n))).padStart(2, '0')
}

function formatClock(ms: number): string {
  const totalSeconds = Math.floor(Math.max(0, ms) / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  const hours = Math.floor(minutes / 60)

  if (hours > 0) {
    return `${pad2(hours)}:${pad2(minutes % 60)}:${pad2(seconds)}`
  }
  return `${pad2(minutes)}:${pad2(seconds)}`
}

function buildSpeakerTurns(
  sentences: TranscribeResponse['sentences'],
  opts: { gapMs?: number; minChars?: number } = {}
): NonNullable<TranscribeResponse['speaker_turns']> | undefined {
  if (!Array.isArray(sentences) || sentences.length === 0) return undefined

  const hasSpeakerInfo = sentences.some((s) => s.speaker_id !== undefined || !!s.speaker)
  if (!hasSpeakerInfo) return undefined

  const gapMs = Math.max(0, Math.floor(opts.gapMs ?? DEFAULT_TURN_MERGE_GAP_MS))
  const minChars = Math.max(0, Math.floor(opts.minChars ?? 1))

  const turns: Array<NonNullable<TranscribeResponse['speaker_turns']>[number]> = []
  let current: NonNullable<TranscribeResponse['speaker_turns']>[number] | null = null

  const flush = () => {
    if (!current) return
    if (String(current.text || '').trim().length >= minChars) {
      turns.push(current)
    }
    current = null
  }

  for (const sent of sentences) {
    const speaker = (sent.speaker || '未知').trim() || '未知'
    const speaker_id = typeof sent.speaker_id === 'number' ? sent.speaker_id : -1

    const start = Number.isFinite(sent.start) ? Math.floor(sent.start) : 0
    const end = Number.isFinite(sent.end) ? Math.floor(sent.end) : start
    const text = String(sent.text || '')

    if (!current) {
      current = {
        speaker,
        speaker_id,
        start,
        end,
        text,
        sentence_count: 1,
      }
      continue
    }

    const sameSpeaker = current.speaker_id === speaker_id && current.speaker === speaker
    const gap = start - (Number.isFinite(current.end) ? current.end : 0)
    const canMerge = sameSpeaker && gap <= gapMs

    if (canMerge) {
      current.end = Math.max(current.end, end)
      current.text = `${current.text}${text}`
      current.sentence_count = (current.sentence_count || 0) + 1
      continue
    }

    flush()
    current = {
      speaker,
      speaker_id,
      start,
      end,
      text,
      sentence_count: 1,
    }
  }

  flush()
  return turns.length > 0 ? turns : undefined
}

function formatTranscriptFromTurns(turns: NonNullable<TranscribeResponse['speaker_turns']>): string {
  return turns
    .map((t) => {
      const speaker = String(t.speaker || '未知').trim() || '未知'
      const text = String(t.text || '')
      const start = Number.isFinite(t.start) ? Math.floor(t.start) : 0
      const end = Number.isFinite(t.end) ? Math.floor(t.end) : 0
      return `[${formatClock(start)} - ${formatClock(end)}] ${speaker}: ${text}`
    })
    .join('\n')
}

function upgradeHistoryItem(item: HistoryItem): HistoryItem {
  const next: HistoryItem = { ...item }

  if (!next.speakerTurns) {
    const derived = buildSpeakerTurns(next.sentences)
    if (derived) {
      next.speakerTurns = derived
    }
  }

  if (!next.transcript && next.speakerTurns && Array.isArray(next.speakerTurns) && next.speakerTurns.length > 0) {
    next.transcript = formatTranscriptFromTurns(next.speakerTurns)
  }

  return next
}

export const useHistoryStore = create<HistoryState>((set, get) => ({
  items: [],
  searchQuery: '',
  isLoaded: false,

  load: () => {
    const items = storage.get<HistoryItem[]>(STORAGE_KEYS.HISTORY, []).map(upgradeHistoryItem)
    set({ items, isLoaded: true })
  },

  addItem: (item) => {
    const newItem: HistoryItem = upgradeHistoryItem({
      ...item,
      id: `history_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      timestamp: Date.now(),
    })

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
    const items = storage.get<HistoryItem[]>(STORAGE_KEYS.HISTORY, [])
    const json = JSON.stringify(items)
    return new Blob([json]).size / (1024 * 1024) // MB
  },
}))
