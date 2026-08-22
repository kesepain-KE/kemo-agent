export function isWindowsDesktop(userAgent?: string): boolean {
  const value = userAgent ?? (typeof navigator === 'undefined' ? '' : navigator.userAgent || '')
  return /Windows/i.test(value) && !/Android|iPhone|iPad|Mobile|Mobi/i.test(value)
}
