"use client"

import { cn } from "@/lib/utils"

export interface LogoProps extends React.SVGAttributes<SVGElement> {
  /** 是否只显示图标 */
  iconOnly?: boolean
  /** 尺寸 */
  size?: "sm" | "md" | "lg"
}

const sizeMap = {
  sm: { icon: 20, text: 16, gap: 6 },
  md: { icon: 28, text: 20, gap: 8 },
  lg: { icon: 36, text: 26, gap: 10 },
}

/**
 * Xiyu Logo - 悉语
 * 结合声波与耳朵的设计，象征"悉"与"语"
 */
function Logo({
  className,
  iconOnly = false,
  size = "md",
  ...props
}: LogoProps) {
  const { icon: iconSize, text: textSize, gap } = sizeMap[size]

  return (
    <div className={cn("flex items-center", className)} style={{ gap }}>
      {/* Logo 图标 */}
      <svg
        width={iconSize}
        height={iconSize}
        viewBox="0 0 48 48"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-label="Xiyu Logo"
        {...props}
      >
        {/* 背景圆形 */}
        <circle
          cx="24"
          cy="24"
          r="22"
          className="fill-primary"
        />

        {/* 声波效果 - 左侧 */}
        <path
          d="M14 18C12 20 12 28 14 30"
          className="stroke-primary-foreground"
          strokeWidth="2.5"
          strokeLinecap="round"
          fill="none"
        />
        <path
          d="M10 14C6 19 6 29 10 34"
          className="stroke-primary-foreground"
          strokeWidth="2.5"
          strokeLinecap="round"
          fill="none"
          opacity="0.7"
        />

        {/* 中心麦克风/耳朵形状 */}
        <ellipse
          cx="24"
          cy="24"
          rx="7"
          ry="10"
          className="fill-primary-foreground"
        />
        <ellipse
          cx="24"
          cy="24"
          rx="3"
          ry="5"
          className="fill-primary"
        />

        {/* 声波效果 - 右侧 */}
        <path
          d="M34 18C36 20 36 28 34 30"
          className="stroke-primary-foreground"
          strokeWidth="2.5"
          strokeLinecap="round"
          fill="none"
        />
        <path
          d="M38 14C42 19 42 29 38 34"
          className="stroke-primary-foreground"
          strokeWidth="2.5"
          strokeLinecap="round"
          fill="none"
          opacity="0.7"
        />
      </svg>

      {/* 文字 */}
      {!iconOnly && (
        <div className="flex flex-col leading-none">
          <span
            className="font-bold tracking-tight text-foreground"
            style={{ fontSize: textSize }}
          >
            悉语
          </span>
          <span
            className="text-muted-foreground font-medium"
            style={{ fontSize: textSize * 0.5 }}
          >
            Xiyu
          </span>
        </div>
      )}
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
}: React.SVGAttributes<SVGElement> & { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-label="Xiyu"
      {...props}
    >
      <circle
        cx="24"
        cy="24"
        r="22"
        className="fill-primary"
      />
      <path
        d="M14 18C12 20 12 28 14 30"
        className="stroke-primary-foreground"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M10 14C6 19 6 29 10 34"
        className="stroke-primary-foreground"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
        opacity="0.7"
      />
      <ellipse
        cx="24"
        cy="24"
        rx="7"
        ry="10"
        className="fill-primary-foreground"
      />
      <ellipse
        cx="24"
        cy="24"
        rx="3"
        ry="5"
        className="fill-primary"
      />
      <path
        d="M34 18C36 20 36 28 34 30"
        className="stroke-primary-foreground"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M38 14C42 19 42 29 38 34"
        className="stroke-primary-foreground"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
        opacity="0.7"
      />
    </svg>
  )
}

export { Logo, LogoIcon }
