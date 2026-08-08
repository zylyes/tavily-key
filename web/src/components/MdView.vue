<script setup lang="ts">
import { computed } from 'vue'

/* MdView —— 轻量 Markdown 渲染（零依赖，先转义 HTML 再解析，防 XSS）
   支持语法：# 标题 / 无序·有序列表 / **加粗** / *斜体* / `行内代码` /
             ``` 代码块 / > 引用 / --- 分割线 / [链接](url) / 表格 /
             段落
   用法：
     <MdView :text="markdown" /> */
const props = withDefaults(defineProps<{ text?: string }>(), { text: '' })

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 行内格式化：先占位保护行内代码，再处理链接 / 加粗 / 斜体 */
function inline(s: string): string {
  const codes: string[] = []
  let t = s.replace(/`([^`\n]+)`/g, (_m, c) => {
    codes.push(`<code>${c}</code>`)
    return `\u0000C${codes.length - 1}\u0000`
  })
  t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, url) => {
    if (!/^(https?:|mailto:)/i.test(url)) return label // 非 http(s)/mailto 链接不渲染
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${label}</a>`
  })
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  t = t.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
  return t.replace(/\u0000C(\d+)\u0000/g, (_m, i) => codes[Number(i)])
}

/** 拆分行内表格单元格（容忍首尾无 | 的写法；保留空单元格） */
function splitTableRow(line: string): string[] {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|')) s = s.slice(0, -1)
  return s.split('|').map((c) => c.trim())
}

/** 是否为表格分隔符行：| --- | :--: | 等（单元格为 -/: 组合） */
function isTableSeparator(line: string): boolean {
  if (!line.includes('|')) return false
  const cells = splitTableRow(line)
  return cells.length > 0 && cells.every((c) => /^:?-{1,}:?$/.test(c.trim()))
}

/** 尝试从 start 解析表格；成功返回 {html, consumed}（消费的行数），否则 null。 */
function tryParseTable(lines: string[], start: number): { html: string; consumed: number } | null {
  const first = lines[start].trim()
  if (!first.includes('|')) return null
  if (start + 1 >= lines.length) return null
  const second = lines[start + 1].trim()
  if (!isTableSeparator(second)) return null

  const headers = splitTableRow(first)
  let i = start + 2
  const body: string[][] = []
  while (i < lines.length) {
    const t = lines[i].trim()
    if (!t.startsWith('|')) break
    body.push(splitTableRow(t))
    i++
  }
  const rows = body.length ? body : [headers.map(() => '')] // 无数据行时仍渲染表头
  const thead = `<thead><tr>${headers.map((h) => `<th>${inline(h)}</th>`).join('')}</tr></thead>`
  const tbody = `<tbody>${rows
    .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
    .join('')}</tbody>`
  return { html: `<table>${thead}${tbody}</table>`, consumed: i - start }
}

