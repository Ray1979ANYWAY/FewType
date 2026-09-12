/**
 * 自定义快捷键弹窗（对应 Tk 版 open_stt_hotkey_dialog）
 * - 两种模式：自定义组合键（按键捕获预览）/ 双击长按 Control
 * - 组合键必须至少包含一个修饰键（Ctrl / Alt / Win / Shift）
 */
import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { getConfig, updateConfig } from "../api";
import { Modal, Btn } from "./ui";
import { useI18n } from "../i18n";

function hotkeyDisplay(combo: string): string {
  if ((combo || "").trim().toLowerCase() === "double_ctrl") return "Double Ctrl (Hold)";
  const names: Record<string, string> = {
    ctrl: "CTRL",
    alt: "ALT",
    win: "Win",
    shift: "Shift",
    space: "Space",
  };
  return (combo || "ctrl+win")
    .split("+")
    .filter(Boolean)
    .map((p) => names[p.trim().toLowerCase()] ?? p.trim())
    .join("+");
}

const MODIFIER_KEYS = ["ctrl", "alt", "win", "shift"];

export default function HotkeyDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [mode, setMode] = useState<"combo" | "double_ctrl">("double_ctrl");
  const [combo, setCombo] = useState("ctrl+win");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!open) return;
    setMsg("");
    getConfig()
      .then((cfg) => {
        const cur = (cfg.stt_hotkey || "ctrl+win").trim().toLowerCase();
        setCombo(cur === "double_ctrl" ? "double_ctrl" : cur);
        setMode(cur === "double_ctrl" ? "double_ctrl" : "combo");
      })
      .catch((e) => setMsg(t("hotkey.load_failed", { msg: (e as Error).message })));
  }, [open, t]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (mode !== "combo") return;
    e.preventDefault();
    const mods: string[] = [];
    if (e.ctrlKey) mods.push("ctrl");
    if (e.altKey) mods.push("alt");
    if (e.metaKey) mods.push("win");
    if (e.shiftKey) mods.push("shift");
    const key = e.key.toLowerCase();
    const bareMods = ["control", "alt", "meta", "shift"];
    let main = "";
    if (!bareMods.includes(key) && key !== " " && key.length === 1) {
      main = key;
    } else if (key === " ") {
      main = "space";
    }
    // 排除裸修饰键
    const comboStr = [...mods, ...(main ? [main] : [])].join("+");
    if (comboStr) setCombo(comboStr);
  };

  const handleSave = async () => {
    if (mode === "combo") {
      const hasMod = MODIFIER_KEYS.some((m) => combo.includes(m));
      if (!hasMod) {
        setMsg(t("hotkey.need_modifier"));
        return;
      }
    }
    try {
      const value = mode === "double_ctrl" ? "double_ctrl" : combo.trim().toLowerCase();
      await updateConfig({ stt_hotkey: value });
      setMsg(`✅ ${t("hotkey.saved")}`);
      setTimeout(onClose, 400);
    } catch (e) {
      setMsg(`❌ ${t("hotkey.save_failed", { msg: (e as Error).message })}`);
    }
  };

  const cardCls = (active: boolean) =>
    `rounded-xl border p-3 transition-colors ${
      active ? "border-accent/70 bg-input" : "border-border bg-bg"
    }`;

  return (
    <Modal open={open} onClose={onClose} title={t("hotkey.title")} width={440}>
      <p className="mb-3 text-[12.65px] text-muted">{t("hotkey.hint")}</p>

      <div className="flex flex-col gap-2">
        {/* 模式 1：自定义组合键 */}
        <div className={cardCls(mode === "combo")}>
          <label className="flex items-center gap-2 text-[13.8px] text-text">
            <input
              type="radio"
              checked={mode === "combo"}
              onChange={() => setMode("combo")}
              className="accent-accent"
            />
            {t("hotkey.mode_combo")}
          </label>
          <div className="mt-2 flex justify-end">
            <input
              value={hotkeyDisplay(combo)}
              readOnly
              disabled={mode !== "combo"}
              onKeyDown={onKeyDown}
              placeholder={t("hotkey.mode_combo_placeholder")}
              className={`w-[180px] rounded-xl border bg-[#121A15] px-3 py-2 text-center text-[16.1px] font-bold text-accent2 outline-none ${
                mode === "combo"
                  ? "border-accent focus:border-accent2"
                  : "border-border/40 text-muted"
              }`}
            />
          </div>
          {mode === "combo" ? (
            <p className="mt-1.5 text-right text-[11.5px] text-muted">{t("hotkey.mode_combo_note")}</p>
          ) : null}
        </div>

        {/* 模式 2：双击长按 Control */}
        <div className={cardCls(mode === "double_ctrl")}>
          <label className="flex items-center gap-2 text-[13.8px] text-text">
            <input
              type="radio"
              checked={mode === "double_ctrl"}
              onChange={() => setMode("double_ctrl")}
              className="accent-accent"
            />
            {t("hotkey.mode_double")}
          </label>
          <p className="mt-1 pl-6 text-[12.65px] text-muted">{t("hotkey.mode_double_note")}</p>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between">
        <span className="text-[12.65px] text-mid">{msg}</span>
        <Btn variant="primary" onClick={handleSave}>
          <Save size={12} />
          {t("hotkey.save")}
        </Btn>
      </div>
    </Modal>
  );
}
