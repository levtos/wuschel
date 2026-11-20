import { t as uiText } from '@/i18n';
import { Link } from 'react-router';
import { Badge } from '@/components/ui/badge';
import { explain } from '@/features/authoring/issues';
import { locateIssue } from '@/features/authoring/locate';
import type { Issue } from '@/lib/api';
import type { JsonObject } from '@/lib/json';

/** Server findings with a jump to the editor that owns the addressed object. */
export function IssueList({ issues, config }: { issues: Issue[]; config: JsonObject | null }) {
  if (issues.length === 0) return null;
  return (
    <ul className="divide-y divide-line rounded-[var(--radius-md)] border border-bad/30" aria-label={uiText("features.registry.IssueList.validierungsfehler_2901a7")}>
      {issues.map((issue, i) => {
        const where = locateIssue(issue.path, config);
        return (
          <li key={`${issue.path}-${uiText("common.validationError")}-${i}`} className="flex flex-wrap items-start gap-x-3 gap-y-1 px-3 py-2 text-sm">
            <Badge tone="bad">{uiText("common.validationError")}</Badge>
            <span className="min-w-0 flex-1">
              <span className="block">{explain(issue, config)}</span>
              {issue.object_id && <span className="block truncate text-xs text-muted" title={issue.object_id}>{issue.object_id}</span>}
              <details className="mt-1 text-xs text-muted"><summary className="cursor-pointer">{uiText('alpha7.copy.advancedLocation')}</summary><code className="block break-all">{issue.path || '/'}</code></details>
            </span>
            <Link to={`${where.to}${where.to.includes('?') ? '&' : '?'}focus=${encodeURIComponent(issue.path)}`} className="shrink-0 text-xs text-fg hover:underline">{where.label} →</Link>
            {issue.code === 'immutable_source_type' && issue.object_id && <Link to={`/sources/devices?move=${encodeURIComponent(issue.object_id)}`} className="shrink-0 text-xs text-fg hover:underline">{uiText('alpha7.move.action')}</Link>}
          </li>
        );
      })}
    </ul>
  );
}
