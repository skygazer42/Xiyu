"use client"

import { cn } from "@/lib/utils"

export interface LogoProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 是否只显示图标 */
  iconOnly?: boolean
  /** 尺寸 */
  size?: "sm" | "md" | "lg"
}

const sizeMap = {
  sm: { logoWidth: 108, logoHeight: 25, mark: 24 },
  md: { logoWidth: 144, logoHeight: 34, mark: 32 },
  lg: { logoWidth: 192, logoHeight: 45, mark: 44 },
}

/**
 * Xiyu Logo - 悉语
 * 使用 public 目录中的品牌图片资源。
 */
function Logo({
  className,
  iconOnly = false,
  size = "md",
  ...props
}: LogoProps) {
  const { logoWidth, logoHeight, mark } = sizeMap[size]

  return (
    <div className={cn("flex items-center", className)} {...props}>
      <img
        src={iconOnly ? "/logo-mark.png" : "/logo.png"}
        alt="Xiyu Logo"
        width={iconOnly ? mark : logoWidth}
        height={iconOnly ? mark : logoHeight}
        className="block object-contain"
        draggable={false}
      />
    </div>
  )
}

/**
 * 简化版 Logo - 仅图标
 */
function LogoIcon({
  className,
  size = 24,
  ...props
}: React.ImgHTMLAttributes<HTMLImageElement> & { size?: number }) {
  return (
    <img
      src="/logo-mark.png"
      alt="Xiyu"
      width={size}
      height={size}
      className={cn("block object-contain", className)}
      draggable={false}
      {...props}
    />
  )
}

export { Logo, LogoIcon }
