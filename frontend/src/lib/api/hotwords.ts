import apiClient from './client'
import type { HotwordsListResponse, HotwordsUpdateResponse, TextFileResponse } from './types'

/**
 * 获取当前热词列表
 */
export async function getHotwords(): Promise<HotwordsListResponse> {
  const response = await apiClient.get<HotwordsListResponse>('/api/v1/hotwords')
  return response.data
}

/**
 * 更新热词列表（替换全部）
 */
export async function updateHotwords(hotwords: string[]): Promise<HotwordsUpdateResponse> {
  const response = await apiClient.post<HotwordsUpdateResponse>(
    '/api/v1/hotwords',
    { hotwords }
  )
  return response.data
}

/**
 * 追加热词（保留现有）
 */
export async function appendHotwords(hotwords: string[]): Promise<HotwordsUpdateResponse> {
  const response = await apiClient.post<HotwordsUpdateResponse>(
    '/api/v1/hotwords/append',
    { hotwords }
  )
  return response.data
}

/**
 * 从文件重新加载热词
 */
export async function reloadHotwords(): Promise<HotwordsUpdateResponse> {
  const response = await apiClient.post<HotwordsUpdateResponse>('/api/v1/hotwords/reload')
  return response.data
}

/**
 * 获取当前上下文热词列表（仅用于注入提示，不做强制替换）
 */
export async function getContextHotwords(): Promise<HotwordsListResponse> {
  const response = await apiClient.get<HotwordsListResponse>('/api/v1/hotwords/context')
  return response.data
}

/**
 * 更新上下文热词列表（替换全部）
 */
export async function updateContextHotwords(hotwords: string[]): Promise<HotwordsUpdateResponse> {
  const response = await apiClient.post<HotwordsUpdateResponse>(
    '/api/v1/hotwords/context',
    { hotwords }
  )
  return response.data
}

/**
 * 追加上下文热词（保留现有）
 */
export async function appendContextHotwords(hotwords: string[]): Promise<HotwordsUpdateResponse> {
  const response = await apiClient.post<HotwordsUpdateResponse>(
    '/api/v1/hotwords/context/append',
    { hotwords }
  )
  return response.data
}

/**
 * 从文件重新加载上下文热词
 */
export async function reloadContextHotwords(): Promise<HotwordsUpdateResponse> {
  const response = await apiClient.post<HotwordsUpdateResponse>('/api/v1/hotwords/context/reload')
  return response.data
}

/**
 * 获取 hot-rules.txt
 */
export async function getRulesText(): Promise<TextFileResponse> {
  const response = await apiClient.get<TextFileResponse>('/api/v1/hotwords/rules')
  return response.data
}

/**
 * 更新 hot-rules.txt（覆盖）
 */
export async function updateRulesText(text: string): Promise<TextFileResponse> {
  const response = await apiClient.post<TextFileResponse>('/api/v1/hotwords/rules', { text })
  return response.data
}

/**
 * 追加 hot-rules.txt
 */
export async function appendRulesText(text: string): Promise<TextFileResponse> {
  const response = await apiClient.post<TextFileResponse>('/api/v1/hotwords/rules/append', { text })
  return response.data
}

/**
 * 从文件重载 hot-rules.txt
 */
export async function reloadRulesText(): Promise<TextFileResponse> {
  const response = await apiClient.post<TextFileResponse>('/api/v1/hotwords/rules/reload')
  return response.data
}

/**
 * 获取 hot-rectify.txt
 */
export async function getRectifyText(): Promise<TextFileResponse> {
  const response = await apiClient.get<TextFileResponse>('/api/v1/hotwords/rectify')
  return response.data
}

/**
 * 更新 hot-rectify.txt（覆盖）
 */
export async function updateRectifyText(text: string): Promise<TextFileResponse> {
  const response = await apiClient.post<TextFileResponse>('/api/v1/hotwords/rectify', { text })
  return response.data
}

/**
 * 追加一条纠错记录到 hot-rectify.txt
 */
export async function appendRectifyRecord(wrong: string, right: string): Promise<TextFileResponse> {
  const response = await apiClient.post<TextFileResponse>('/api/v1/hotwords/rectify/append', { wrong, right })
  return response.data
}

/**
 * 从文件重载 hot-rectify.txt
 */
export async function reloadRectifyText(): Promise<TextFileResponse> {
  const response = await apiClient.post<TextFileResponse>('/api/v1/hotwords/rectify/reload')
  return response.data
}
