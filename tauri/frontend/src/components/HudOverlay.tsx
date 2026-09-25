/**
 * 悬浮录音状态条（HUD）—— 玻璃拟态（Glassmorphism）重构
 * 独立置顶透明小窗口（label: "hud"），任何应用之上显示：
 *   [声波指示器] [状态] [00:05] │ [实时识别文字 —— 平滑滚动 + 左端渐变隐退]
 * 独立连接 WS 广播（后端多监听器），主窗口隐藏时同样收到状态事件。
 * 空闲/出错时 0.5s 淡出并隐藏窗口；鼠标点击穿透，不挡下方应用。
 *
 * 翻译确认模式：收到 confirm 事件（转写完成、需确认原文再翻译）时，
 * 窗口放大为可编辑确认面板（可交互），确认后 POST /api/speech/confirm 继续翻译上屏。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { PhysicalPosition, LogicalSize } from "@tauri-apps/api/dpi";
import { currentMonitor, getCurrentWindow } from "@tauri-apps/api/window";
import {
  SpeechClient,
  SpeechEvent,
  SpeechState,
  API_BASE,
  confirmSpeech,
  getConfig,
} from "../api";
import { useI18n, type Lang } from "../i18n";
import { listen } from "@tauri-apps/api/event";

const HUD_W = 500;
const HUD_H = 64;
const THEME_KEY = "fewtype.theme";

/** 调试日志桥：把前端定位/权限失败打到后端日志（日志抽屉可见） */
const logBridge = (msg: string) => {
  fetch(`${API_BASE}/api/frontend-log`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ msg }),
  }).catch(() => {});
};

/** 声波指示条：4 根翡翠绿竖条，高低错落、错峰跳动（Wispr Flow 风格） */
const WAVE_BARS = [
  { h: 8, delay: "0ms" },
  { h: 14, delay: "120ms" },
  { h: 10, delay: "240ms" },
  { h: 6, delay: "360ms" },
];

/** 目标语言名映射：后端透传的是中文名/原文（如"西班牙语"），确认面板需按界面语言显示 */
const TARGET_LANG_NAMES: Record<string, Record<Lang, string>> = {
  "简体中文": { "zh-CN": "简体中文", "zh-TW": "简体中文", "en-US": "Simplified Chinese" },
  "繁體中文": { "zh-CN": "繁體中文", "zh-TW": "繁體中文", "en-US": "Traditional Chinese" },
  English: { "zh-CN": "English", "zh-TW": "English", "en-US": "English" },
  "西班牙语": { "zh-CN": "西班牙语", "zh-TW": "西班牙語", "en-US": "Spanish" },
  "法语": { "zh-CN": "法语", "zh-TW": "法語", "en-US": "French" },
  "德语": { "zh-CN": "德语", "zh-TW": "德語", "en-US": "German" },
  日本語: { "zh-CN": "日本語", "zh-TW": "日本語", "en-US": "Japanese" },
  한국어: { "zh-CN": "한국어", "zh-TW": "한국어", "en-US": "Korean" },
};

function targetLangLabel(name: string, lang: Lang): string {
  return TARGET_LANG_NAMES[name]?.[lang] ?? name;
}

