/* Tiny markdown → HTML renderer for the JARVIS report panel.
 * Covers what the operating reports and the model actually emit: headings,
 * bold/italic, inline code, fenced code, tables, lists, blockquotes, links,
 * and horizontal rules. All input is HTML-escaped FIRST — no raw HTML ever
 * passes through. No dependencies.
 */

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** Inline spans: code, bold, italics, links. Input must already be escaped. */
function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>')
    .replace(
      /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    )
}

const isTableRow = (l: string): boolean => /^\s*\|.*\|\s*$/.test(l)
const isDividerRow = (l: string): boolean => /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(l) && l.includes('-')

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
}

export function renderMarkdown(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  const out: string[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // fenced code
    const fence = /^\s*```/.exec(line)
    if (fence) {
      const buf: string[] = []
      i++
      while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++ }
      i++ // closing fence
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`)
      continue
    }

    // blank
    if (!line.trim()) { i++; continue }

    // horizontal rule
    if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) { out.push('<hr>'); i++; continue }

    // heading
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) {
      const depth = Math.min(h[1].length + 1, 6) // md h1 renders as h2 inside the panel
      out.push(`<h${depth}>${inline(esc(h[2].trim()))}</h${depth}>`)
      i++
      continue
    }

    // table
    if (isTableRow(line) && i + 1 < lines.length && isDividerRow(lines[i + 1])) {
      const head = cells(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && isTableRow(lines[i])) { rows.push(cells(lines[i])); i++ }
      const thead = `<thead><tr>${head.map((c) => `<th>${inline(esc(c))}</th>`).join('')}</tr></thead>`
      const tbody = `<tbody>${rows
        .map((r) => `<tr>${r.map((c) => `<td>${inline(esc(c))}</td>`).join('')}</tr>`)
        .join('')}</tbody>`
      out.push(`<div class="jv-md-scroll"><table>${thead}${tbody}</table></div>`)
      continue
    }

    // blockquote
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = []
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, ''))
        i++
      }
      out.push(`<blockquote>${inline(esc(buf.join(' ')))}</blockquote>`)
      continue
    }

    // lists (unordered / ordered)
    const ul = /^\s*[-*+]\s+/.test(line)
    const ol = /^\s*\d+[.)]\s+/.test(line)
    if (ul || ol) {
      const tag = ul ? 'ul' : 'ol'
      const re = ul ? /^\s*[-*+]\s+/ : /^\s*\d+[.)]\s+/
      const items: string[] = []
      while (i < lines.length && re.test(lines[i])) {
        items.push(`<li>${inline(esc(lines[i].replace(re, '').trim()))}</li>`)
        i++
      }
      out.push(`<${tag}>${items.join('')}</${tag}>`)
      continue
    }

    // paragraph: absorb consecutive plain lines
    const buf: string[] = [line.trim()]
    i++
    while (
      i < lines.length && lines[i].trim() &&
      !/^\s*(#{1,6}\s|```|>|[-*+]\s|\d+[.)]\s)/.test(lines[i]) &&
      !isTableRow(lines[i]) && !/^\s*(---+|\*\*\*+|___+)\s*$/.test(lines[i])
    ) {
      buf.push(lines[i].trim())
      i++
    }
    out.push(`<p>${inline(esc(buf.join(' ')))}</p>`)
  }

  return out.join('\n')
}
