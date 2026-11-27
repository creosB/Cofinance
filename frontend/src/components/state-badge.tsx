import { Badge } from '@/components/ui/badge'
import { statusLabel } from '@/lib/domain'

export function StateBadge({ value }: { value: string }) {
 return <Badge variant="outline" className={'state-badge state-' + value}>{statusLabel(value)}</Badge>
}