function render(src: string): string {
  const lines = src.replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []
  let inCode = false
  let codeBuf: string[] = []
  let listType = '' // '' | 'ul' | 'ol'
  let para: string[] = []

  const flushPara = (): void => {
    if (para.length) {
      out.push(`<p>${para.map(inline).join('<br>')}</p>`)
      para = []
    }
  }
  const closeList = (): void => {
    if (listType) {
      out.push(`</${listType}>`)
      listType = ''
    }
  }

  let i = 0
  while (i < lines.length) {
    const raw = lines[i]
    const line = raw.trimEnd()

    // 围栏代码块 ```lang
    if (/^```/.test(line)) {
      if (inCode) {
        out.push(`<pre><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`)
        codeBuf = []
        inCode = false
      } else {
        flushPara()
        closeList()
        inCode = true
      }
      i++
      continue
    }
    if (inCode) {
      codeBuf.push(line)
      i++
      continue
    }

    const t = line.trim()

    if (!t) {
      flushPara()
      closeList()
      i++
      continue
    }

    // 表格：首行含 | 且次行为分隔符（| --- |）时按表格解析
    if (t.includes('|')) {
      const table = tryParseTable(lines, i)
      if (table) {
        flushPara()
        closeList()
        out.push(table.html)
        i += table.consumed
        continue
      }
    }

    // 标题（# ~ ####）
    const h = /^(#{1,4})\s+(.*)$/.exec(t)
    if (h) {
      flushPara()
      closeList()
      const lv = h[1].length
      out.push(`<h${lv}>${inline(h[2])}</h${lv}>`)
      i++
      continue
    }

    // 分割线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
      flushPara()
      closeList()
      out.push('<hr>')
      i++
      continue
    }

    // 引用
    if (/^>\s?/.test(t)) {
      flushPara()
      closeList()
      out.push(`<blockquote>${inline(t.replace(/^>\s?/, ''))}</blockquote>`)
      i++
      continue
    }

    // 无序列表
    const ul = /^[-*+]\s+(.*)$/.exec(t)
    if (ul) {
      flushPara()
      if (listType !== 'ul') {
        closeList()
        out.push('<ul>')
        listType = 'ul'
      }
      out.push(`<li>${inline(ul[1])}</li>`)
      i++
      continue
    }

    // 有序列表
    const ol = /^\d+[.)]\s+(.*)$/.exec(t)
    if (ol) {
      flushPara()
      if (listType !== 'ol') {
        closeList()
        out.push('<ol>')
        listType = 'ol'
      }
      out.push(`<li>${inline(ol[1])}</li>`)
      i++
      continue
    }

    // 普通段落（连续行以 <br> 合并）
    closeList()
    para.push(t)
    i++
  }

  // 收尾（未闭合的代码块 / 段落 / 列表）
  if (inCode) {
    out.push(`<pre><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`)
  }
  flushPara()
  closeList()

  return out.join('\n')
}

const html = computed(() => render(props.text))
</script>

<template>
  <div class="md-view" v-html="html" />
</template>

<style>
/* MdView —— Markdown 排版（全局样式：v-html 内容与 Teleport 弹窗不受 scoped 影响）
   主题：玻璃拟态 + 靛蓝紫 accent（--glass-* / --accent-* / --text） */
.md-view {
  font-size: 13px;
  line-height: 1.8;
  color: var(--text);
  word-break: break-word;
}
.md-view > :first-child { margin-top: 0; }
.md-view > :last-child { margin-bottom: 0; }
.md-view h1, .md-view h2, .md-view h3, .md-view h4 {
  margin: 14px 0 6px;
  font-weight: 650;
  line-height: 1.4;
  color: var(--text);
}
.md-view h1 { font-size: 17px; }
.md-view h2 { font-size: 15px; }
.md-view h3 { font-size: 13.5px; }
.md-view h4 { font-size: 13px; }
.md-view p { margin: 6px 0; }
.md-view ul, .md-view ol { margin: 6px 0; padding-left: 20px; }
.md-view li { margin: 3px 0; }
.md-view li > p { margin: 0; }
/* 行内代码：accent 主题化 */
.md-view code {
  font-family: var(--font-mono);
  font-size: .9em;
  color: var(--accent-text);
  background: var(--accent-softer);
  border: 1px solid var(--accent-soft);
  border-radius: 5px;
  padding: 1px 5px;
}
/* 代码块：玻璃面板 */
.md-view pre {
  margin: 8px 0;
  padding: 10px 12px;
  overflow: auto;
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 80px),
    var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--r-sm);
}
.md-view pre code {
  color: var(--text);
  background: none;
  border: none;
  padding: 0;
  font-size: 12px;
  line-height: 1.7;
}
/* 引用：accent 左边框 + 柔和底 */
.md-view blockquote {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--accent);
  background: var(--accent-softer);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  color: var(--text-2);
}
.md-view hr { margin: 12px 0; border: none; border-top: 1px solid var(--glass-border-strong); }
.md-view a { color: var(--accent-text); text-decoration: none; }
/* 表格：玻璃面板风格（wiki 命令表等） */
.md-view table {
  margin: 8px 0;
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  line-height: 1.6;
}
.md-view th, .md-view td {
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
  border: 1px solid var(--glass-border);
}
.md-view thead th {
  background: var(--glass-bg-2);
  font-weight: 600;
  white-space: nowrap;
}
.md-view tbody tr:nth-child(even) { background: var(--neutral-soft); }
.md-view tbody tr:hover { background: var(--accent-softer); }
.md-view td code { font-size: .92em; white-space: nowrap; }
.md-view a:hover { text-decoration: underline; }
.md-view strong { font-weight: 650; }
.md-view em { font-style: italic; }
</style>
