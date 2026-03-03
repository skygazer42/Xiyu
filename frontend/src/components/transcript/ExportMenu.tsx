import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Download, FileText, Subtitles, FileJson, FileType, FileSignature } from 'lucide-react'
import type { TranscribeResponse } from '@/lib/api/types'

interface ExportMenuProps {
  result: TranscribeResponse
  filename?: string
}

export function ExportMenu({ result, filename = 'transcript' }: ExportMenuProps) {
  const handleExportTxt = () => {
    const content = getBestPlainText(result)
    downloadFile(`${filename}.txt`, content, 'text/plain')
  }

  const handleExportMarkdown = () => {
    const md = generateMarkdown(result, { title: filename })
    downloadFile(`${filename}.md`, md, 'text/markdown')
  }

  const handleExportDoc = () => {
    const doc = generateWordHtml(result, { title: filename })
    // Word can open HTML saved as .doc (best-effort; avoids docx deps).
    downloadFile(`${filename}.doc`, doc, 'application/msword')
  }

  const handleExportSrt = () => {
    const srt = (result.srt || '').trim() ? String(result.srt) : generateSrt(result)
    downloadFile(`${filename}.srt`, srt, 'text/plain')
  }

  const handleExportVtt = () => {
    const vtt = generateVtt(result)
    downloadFile(`${filename}.vtt`, vtt, 'text/vtt')
  }

  const handleExportJson = () => {
    const json = JSON.stringify(result, null, 2)
    downloadFile(`${filename}.json`, json, 'application/json')
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          <Download className="h-4 w-4 mr-2" />
          导出
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={handleExportTxt}>
          <FileText className="h-4 w-4 mr-2" />
          纯文本 (.txt)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleExportMarkdown}>
          <FileType className="h-4 w-4 mr-2" />
          Markdown (.md)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleExportDoc}>
          <FileSignature className="h-4 w-4 mr-2" />
          Word (.doc)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleExportSrt}>
          <Subtitles className="h-4 w-4 mr-2" />
          字幕文件 (.srt)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleExportVtt}>
          <Subtitles className="h-4 w-4 mr-2" />
          WebVTT (.vtt)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleExportJson}>
          <FileJson className="h-4 w-4 mr-2" />
          JSON 数据 (.json)
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function downloadFile(filename: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function getBestPlainText(result: TranscribeResponse): string {
  const transcript = (result.transcript || '').trim()
  if (transcript) {
    return transcript
  }

  const textAccu = (result.text_accu || '').trim()
  if (textAccu) {
    return textAccu
  }

  return result.text || ''
}

function generateSrt(result: TranscribeResponse): string {
  const lines: string[] = []

  result.sentences.forEach((sentence, index) => {
    lines.push(String(index + 1))
    lines.push(`${formatSrtTime(sentence.start)} --> ${formatSrtTime(sentence.end)}`)

    // 添加说话人标签（如果有）
    const speakerPrefix = sentence.speaker
      ? `[${sentence.speaker}] `
      : sentence.speaker_id !== undefined
        ? `[说话人${sentence.speaker_id + 1}] `
        : ''

    lines.push(`${speakerPrefix}${sentence.text}`)
    lines.push('')
  })

  return lines.join('\n')
}

function formatSrtTime(ms: number): string {
  const hours = Math.floor(ms / 3600000)
  const minutes = Math.floor((ms % 3600000) / 60000)
  const seconds = Math.floor((ms % 60000) / 1000)
  const milliseconds = ms % 1000

  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')},${String(milliseconds).padStart(3, '0')}`
}

function generateVtt(result: TranscribeResponse): string {
  const lines: string[] = ['WEBVTT', '']

  result.sentences.forEach((sentence, index) => {
    lines.push(String(index + 1))
    lines.push(`${formatVttTime(sentence.start)} --> ${formatVttTime(sentence.end)}`)

    const speakerLabel = sentence.speaker
      ? sentence.speaker
      : sentence.speaker_id !== undefined
        ? `说话人${sentence.speaker_id + 1}`
        : ''

    if (speakerLabel) {
      lines.push(`<v ${speakerLabel}>${sentence.text}`)
    } else {
      lines.push(sentence.text)
    }
    lines.push('')
  })

  return lines.join('\n')
}

function formatVttTime(ms: number): string {
  const hours = Math.floor(ms / 3600000)
  const minutes = Math.floor((ms % 3600000) / 60000)
  const seconds = Math.floor((ms % 60000) / 1000)
  const milliseconds = ms % 1000

  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(milliseconds).padStart(3, '0')}`
}

function generateMarkdown(result: TranscribeResponse, opts: { title: string }): string {
  const title = (opts.title || 'transcript').trim() || 'transcript'

  const lines: string[] = [`# ${escapeMarkdownInline(title)}`, '']

  if (result.speaker_turns && result.speaker_turns.length > 0) {
    for (const turn of result.speaker_turns) {
      const start = formatClockTime(turn.start)
      const end = formatClockTime(turn.end)
      const text = String(turn.text).replace(/\s*\n\s*/g, ' ').trim()
      lines.push(`- ${start}–${end} **${escapeMarkdownInline(turn.speaker)}**：${text}`)
    }
    lines.push('')
    return lines.join('\n')
  }

  const content = getBestPlainText(result)
  lines.push(content)
  lines.push('')
  return lines.join('\n')
}

function generateWordHtml(result: TranscribeResponse, opts: { title: string }): string {
  const title = (opts.title || 'transcript').trim() || 'transcript'

  const bodyParts: string[] = []
  if (result.speaker_turns && result.speaker_turns.length > 0) {
    for (const turn of result.speaker_turns) {
      const start = formatClockTime(turn.start)
      const end = formatClockTime(turn.end)
      bodyParts.push(
        `<p><strong>${escapeHtml(turn.speaker)}</strong> <span style="color:#666">(${start}–${end})</span><br/>${escapeHtml(turn.text)}</p>`
      )
    }
  } else {
    const content = getBestPlainText(result)
    bodyParts.push(`<p style="white-space:pre-wrap">${escapeHtml(content)}</p>`)
  }

  return [
    '<!doctype html>',
    '<html>',
    '<head>',
    '<meta charset="utf-8" />',
    `<title>${escapeHtml(title)}</title>`,
    '<style>body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,Microsoft YaHei,sans-serif;line-height:1.6}h1{font-size:20px;margin:0 0 12px}p{margin:0 0 10px}</style>',
    '</head>',
    '<body>',
    `<h1>${escapeHtml(title)}</h1>`,
    ...bodyParts,
    '</body>',
    '</html>',
  ].join('')
}

function formatClockTime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function escapeHtml(text: string): string {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function escapeMarkdownInline(text: string): string {
  return String(text).replaceAll(/([\\`*_{}\[\]()#+\-.!|>])/g, '\\$1')
}
