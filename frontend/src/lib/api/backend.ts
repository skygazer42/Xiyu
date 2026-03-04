import axios from 'axios'
import apiClient from './client'
import type { BackendInfoResponse, BackendTargetsResponse } from './types'

export async function getBackendInfo(): Promise<BackendInfoResponse> {
  const response = await apiClient.get<BackendInfoResponse>('/api/v1/backend')
  return response.data
}

export async function getBackendTargets(): Promise<BackendTargetsResponse> {
  const response = await apiClient.get<BackendTargetsResponse>('/api/v1/backend/targets')
  return response.data
}

function normalizeBaseUrl(baseUrl: string): string {
  const trimmed = String(baseUrl || '').trim()
  if (!trimmed) return ''
  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed
}

/**
 * Probe a backend at a specific baseUrl (port) without touching global apiClient baseURL.
 *
 * - baseUrl="" means "same origin" (relative path)
 * - baseUrl="http://host:port" probes that origin (CORS required)
 */
export async function probeBackendInfo(
  baseUrl: string,
  opts?: { timeoutMs?: number; signal?: AbortSignal }
): Promise<BackendInfoResponse> {
  const normalized = normalizeBaseUrl(baseUrl)
  const url = normalized ? `${normalized}/api/v1/backend` : '/api/v1/backend'
  const timeoutMs = opts?.timeoutMs ?? 1500

  const response = await axios.get<BackendInfoResponse>(url, {
    timeout: timeoutMs,
    signal: opts?.signal,
  })
  return response.data
}
