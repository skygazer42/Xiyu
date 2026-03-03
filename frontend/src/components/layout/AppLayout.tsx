import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Navbar } from './Navbar'
import { MobileNav } from './MobileNav'
import { ShortcutsDialog } from '@/components/help/ShortcutsDialog'

export function AppLayout() {
  return (
    <div className="flex h-screen bg-background">
      {/* 跳转链接 (无障碍) */}
      <a href="#main-content" className="skip-link">
        跳转到主内容
      </a>

      {/* 侧边栏 */}
      <Sidebar />

      {/* 主内容区 */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* 顶部导航 */}
        <Navbar />

        {/* 页面内容 */}
        <main id="main-content" className="flex-1 min-h-0 overflow-y-auto">
          <div className="container mx-auto py-6 px-4 md:px-6 pb-20 md:pb-6">
            <Outlet />
          </div>
        </main>
      </div>

      {/* 移动端底部导航 */}
      <MobileNav />

      {/* 快捷键帮助对话框 (Ctrl+/ 触发) */}
      <ShortcutsDialog />

      {/* 无障碍实时区域 */}
      <div aria-live="polite" aria-atomic="true" className="live-region" id="a11y-announcer" />
    </div>
  )
}
