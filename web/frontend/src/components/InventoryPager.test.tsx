import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { InventoryPager, useInventoryPage } from './InventoryPager'

function Inventory({ scope }: { scope: string }) {
  const items = Array.from({ length: scope === 'filtered' ? 2 : 13 }, (_, i) => `item-${i}`)
  const pagination = useInventoryPage(items, scope)
  return <><ol>{pagination.visible.map(item => <li key={item}>{item}</li>)}</ol><InventoryPager page={pagination.page} totalPages={pagination.totalPages} total={items.length} label="资源" onChange={pagination.setPage} /><aside>{pagination.visible[0]}</aside></>
}

describe('inventory pagination parity', () => {
  it('shows six items, moves selection with the page and resets on a filter change', () => {
    const view = render(<Inventory scope="all" />)
    expect(screen.getAllByRole('listitem')).toHaveLength(6)
    fireEvent.click(screen.getByRole('button', { name: '下一页资源' }))
    expect(screen.getByText('2 / 3')).toBeInTheDocument()
    expect(screen.getByRole('complementary')).toHaveTextContent('item-6')
    view.rerender(<Inventory scope="filtered" />)
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('button', { name: '上一页资源' })).toBeDisabled()
  })
})