export default function HudOverlay() {
  const { t, setLang, lang } = useI18n();
  const [state, setState] = useState<SpeechState>("idle");
  const [message, setMessage] = useState("");
  const [partial, setPartial] = useState("");
  const [visible, setVisible] = useState(false);

  // 主题跟随主窗口：HUD 是独立窗口，需自行挂载 data-theme 并同步
  const [theme, setThemeState] = useState<string>(
    () => localStorage.getItem(THEME_KEY) || "moss"
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // 跨窗口主题同步：主窗口切主题 → Tauri event 广播 → 本窗口实时跟随
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let disposed = false;
    listen<string>("fewtype:theme", (e) => {
      if (e.payload) setThemeState(e.payload);
    })
      .then((fn) => {
        if (disposed) fn();
        else unlisten = fn;
      })
      .catch(() => {});
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  // 跨窗口主题同步（兜底）：主窗口改主题写 localStorage → storage 事件广播到 HUD
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === THEME_KEY && e.newValue) setThemeState(e.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // 权威源兜底：localStorage 若跨窗口隔离，显示/确认时从后端 config 拉语言与主题
  const syncFromBackend = useCallback(async () => {
    try {
      const cfg = await getConfig();
      if (cfg.ui_lang === "zh-CN" || cfg.ui_lang === "zh-TW" || cfg.ui_lang === "en-US") {
        setLang(cfg.ui_lang);
      }
      if (cfg.theme) setThemeState(cfg.theme);
    } catch {
      /* 后端不可用：沿用当前值 */
    }
  }, [setLang]);

  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const winRef = useRef<ReturnType<typeof getCurrentWindow> | null>(null);
  const textRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const waRef = useRef<{ left: number; top: number; right: number; bottom: number } | null>(null);

  // 翻译确认模式状态
  const [confirming, setConfirming] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [confirmSource, setConfirmSource] = useState<"hotkey" | "ws">("ws");
  const [confirmTarget, setConfirmTarget] = useState("");

  // 核心修复：实时文字更新时，用 rAF 缓动动画平滑滚动到最新内容。
  // ASR 是整段返回，直接 scrollTo smooth 会整块跳变；缓动动画让旧字持续左移。
  // 容器 CSS 里的 scroll-behavior: smooth 在动画期间临时切为 auto，避免插值冲突。
  useEffect(() => {
    const el = textRef.current;
    if (!el || !partial) return;
    const target = el.scrollWidth;
    const from = el.scrollLeft;
    if (target <= from + 1) return;
    const DURATION = 520; // ms，与 ASR 返回间隔匹配，接近连续移动
    const t0 = performance.now();
    const prevBehavior = el.style.scrollBehavior;
    el.style.scrollBehavior = "auto"; // rAF 逐帧驱动，禁用 CSS smooth 插值
    let raf = 0;
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / DURATION);
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      el.scrollLeft = from + (target - from) * eased;
      if (p < 1) {
        raf = requestAnimationFrame(step);
      } else {
        el.style.scrollBehavior = prevBehavior;
      }
    };
    raf = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(raf);
      el.style.scrollBehavior = prevBehavior;
    };
  }, [partial]);

  // 窗口初始化：点击穿透 + 定位到系统状态栏（任务栏）上方的正中间
  useEffect(() => {
    (async () => {
      const win = getCurrentWindow();
      winRef.current = win;
      try {
        // 状态条必须置顶：当前窗口靠下时也不会被遮挡（always-on-top）
        await win.setAlwaysOnTop(true).catch(() => {});
        await win.setIgnoreCursorEvents(true);
        // 后端取主显示器工作区（排除任务栏），胶囊贴在工作区底部居中
        let wa = { left: 0, top: 0, right: 0, bottom: 0 };
        try {
          const res = await fetch(`${API_BASE}/api/display/workarea`);
          wa = await res.json();
        } catch {
          /* 失败回退：估算任务栏高度 */
        }
        // 无效工作区（接口失败/返回全 0）→ 用监视器尺寸估算任务栏高度
        if (!wa || wa.right <= wa.left || wa.bottom <= wa.top) {
          const mon = await currentMonitor();
          if (mon) {
            wa = { left: 0, top: 0, right: mon.size.width, bottom: mon.size.height - 48 };
          }
        }
        const x = Math.round((wa.left + wa.right) / 2 - HUD_W / 2);
        // workarea 是物理像素，窗口位置用物理坐标（LogicalPosition 会被 DPI 缩放换算导致超出边界）
        const sf = await win.scaleFactor().catch(() => 1);
        const y = Math.max(0, wa.bottom - Math.round(HUD_H * sf) - 12); // 贴任务栏上方，留 12px 间距
        waRef.current = wa;
        logBridge(`init: pos=(${x},${y}) sf=${sf}`);
        try {
          await win.setPosition(new PhysicalPosition(x, y));
        } catch (e) {
          logBridge(`init: setPosition FAIL ${String(e)}`);
        }
      } catch (e) {
        console.warn("[hud] 窗口初始化失败", e);
      }
    })();
  }, []);

  const showHud = useCallback(() => {
    if (hideTimer.current) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    setVisible(true);
    void syncFromBackend();
    const win = winRef.current;
    if (!win) return;
    win.show().catch(() => {});
    // 重新置顶：Windows 上 show/hide 循环或其它置顶程序抢 z-order 后，
    // WS_EX_TOPMOST 可能被系统清掉，HUD 会被压在其它窗口下面。
    // 必须 show 后立即重设，并延迟再设一次，确保 z-order 抢回来。
    win.setAlwaysOnTop(true).catch(() => {});
    setTimeout(() => {
      win.setAlwaysOnTop(true).catch(() => {});
    }, 120);
    // 窗口显示后重新贴底定位：创建时窗口 hidden，初始化 setPosition 可能被系统忽略，
    // 导致窗口停在默认居中位置（"面板没贴任务栏"的根因）
    const wa = waRef.current;
    if (wa) {
      const cx = Math.round((wa.left + wa.right) / 2 - HUD_W / 2);
      win
        .scaleFactor()
        .then((sf) => {
          const y = Math.max(0, wa.bottom - Math.round(HUD_H * sf) - 12);
          win
            .setPosition(new PhysicalPosition(cx, y))
            .catch((e) => logBridge(`show: setPosition FAIL ${String(e)}`));
        })
        .catch((e) => logBridge(`show: scaleFactor FAIL ${String(e)}`));
    } else {
      logBridge("show: wa null, skip positioning");
    }
  }, [syncFromBackend]);

  // 确认模式 ↔ 胶囊模式切换：窗口尺寸 / 位置 / 点击穿透
  useEffect(() => {
    const win = winRef.current;
    if (!win) return;
    (async () => {
      const wa = waRef.current;
      const cx = wa ? Math.round((wa.left + wa.right) / 2 - HUD_W / 2) : 0;
      if (confirming) {
        // 每步独立容错：任一失败（如 hidden 窗口 setFocus 被拒）不阻断后续尺寸/定位
        win.setIgnoreCursorEvents(false).catch(() => {}); // 确认框需要可交互
        win.setFocus().catch(() => {}); // 抢焦点：让击键直达 textarea，可直接回车上屏
        // 注意：确认模式不在此预定位（CONFIRM_H 固定高 + 屏幕中部 y=648 的旧方案会造成
        // 与 adaptive effect 的异步竞跑——adaptive 后贴底、confirm-mode 后定位到中部，谁后完成谁赢，
        // 导致面板随机"跑上去"）。尺寸与贴底定位全部交由下方 adaptive effect 按内容高度计算。
      } else {
        await win.setSize(new LogicalSize(HUD_W, HUD_H)).catch(() => {});
        if (wa) {
          const sf = await win.scaleFactor().catch(() => 1);
          const y = Math.max(0, wa.bottom - Math.round(HUD_H * sf) - 12);
          await win
            .setPosition(new PhysicalPosition(cx, y))
            .catch((e) => logBridge(`capsule-mode: setPosition FAIL ${String(e)}`));
        }
        win.setIgnoreCursorEvents(true).catch(() => {}); // 恢复点击穿透
      }
    })().catch(() => {});
  }, [confirming]);

  // 确认面板弹出后：聚焦 textarea 并把光标放到末尾，短句无需修改可直接回车上屏
  useEffect(() => {
    if (!confirming) return;
    const timer = setTimeout(() => {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        const len = el.value.length;
        el.setSelectionRange(len, len);
      }
    }, 120); // 等窗口 setSize/setFocus 完成
    return () => clearTimeout(timer);
  }, [confirming]);
  useEffect(() => {
    if (!confirming) return;
    const el = textareaRef.current;
    const panel = panelRef.current;
    const win = winRef.current;
    if (!el || !panel || !win) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 40), 240)}px`;
    const raf = requestAnimationFrame(async () => {
      const h = Math.min(panel.scrollHeight + 8, 520);
      await win.setSize(new LogicalSize(HUD_W, h)).catch(() => {});
      const wa = waRef.current;
      if (wa) {
        const cx = Math.round((wa.left + wa.right) / 2 - HUD_W / 2);
        const sf = await win.scaleFactor().catch(() => 1);
        const y = Math.max(0, wa.bottom - Math.round(h * sf) - 12);
        win
          .setPosition(new PhysicalPosition(cx, y))
          .catch((e) => logBridge(`adaptive: setPosition FAIL ${String(e)}`));
      } else {
        logBridge("adaptive: wa null, skip positioning");
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [confirming, confirmText]);

  const fadeOut = useCallback(() => {
    setVisible(false); // CSS 0.5s 淡出
    hideTimer.current = setTimeout(() => {
      winRef.current?.hide().catch(() => {});
      setPartial("");
    }, 550);
  }, []);

  /** 恢复胶囊模式（确认完成/取消/收到后续状态时） */
  const exitConfirm = useCallback(() => {
    setConfirming(false);
  }, []);

  /** 「翻译并上屏」：把用户确认/修改后的原文提交给后端继续翻译 */
  const submitConfirm = useCallback(async () => {
    const ok = await confirmSpeech(confirmText, confirmSource);
    exitConfirm();
    if (!ok) {
      setMessage(""); // 后端会广播 error/idle，胶囊按状态显示
    }
  }, [confirmText, confirmSource, exitConfirm]);

  /** 「取消」：放弃本次翻译上屏 */
  const cancelConfirm = useCallback(async () => {
    await confirmSpeech("", confirmSource, true);
    exitConfirm();
  }, [confirmSource, exitConfirm]);

  // 独立 WS 连接：接收全部广播事件
  useEffect(() => {
    let disposed = false;
    const client = new SpeechClient({
      onOpen: () => {},
      onClose: () => {},
      onEvent: (ev: SpeechEvent) => {
        if (disposed) return;
        switch (ev.type) {
          case "status":
            setState(ev.state);
            setMessage(ev.message);
            if (ev.state === "listening") {
              showHud();
            } else if (
              ev.state === "transcribing" ||
              ev.state === "polishing" ||
              ev.state === "confirming" ||
              ev.state === "committing"
            ) {
              showHud();
            } else if (ev.state === "idle" || ev.state === "error") {
              exitConfirm();
              fadeOut();
            }
            break;
          case "partial":
            setPartial(ev.text);
            break;
          case "confirm":
            // 转写完成、等待确认原文：切换到可编辑确认面板
            setConfirmText(ev.text);
            setConfirmSource(ev.source);
            setConfirmTarget(ev.target_lang ?? "");
            setConfirming(true);
            void syncFromBackend();
            showHud();
            break;
          case "commit":
            exitConfirm();
            setMessage(t("hud.committed"));
            break;
          default:
            break;
        }
      },
    });
    client.connect().catch(() => {});
    return () => {
      disposed = true;
      client.close();
    };
  }, [showHud, fadeOut, exitConfirm, t, syncFromBackend]);

  const isActive =
    state === "listening" ||
    state === "transcribing" ||
    state === "polishing" ||
    state === "committing";

  const stateText =
    state === "listening"
      ? t("hud.recording")
      : state === "transcribing"
        ? t("hud.transcribing")
        : state === "polishing"
          ? t("hud.polishing")
          : state === "confirming"
            ? t("hud.confirming")
            : state === "committing"
              ? t("hud.finalizing")
              : state === "error"
                ? t("hud.error")
                : t("hud.idle");

  // 右侧文字内容：优先实时识别文字；无则显示当前状态消息
  const streamText = partial || (isActive ? message : "");

  // 确认面板：转写完成、需先确认/修改原文再翻译
  if (confirming) {
    return (
      <div className="flex h-screen w-screen items-start justify-center bg-transparent">
        <div
          ref={panelRef}
          className="w-full rounded-2xl border border-accent/20 bg-card/90 p-4 shadow-[inset_0_1px_1px_rgba(255,255,255,0.1)]"
        >
          {/* 顶部固定行：标题 + 目标 + 操作按钮 */}
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-[13px] font-bold text-text">
                {t("hud.confirm_title")}
              </span>
              {confirmTarget && (
                <span className="shrink-0 rounded-md border border-accent/30 bg-accent/10 px-2 py-0.5 text-[11.5px] text-accent2">
                  → {targetLangLabel(confirmTarget, lang)}
                </span>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                onClick={cancelConfirm}
                className="rounded-lg border border-border bg-input px-4 py-1.5 text-[12.65px] text-mid transition-colors hover:border-accent hover:text-text"
              >
                {t("hud.confirm_cancel")}
              </button>
              <button
                onClick={submitConfirm}
                className="rounded-lg bg-accent px-4 py-1.5 text-[12.65px] font-bold text-bg transition-colors hover:bg-accent2"
              >
                {t("hud.confirm_submit")}
              </button>
            </div>
          </div>
          {/* 中间：可编辑原文（绿色圆角框，随文本自适应，最低一行字） */}
          <textarea
            ref={textareaRef}
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            onKeyDown={(e) => {
              // 纯回车 → 直接翻译上屏；Ctrl+Enter → 换行；Esc → 取消
              if (
                e.key === "Enter" &&
                !e.ctrlKey &&
                !e.metaKey &&
                !e.shiftKey &&
                !e.altKey
              ) {
                e.preventDefault();
                void submitConfirm();
              } else if (e.key === "Escape") {
                e.preventDefault();
                void cancelConfirm();
              }
            }}
            autoFocus
            className="min-h-[40px] max-h-[240px] w-full resize-none overflow-y-auto rounded-lg border border-border bg-input px-3 py-2 text-[13px] leading-relaxed text-text outline-none transition-colors focus:border-accent"
          />
          {/* 底部固定行：快捷键提示（跟随界面语言） */}
          <div className="mt-1.5 text-center text-[10.5px] text-accent/50">
            {t("hud.confirm_hint")}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-screen items-start justify-center bg-transparent">
      {/* 玻璃拟态胶囊容器 */}
      <div
        className={`mt-1 flex max-w-[400px] items-center gap-3 rounded-full border border-accent/20 bg-card/75 px-5 py-2.5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.1)] transition-opacity duration-500 ${
          visible ? "opacity-100" : "opacity-0"
        }`}
      >
        {/* 声波脉冲指示器（替代闪烁绿点） */}
        <span className="flex h-3.5 shrink-0 items-end gap-[3px]">
          {WAVE_BARS.map((b, i) => (
            <span
              key={i}
              className={`w-0.5 origin-bottom rounded-full transition-colors duration-300 ${
                isActive
                  ? "animate-[hud-wave_1.1s_ease-in-out_infinite] bg-accent2 shadow-[0_0_6px_var(--t-glow-strong)]"
                  : "bg-border"
              }`}
              style={{ height: `${b.h}px`, animationDelay: b.delay }}
            />
          ))}
        </span>

        {/* 状态文字（呼吸灯：亮度 + 翡翠光晕脉动） */}
        <span
          className={`shrink-0 whitespace-nowrap text-[13.8px] font-bold text-text ${
            isActive ? "animate-[hud-breathe_1.8s_ease-in-out_infinite]" : ""
          }`}
        >
          {stateText}
        </span>

        {/* 中间半透明竖线分隔 */}
        <span className="h-4 w-px shrink-0 bg-accent2/40" />

        {/* 右侧实时文字：平滑滚动跟随最新 + 左端渐变隐退 */}
        <div
          ref={textRef}
          className="scrollbar-none min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-[12.65px] text-accent2/90 [mask-image:linear-gradient(to_right,transparent_0%,black_18%)]"
          style={{
            scrollBehavior: "smooth",
            willChange: "transform",
            transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
          }}
        >
          {streamText || "　"}
        </div>
      </div>
    </div>
  );
}
