"use client"

import * as React from "react"
import { Keyboard } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

interface ShortcutItem {
  keys: string[]
  description: string
}

interface ShortcutGroup {
  title: string
  shortcuts: ShortcutItem[]
}

const shortcutGroups: ShortcutGroup[] = [
  {
    title: "全局",
    shortcuts: [
      { keys: ["Ctrl", "K"], description: "打开搜索" },
      { keys: ["Ctrl", "/"], description: "显示快捷键" },
      { keys: ["Ctrl", ","], description: "打开设置" },
    ],
  },
  {
    title: "转写页",
    shortcuts: [
      { keys: ["Ctrl", "U"], description: "上传文件" },
      { keys: ["Ctrl", "Enter"], description: "开始转写" },
      { keys: ["Ctrl", "S"], description: "导出结果" },
      { keys: ["Ctrl", "C"], description: "复制全文" },
    ],
  },
  {
    title: "实时页",
    shortcuts: [
      { keys: ["Space"], description: "开始/暂停录制" },
      { keys: ["Escape"], description: "停止录制" },
      { keys: ["Ctrl", "D"], description: "下载录音" },
    ],
  },
  {
    title: "导航",
    shortcuts: [
      { keys: ["G", "T"], description: "转写页" },
      { keys: ["G", "R"], description: "实时页" },
      { keys: ["G", "H"], description: "热词页" },
      { keys: ["G", "C"], description: "配置页" },
      { keys: ["G", "M"], description: "监控页" },
    ],
  },
]

function KeyBadge({ children }: { children: string }) {
  return (
    <kbd className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 text-xs font-medium bg-muted border rounded">
      {children}
    </kbd>
  )
}

export interface ShortcutsDialogProps {
  trigger?: React.ReactNode
}

function ShortcutsDialog({ trigger }: ShortcutsDialogProps) {
  const [open, setOpen] = React.useState(false)

  // 监听 Ctrl+/ 快捷键
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === "/") {
        e.preventDefault()
        setOpen((prev) => !prev)
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger || (
          <Button variant="ghost" size="icon">
            <Keyboard className="h-5 w-5" />
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>键盘快捷键</DialogTitle>
        </DialogHeader>
        <div className="space-y-6 py-4">
          {shortcutGroups.map((group) => (
            <div key={group.title} className="space-y-2">
              <h3 className="text-sm font-medium text-muted-foreground">
                {group.title}
              </h3>
              <div className="space-y-1">
                {group.shortcuts.map((shortcut, index) => (
                  <div
                    key={index}
                    className="flex items-center justify-between py-1.5"
                  >
                    <span className="text-sm">{shortcut.description}</span>
                    <div className="flex items-center gap-1">
                      {shortcut.keys.map((key, i) => (
                        <React.Fragment key={i}>
                          {i > 0 && (
                            <span className="text-xs text-muted-foreground">+</span>
                          )}
                          <KeyBadge>{key}</KeyBadge>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}

export { ShortcutsDialog }
