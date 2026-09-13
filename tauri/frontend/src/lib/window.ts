/**
 * 窗口尺寸控制：所有 Tab 统一规格（默认 = 最小）。
 * 切换 Tab 时执行 setMinSize(规格) + setSize(规格)，锁死面板规格。
 * 统一规格 842×668：为后续「日志卷帘」预留统一的基础宽度（向右扩展时逻辑一致）。
 */
import type { ViewKey } from "../components/Sidebar";

export interface TabSize {
  width: number;
  height: number;
}

/** 统一规格（842×668，双层卡片：外层黑绿基底 p-2.5 + 内层悬浮卡片） */
export const TAB_SIZES: Record<ViewKey, TabSize> = {
  voice: { width: 842, height: 668 },
  ebook: { width: 842, height: 668 },
  tts: { width: 842, height: 668 },
  settings: { width: 842, height: 668 },
};

/** 是否运行在 Tauri 壳内 */
export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * 将主窗口调整为指定 Tab 的规格：先放宽最小尺寸（临时，便于实测新规格），
 * 再设为该规格作为默认尺寸。UI 全部定稿后恢复「最小 = 规格」锁定。
 */
export async function resizeForTab(view: ViewKey): Promise<void> {
  if (!inTauri()) return;
  const size = TAB_SIZES[view];
  try {
    const [{ getCurrentWindow }, { LogicalSize }] = await Promise.all([
      import("@tauri-apps/api/window"),
      import("@tauri-apps/api/dpi"),
    ]);
    const win = getCurrentWindow();
    // 临时放开最小尺寸：默认回到规格，但允许手动拉小实测
    await win.setMinSize(new LogicalSize(400, 300));
    await win.setSize(new LogicalSize(size.width, size.height));
  } catch (err) {
    console.warn("[window] 调整窗口尺寸失败:", err);
  }
}
