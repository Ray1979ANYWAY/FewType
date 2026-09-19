/**
 * 自定义风格管理弹窗（对应 Tk 版 open_stt_styles_dialog）
 * 左侧：风格列表（最多 5 个，行内重命名 / 删除 / 新增）
 * 右侧：Prompt 积木编辑（保存时写入配置 stt_styles）
 */
import { useEffect, useState } from "react";
import { Pencil, Trash2, Plus, Save } from "lucide-react";
import { getConfig, updateConfig, SttStyle } from "../api";
import { Modal, Btn } from "./ui";
import { useI18n } from "../i18n";

const MAX_STYLES = 5;

export default function StyleManager({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const { t } = useI18n();
  const [styles, setStyles] = useState<SttStyle[]>([]);
  const [selected, setSelected] = useState(-1);
  const [editing, setEditing] = useState(-1);
  const [nameDraft, setNameDraft] = useState("");
  const [prompt, setPrompt] = useState("");
  const [msg, setMsg] = useState("");
  const [customStyle, setCustomStyle] = useState(""); // 当前选中风格名（stt_custom_style）
  const [pendingRename, setPendingRename] = useState<{ old: string; next: string } | null>(null);

  // 打开时加载配置
  useEffect(() => {
    if (!open) return;
    setMsg("");
    setPendingRename(null);
    getConfig()
      .then((cfg) => {
        const list = (cfg.stt_styles ?? []).map((s) => ({ ...s }));
        setStyles(list);
        setCustomStyle(cfg.stt_custom_style ?? "");
        // 自动选中：优先当前选中风格；否则选第一个，让右侧直接显示 prompt
        const names = list.map((s) => s.name);
        const idx = names.indexOf(cfg.stt_custom_style ?? "");
        if (idx >= 0) {
          setSelected(idx);
          setPrompt(list[idx]?.prompt ?? "");
        } else if (list.length > 0) {
          setSelected(0);
          setPrompt(list[0]?.prompt ?? "");
        } else {
          setSelected(-1);
          setPrompt("");
        }
        setEditing(-1);
      })
      .catch((e) => setMsg(t("style.load_failed", { msg: (e as Error).message })));
  }, [open, t]);

  const select = (i: number) => {
    setSelected(i);
    setEditing(-1);
    setPrompt(styles[i]?.prompt ?? "");
  };

  const startEdit = (i: number) => {
    setEditing(i);
    setNameDraft(styles[i]?.name ?? "");
  };

  const commitName = (i: number) => {
    const name = nameDraft.trim();
    setEditing(-1);
    if (i >= styles.length) return;
    const next = [...styles];
    if (name) {
      // 重命名：若改的正是当前选中风格，记录新旧名，保存时同步 stt_custom_style
      const old = next[i].name;
      if (old && old !== name && old === customStyle) {
        setPendingRename({ old, next: name });
      }
      next[i] = { ...next[i], name };
      setStyles(next);
    } else if (!next[i].name) {
      // 新建但未命名：取消该条目
      next.splice(i, 1);
      setStyles(next);
      if (selected === i) {
        setSelected(-1);
        setPrompt("");
      }
    }
  };

  const addStyle = () => {
    if (styles.length >= MAX_STYLES) {
      setMsg(t("style.max", { n: MAX_STYLES }));
      return;
    }
    const next = [...styles, { name: "", prompt: "" }];
    setStyles(next);
    setEditing(next.length - 1);
    setNameDraft("");
    setSelected(-1);
    setPrompt("");
  };

  const delStyle = (i: number) => {
    if (!window.confirm(t("style.confirm_delete"))) return;
    const next = styles.filter((_, idx) => idx !== i);
    setStyles(next);
    if (selected === i) {
      setSelected(-1);
      setPrompt("");
    } else if (selected > i) {
      setSelected(selected - 1);
    }
  };

  const handleSave = async () => {
    const next = [...styles];
    if (selected >= 0 && selected < next.length) {
      next[selected] = { ...next[selected], prompt: prompt.trim() };
    }
    setStyles(next);
    const patch: { stt_styles: SttStyle[]; stt_custom_style?: string } = {
      stt_styles: next,
    };
    // 当前选中风格被重命名 → 同步 stt_custom_style，避免语音输入面板脱钩
    if (pendingRename && pendingRename.old === customStyle && pendingRename.next) {
      patch.stt_custom_style = pendingRename.next;
      setCustomStyle(pendingRename.next);
    } else if (customStyle && !next.some((s) => s.name === customStyle)) {
      // 当前选中风格被删除 → 回退到第一个或清空
      patch.stt_custom_style = next.length > 0 && next[0].name ? next[0].name : "";
      setCustomStyle(patch.stt_custom_style);
    }
    setPendingRename(null);
    try {
      await updateConfig(patch);
      setMsg(`✅ ${t("style.saved")}`);
      onSaved?.();
      setTimeout(onClose, 400);
    } catch (e) {
      setMsg(`❌ ${t("style.save_failed", { msg: (e as Error).message })}`);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={t("style.title")} width={760}>
      <div className="flex gap-4">
        {/* 左侧：风格列表 */}
        <div className="w-[220px] shrink-0">
          <p className="mb-2 text-[12.65px] text-mid">
            {t("style.list", { n: MAX_STYLES })}
          </p>
          <div className="flex flex-col gap-1.5">
            {Array.from({ length: MAX_STYLES }).map((_, i) => {
              const s = styles[i];
              if (!s) {
                // 空槽位：加号
                return (
                  <div
                    key={`empty-${i}`}
                    className="flex items-center gap-1 rounded-lg border border-dashed border-border px-2 py-1.5"
                  >
                    <span className="flex-1" />
                    <button
                      type="button"
                      title={t("style.add")}
                      onClick={addStyle}
                      className="flex h-6 w-6 items-center justify-center rounded-md bg-input text-mid hover:text-accent2"
                    >
                      <Plus size={13} />
                    </button>
                  </div>
                );
              }
              const isEdit = editing === i;
              const isSel = selected === i;
              return (
                <div
                  key={`style-${i}`}
                  className={`flex items-center gap-1 rounded-lg border px-2 py-1.5 ${
                    isSel
                      ? "border-accent/60 bg-input"
                      : "border-border bg-card"
                  }`}
                >
                  {isEdit ? (
                    <input
                      autoFocus
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      onBlur={() => commitName(i)}
                      onKeyDown={(e) => e.key === "Enter" && commitName(i)}
                      placeholder={t("style.name_placeholder")}
                      className="w-full bg-transparent text-[13.8px] text-text outline-none placeholder:text-muted"
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={() => select(i)}
                      title={s.name || t("style.unnamed")}
                      className="flex-1 truncate text-left text-[13.8px] text-text hover:text-accent2"
                    >
                      {s.name === "Karwai Wong" ? t("voice.style_karwai") : s.name || t("style.unnamed")}
                    </button>
                  )}
                  <button
                    type="button"
                    title={t("style.rename")}
                    onClick={() => startEdit(i)}
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-input text-mid hover:text-accent2"
                  >
                    <Pencil size={12} />
                  </button>
                  <button
                    type="button"
                    title={t("style.delete")}
                    onClick={() => delStyle(i)}
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-input text-mid hover:text-destructive"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {/* 右侧：Prompt 编辑 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <p className="mb-2 text-[12.65px] text-mid">
            {t("style.prompt_label")}
          </p>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={
              selected >= 0 && selected < styles.length
                ? t("style.prompt_placeholder_sel")
                : t("style.prompt_placeholder_none")
            }
            className="h-[200px] w-full resize-none rounded-xl border border-border bg-input px-3 py-2 font-mono text-[12.65px] leading-relaxed text-text outline-none placeholder:text-muted focus:border-accent"
          />
          <p className="mt-2 text-[11.5px] text-muted">
            {t("style.prompt_note")}
          </p>
          <div className="mt-3 flex items-center justify-between">
            <span className="text-[12.65px] text-mid">{msg}</span>
            <Btn variant="primary" onClick={handleSave}>
              <Save size={12} />
              {t("style.save")}
            </Btn>
          </div>
        </div>
      </div>
    </Modal>
  );
}
