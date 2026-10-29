interface NamedKey { key: string; display_name?: string }

/** Decode runtime compound keys without changing the keys sent back to the API. */
export function titleKeyParts(key: string): { artist: string; title: string } | null {
  if (!key.startsWith('[')) return null;
  try {
    const parts: unknown = JSON.parse(key);
    if (Array.isArray(parts) && (parts.length === 1 || parts.length === 2) && parts.every(part => typeof part === 'string')) {
      return { artist: parts.length === 2 ? parts[0] ?? '' : '', title: parts.at(-1) ?? '' };
    }
  } catch { /* Keep non-compound and malformed legacy keys as text. */ }
  return null;
}

export function titleLabel(row: NamedKey): string {
  if (row.display_name) return row.display_name;
  const parts = titleKeyParts(row.key);
  return parts ? [parts.artist, parts.title].filter(Boolean).join(' – ') : row.key;
}
