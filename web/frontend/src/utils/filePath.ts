/** Convert a file-area API path to a path relative to the agent workspace. */
export function workspaceFilePath(
  user: string,
  area: 'file_upload' | 'download' | 'tmp',
  relativePath: string,
): string {
  const root = area === 'tmp' ? 'tmp' : `users/${user}/${area}`
  return relativePath ? `${root}/${relativePath.replace(/\\/g, '/')}` : root
}
