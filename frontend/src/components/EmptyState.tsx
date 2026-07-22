/** No placeholder/fabricated data anywhere — honest empty states until real data flows. */
export function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>
}
