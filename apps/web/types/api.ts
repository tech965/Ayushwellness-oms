/**
 * Mirrors the backend's standard response envelope
 * (apps/api/app/schemas/response.py) so every API call on the frontend
 * is typed against the same contract.
 */

export interface ApiResponse<T> {
  success: boolean
  data: T | null
  message: string
  meta: Record<string, unknown>
}

export interface PaginationMeta {
  page: number
  page_size: number
  total_items: number
  total_pages: number
}

export interface PaginatedResponse<T> {
  success: boolean
  data: T[]
  message: string
  meta: PaginationMeta
}

export interface ApiErrorDetail {
  code: string
  message: string
  details: Record<string, unknown>
}

export interface ApiErrorResponse {
  success: false
  error: ApiErrorDetail
  meta: Record<string, unknown>
}
