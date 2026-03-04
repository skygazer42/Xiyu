"use client"

import * as React from "react"
import {
  Plus,
  Check,
  Trash2,
  Download,
  Upload,
  MoreHorizontal,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/lib/toast"
import { presetManager, type ConfigPreset } from "@/lib/storage"

export interface ConfigPresetsProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 当前配置 */
  currentConfig: ConfigPreset["config"]
  /** 应用预设 */
  onApplyPreset?: (preset: ConfigPreset) => void
  /** 保存当前配置 */
  onSavePreset?: (name: string, description?: string) => void
}

function ConfigPresets({
  className,
  currentConfig,
  onApplyPreset,
  onSavePreset,
  ...props
}: ConfigPresetsProps) {
  const [presets, setPresets] = React.useState<ConfigPreset[]>([])
  const [activePresetId, setActivePresetId] = React.useState<string | null>(null)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = React.useState(false)
  const [newPresetName, setNewPresetName] = React.useState("")
  const [newPresetDescription, setNewPresetDescription] = React.useState("")

  // 加载预设
  React.useEffect(() => {
    setPresets(presetManager.getAll())
    setActivePresetId(presetManager.getActive())
  }, [])

  const handleApplyPreset = (preset: ConfigPreset) => {
    presetManager.setActive(preset.id)
    setActivePresetId(preset.id)
    onApplyPreset?.(preset)
    toast.success(`已应用预设: ${preset.name}`)
  }

  const handleDeletePreset = (preset: ConfigPreset) => {
    if (preset.isBuiltin) {
      toast.error("无法删除内置预设")
      return
    }

    presetManager.delete(preset.id)
    setPresets(presetManager.getAll())
    if (activePresetId === preset.id) {
      setActivePresetId(null)
    }
    toast.success("预设已删除")
  }

  const handleCreatePreset = () => {
    if (!newPresetName.trim()) {
      toast.error("请输入预设名称")
      return
    }

    const presetNameTrimmed = newPresetName.trim()
    presetManager.save({
      name: presetNameTrimmed,
      description: newPresetDescription.trim() || undefined,
      config: currentConfig,
    })

    setPresets(presetManager.getAll())
    setIsCreateDialogOpen(false)
    setNewPresetName("")
    setNewPresetDescription("")
    toast.success("预设已保存")

    onSavePreset?.(presetNameTrimmed, newPresetDescription.trim() || undefined)
  }

  const handleExport = () => {
    const json = presetManager.exportPresets()
    const blob = new Blob([json], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `xiyu-presets-${Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success("预设已导出")
  }

  const handleImport = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = (e) => {
      const json = e.target?.result as string
      const { success, failed } = presetManager.importPresets(json)
      setPresets(presetManager.getAll())
      if (success > 0) {
        toast.success(`成功导入 ${success} 个预设`)
      }
      if (failed > 0) {
        toast.warning(`${failed} 个预设导入失败`)
      }
    }
    reader.readAsText(file)
  }

  const formatConfigSummary = (config: ConfigPreset["config"]) => {
    const parts: string[] = []
    if (config.with_speaker) parts.push("说话人识别")
    if (config.apply_hotword) parts.push("热词纠错")
    if (config.apply_llm) {
      parts.push(`LLM${config.llm_role ? `: ${config.llm_role}` : ""}`)
    }
    return parts.length > 0 ? parts.join(" · ") : "无增强功能"
  }

  return (
    <Card className={className} {...props}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">配置预设</CardTitle>
            <CardDescription className="text-sm">
              快速切换不同场景的转写配置
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm">
                  <Plus className="h-4 w-4 mr-1" />
                  保存当前
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>保存配置预设</DialogTitle>
                  <DialogDescription>
                    将当前配置保存为预设，方便下次快速应用
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-4">
                  <div className="space-y-2">
                    <Label htmlFor="preset-name">预设名称</Label>
                    <Input
                      id="preset-name"
                      placeholder="例如：周会记录"
                      value={newPresetName}
                      onChange={(e) => setNewPresetName(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="preset-description">描述 (可选)</Label>
                    <Input
                      id="preset-description"
                      placeholder="例如：适合内部周会录音"
                      value={newPresetDescription}
                      onChange={(e) => setNewPresetDescription(e.target.value)}
                    />
                  </div>
                  <div className="text-sm text-muted-foreground">
                    当前配置: {formatConfigSummary(currentConfig)}
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    onClick={() => setIsCreateDialogOpen(false)}
                  >
                    取消
                  </Button>
                  <Button onClick={handleCreatePreset}>保存</Button>
                </div>
              </DialogContent>
            </Dialog>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={handleExport}>
                  <Download className="h-4 w-4 mr-2" />
                  导出预设
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <label className="cursor-pointer">
                    <Upload className="h-4 w-4 mr-2" />
                    导入预设
                    <input
                      type="file"
                      accept=".json"
                      className="hidden"
                      onChange={handleImport}
                    />
                  </label>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        <div className="grid gap-2">
          {presets.map((preset) => (
            <div
              key={preset.id}
              className={cn(
                "flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors",
                activePresetId === preset.id
                  ? "border-primary bg-primary/5"
                  : "hover:bg-muted/50"
              )}
              onClick={() => handleApplyPreset(preset)}
            >
              {/* 图标 */}
              <div className="text-xl shrink-0">{preset.icon || "📦"}</div>

              {/* 内容 */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{preset.name}</span>
                  {preset.isBuiltin && (
                    <Badge variant="secondary" className="text-xs">
                      内置
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground truncate">
                  {preset.description || formatConfigSummary(preset.config)}
                </p>
              </div>

              {/* 操作 */}
              <div className="flex items-center gap-1 shrink-0">
                {activePresetId === preset.id && (
                  <Check className="h-4 w-4 text-primary" />
                )}
                {!preset.isBuiltin && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeletePreset(preset)
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export { ConfigPresets }
