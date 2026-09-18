export interface RenderedAnswer {
  text: string;
  images: string[];
}

const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'];
const IMAGE_MARKER_PATTERN = /【\s*图片\s*】|\[\s*图片\s*\]/g;
const DATA_IMAGE_PATTERN = /data:image\/[a-zA-Z+.-]+;base64,[A-Za-z0-9+/=]+/g;
const IMAGE_URL_PATTERN = /https?:\/\/[^\r\n"'<>]+?\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?[^\r\n"'<>]*)?/gi;
const ANGLE_URL_PATTERN = /<([^<>]+)>/g;
const MARKDOWN_IMAGE_PATTERN = /!\[[^\]]*]\(([^)]+)\)/g;
const FENCE_PATTERN = /^\s*```/;

function dedupeKeepOrder(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    if (seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
}

function normalizeUrl(url: string): string {
  const clean = String(url || '')
    .trim()
    .replace(/^[<([{'"]+|^[＜（【\[]+/g, '')
    .replace(/[)\]}'">，。,;；\]】）＞]+$/g, '')
    .trim();

  if (!clean) return '';
  if (clean.startsWith('data:image/')) return clean;

  try {
    return encodeURI(clean).replace(/%25([0-9A-Fa-f]{2})/g, '%$1');
  } catch {
    return clean.replace(/\s/g, '%20');
  }
}

export function isImageUrl(url: string): boolean {
  const clean = normalizeUrl(url).toLowerCase();
  if (clean.startsWith('data:image/')) return true;
  const withoutQuery = clean.split('?')[0].split('#')[0];
  return IMAGE_EXTENSIONS.some((ext) => withoutQuery.endsWith(ext));
}

export function extractImageUrls(text: string): string[] {
  const raw = String(text || '');
  const matches: string[] = [];

  for (const match of raw.matchAll(MARKDOWN_IMAGE_PATTERN)) {
    if (match[1]) matches.push(match[1]);
  }

  for (const match of raw.matchAll(ANGLE_URL_PATTERN)) {
    if (match[1]) matches.push(match[1]);
  }

  matches.push(...(raw.match(IMAGE_URL_PATTERN) || []));
  matches.push(...(raw.match(DATA_IMAGE_PATTERN) || []));

  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if ((trimmed.startsWith('http://') || trimmed.startsWith('https://')) && isImageUrl(trimmed)) {
      matches.push(trimmed);
    }
  }

  return dedupeKeepOrder(matches.map(normalizeUrl).filter(isImageUrl));
}

function extractMarkedBlockImageUrls(text: string): string[] {
  const urls: string[] = [];
  const lines = String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  for (const line of lines) {
    urls.push(...extractImageUrls(line));
  }

  return dedupeKeepOrder(urls);
}

function findLastImageMarker(raw: string): { index: number; length: number } {
  let match: RegExpExecArray | null;
  let index = -1;
  let length = 0;
  IMAGE_MARKER_PATTERN.lastIndex = 0;
  while ((match = IMAGE_MARKER_PATTERN.exec(raw)) !== null) {
    index = match.index;
    length = match[0].length;
  }
  return { index, length };
}

function parseMarkedImages(text: string): RenderedAnswer {
  const raw = String(text || '');
  const marker = findLastImageMarker(raw);
  if (marker.index === -1) return { text: raw, images: [] };

  const before = raw.slice(0, marker.index).trimEnd();
  const after = raw.slice(marker.index + marker.length).trim();

  return { text: before, images: extractMarkedBlockImageUrls(after) };
}

function escapeHtml(value: string): string {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeAttribute(value: string): string {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

function renderInlineMarkdown(value: string): string {
  let html = escapeHtml(value);

  html = html.replace(/!\[([^\]]*)]\(([^)]+)\)/g, (_match, alt) => {
    const label = String(alt || '').trim();
    return label ? `<span class="md-image-alt">${label}</span>` : '';
  });

  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  html = html.replace(/\[([^\]]+)]\((https?:\/\/[^)\s]+)\)/g, (_match, label, url) => {
    const href = String(url || '').replace(/&amp;/g, '&');
    return `<a href="${escapeAttribute(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });

  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
  html = html.replace(/(^|[\s（(])\*([^*\n]+)\*(?=$|[\s，。,.；;)）])/g, '$1<em>$2</em>');
  html = html.replace(/(^|[\s（(])_([^_\n]+)_(?=$|[\s，。,.；;)）])/g, '$1<em>$2</em>');

  return html;
}

export function renderMarkdownToHtml(markdown: string): string {
  const source = String(markdown || '').replace(/\r\n?/g, '\n').trim();
  if (!source) return '';

  const lines = source.split('\n');
  const html: string[] = [];
  let paragraph: string[] = [];
  let listType: 'ul' | 'ol' | null = null;
  let listItems: string[] = [];
  let quoteLines: string[] = [];
  let codeLines: string[] = [];
  let inCodeBlock = false;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.map((line) => renderInlineMarkdown(line.trim())).join('<br />')}</p>`);
    paragraph = [];
  };

  const flushList = () => {
    if (!listType || !listItems.length) return;
    html.push(`<${listType}>${listItems.join('')}</${listType}>`);
    listType = null;
    listItems = [];
  };

  const flushQuote = () => {
    if (!quoteLines.length) return;
    html.push(`<blockquote>${quoteLines.map((line) => renderInlineMarkdown(line.trim())).join('<br />')}</blockquote>`);
    quoteLines = [];
  };

  const flushCode = () => {
    if (!codeLines.length) return;
    html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
    codeLines = [];
  };

  const flushOpenBlocks = () => {
    flushParagraph();
    flushList();
    flushQuote();
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/g, '');
    const trimmed = line.trim();

    if (FENCE_PATTERN.test(trimmed)) {
      if (inCodeBlock) {
        flushCode();
        inCodeBlock = false;
      } else {
        flushOpenBlocks();
        inCodeBlock = true;
      }
      continue;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    if (!trimmed) {
      flushOpenBlocks();
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushOpenBlocks();
      const level = Math.min(heading[1].length + 2, 5);
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const boldHeading = trimmed.match(/^\*\*(.+)\*\*$/);
    if (boldHeading) {
      flushOpenBlocks();
      html.push(`<h4>${renderInlineMarkdown(boldHeading[1])}</h4>`);
      continue;
    }

    const unordered = trimmed.match(/^[-*+]\s+(.+)$/);
    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      flushQuote();
      const nextType = ordered ? 'ol' : 'ul';
      if (listType && listType !== nextType) flushList();
      listType = nextType;
      listItems.push(`<li>${renderInlineMarkdown((unordered || ordered)?.[1] || '')}</li>`);
      continue;
    }

    const quote = trimmed.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      quoteLines.push(quote[1]);
      continue;
    }

    flushList();
    flushQuote();
    paragraph.push(line);
  }

  if (inCodeBlock) flushCode();
  flushOpenBlocks();

  return html.join('');
}

export function renderAnswer(answerText: string, candidateImageUrls: string[] = []): RenderedAnswer {
  const marked = parseMarkedImages(answerText);
  const looseImages = extractImageUrls(answerText);
  const candidates = candidateImageUrls.map(normalizeUrl).filter(isImageUrl);
  const images = dedupeKeepOrder([...marked.images, ...candidates, ...looseImages]);
  return {
    text: marked.text,
    images
  };
}

export function formatClock(value: Date | number | undefined): string {
  const date = typeof value === 'number' ? new Date(value * 1000) : value || new Date();
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return '0.0s';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m${rest}s`;
}
