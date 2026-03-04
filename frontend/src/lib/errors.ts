/**
 * Xiyu 错误处理系统
 * Plan 16: 错误处理统一化
 */

// 错误类型
export const ErrorType = {
  NETWORK: 'NETWORK',
  VALIDATION: 'VALIDATION',
  SERVER: 'SERVER',
  TIMEOUT: 'TIMEOUT',
  UNKNOWN: 'UNKNOWN',
  FILE_FORMAT: 'FILE_FORMAT',
  FILE_SIZE: 'FILE_SIZE',
  AUTH: 'AUTH',
} as const

export type ErrorType = (typeof ErrorType)[keyof typeof ErrorType]

// 错误代码映射
export const ERROR_CODES: Record<number, { type: ErrorType; message: string }> = {
  400: { type: ErrorType.VALIDATION, message: '请求参数无效' },
  401: { type: ErrorType.AUTH, message: '未授权，请重新登录' },
  403: { type: ErrorType.AUTH, message: '无权限执行此操作' },
  404: { type: ErrorType.SERVER, message: '请求的资源不存在' },
  408: { type: ErrorType.TIMEOUT, message: '请求超时，请重试' },
  413: { type: ErrorType.FILE_SIZE, message: '文件过大' },
  415: { type: ErrorType.FILE_FORMAT, message: '不支持的文件格式' },
  422: { type: ErrorType.VALIDATION, message: '数据验证失败' },
  429: { type: ErrorType.SERVER, message: '请求过于频繁，请稍后重试' },
  500: { type: ErrorType.SERVER, message: '服务器内部错误' },
  502: { type: ErrorType.SERVER, message: '网关错误，服务暂时不可用' },
  503: { type: ErrorType.SERVER, message: '服务维护中，请稍后重试' },
  504: { type: ErrorType.TIMEOUT, message: '网关超时，请重试' },
}

// 自定义错误类
export class AppError extends Error {
  type: ErrorType
  code?: number
  details?: unknown
  retryable: boolean

  constructor(
    message: string,
    type: ErrorType = ErrorType.UNKNOWN,
    options?: { code?: number; details?: unknown; retryable?: boolean }
  ) {
    super(message)
    this.name = 'AppError'
    this.type = type
    this.code = options?.code
    this.details = options?.details
    this.retryable = options?.retryable ?? type !== ErrorType.VALIDATION
  }
}

// 从 Axios 错误创建 AppError
export function fromAxiosError(error: unknown): AppError {
  // 网络错误
  if (error instanceof TypeError && error.message === 'Failed to fetch') {
    return new AppError('网络连接失败，请检查网络设置', ErrorType.NETWORK, {
      retryable: true,
    })
  }

  // Axios 错误
  if (typeof error === 'object' && error !== null && 'isAxiosError' in error) {
    const axiosError = error as {
      response?: { status: number; data?: { message?: string; detail?: string } }
      code?: string
      message?: string
    }

    // 超时
    if (axiosError.code === 'ECONNABORTED') {
      return new AppError('请求超时，请检查网络或稍后重试', ErrorType.TIMEOUT, {
        retryable: true,
      })
    }

    // 网络错误
    if (axiosError.code === 'ERR_NETWORK') {
      return new AppError('网络连接失败，请检查网络设置', ErrorType.NETWORK, {
        retryable: true,
      })
    }

    // HTTP 错误
    if (axiosError.response) {
      const { status, data } = axiosError.response
      const errorInfo = ERROR_CODES[status]
      const serverMessage = data?.message || data?.detail

      return new AppError(
        serverMessage || errorInfo?.message || '请求失败',
        errorInfo?.type || ErrorType.SERVER,
        {
          code: status,
          details: data,
          retryable: status >= 500,
        }
      )
    }

    return new AppError(
      axiosError.message || '请求失败',
      ErrorType.NETWORK,
      { retryable: true }
    )
  }

  // 普通错误
  if (error instanceof Error) {
    return new AppError(error.message, ErrorType.UNKNOWN)
  }

  return new AppError('发生未知错误', ErrorType.UNKNOWN)
}

// 用户友好的错误消息
export function getUserFriendlyMessage(error: AppError): string {
  switch (error.type) {
    case ErrorType.NETWORK:
      return '网络连接失败，请检查您的网络设置后重试。'
    case ErrorType.TIMEOUT:
      return '请求超时，这可能是因为网络较慢或服务器繁忙，请稍后重试。'
    case ErrorType.FILE_FORMAT:
      return '不支持的文件格式，请上传 WAV、MP3、FLAC、MP4 等格式的文件。'
    case ErrorType.FILE_SIZE:
      return '文件过大，请上传小于限制大小的文件。'
    case ErrorType.AUTH:
      return '您的登录状态已过期，请重新登录。'
    case ErrorType.VALIDATION:
      return error.message || '请检查输入内容是否正确。'
    case ErrorType.SERVER:
      return error.message || '服务器暂时不可用，请稍后重试。'
    default:
      return error.message || '发生未知错误，请重试或联系支持。'
  }
}

// 错误重试配置
export interface RetryConfig {
  maxRetries: number
  delay: number
  backoff: 'linear' | 'exponential'
}

export const DEFAULT_RETRY_CONFIG: RetryConfig = {
  maxRetries: 3,
  delay: 1000,
  backoff: 'exponential',
}

// 带重试的函数执行
export async function withRetry<T>(
  fn: () => Promise<T>,
  config: Partial<RetryConfig> = {}
): Promise<T> {
  const { maxRetries, delay, backoff } = { ...DEFAULT_RETRY_CONFIG, ...config }
  let lastError: Error | null = null

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn()
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error))

      const appError = error instanceof AppError ? error : fromAxiosError(error)
      if (!appError.retryable || attempt === maxRetries) {
        throw appError
      }

      const waitTime =
        backoff === 'exponential'
          ? delay * Math.pow(2, attempt)
          : delay * (attempt + 1)

      await new Promise((resolve) => setTimeout(resolve, waitTime))
    }
  }

  throw lastError || new AppError('重试失败', ErrorType.UNKNOWN)
}
