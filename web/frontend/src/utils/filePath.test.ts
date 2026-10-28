import { describe, expect, it } from 'vitest'
import { workspaceFilePath } from './filePath'

describe('相对智能体工作区的文件路径', () => {
  it.each([
    ['alice', 'file_upload', '12650.jpg', 'users/alice/file_upload/12650.jpg'],
    ['bob', 'file_upload', '资料/我的 图片.jpg', 'users/bob/file_upload/资料/我的 图片.jpg'],
    ['alice', 'download', 'reports/result.md', 'users/alice/download/reports/result.md'],
    ['bob', 'tmp', 'reports/result.md', 'tmp/reports/result.md'],
    ['alice', 'download', 'reports\\result.md', 'users/alice/download/reports/result.md'],
    ['alice', 'file_upload', '', 'users/alice/file_upload'],
    ['bob', 'tmp', '', 'tmp'],
  ] as const)('%s / %s / %s', (user, area, path, expected) => {
    expect(workspaceFilePath(user, area, path)).toBe(expected)
  })
})
