/**
 * 设置视图
 * - 通用偏好：界面语言 / 开机自启
 * - 语音服务配置入口（ProviderDialog）
 * - 自定义快捷键入口（HotkeyDialog）
 * - 关于（AboutDialog）
 */
import { useEffect, useState } from "react";
import { Mic, Info, Palette } from "lucide-react";
import { getConfig, updateConfig, AppConfig } from "../api";
import { Card, CardTitle, Switch, Select, Btn } from "./ui";
import { useI18n } from "../i18n";

const UI_LANGS = ["简体中文", "繁體中文", "English"];

export default function Settings({
  onOpenProvider,
  onOpenHotkey,
  onOpenAbout,
}: {
  onOpenProvider: () => void;
  onOpenHotkey: () => void;
  onOpenAbout: () => void;
}) {
  const { t, setLang: setI18nLang } = useI18n();
  const [lang, setLang] = useState("简体中文");
  const [autostart, setAutostart] = useState(false);
  const [providerSummary, setProviderSummary] = useState("");
  const [hotkey, setHotkey] = useState("ctrl+win");
  const [status, setStatus] = useState("");
  const [loaded, setLoaded] = useState(false);

  const refresh = () => {
    getConfig()
      .then((cfg: AppConfig) => {
        setLang(cfg.ui_lang === "zh-TW" ? "繁體中文" : cfg.ui_lang === "en-US" ? "English" : "简体中文");
        setAutostart(!!cfg.autostart);
        const p = cfg.provider;
        setProviderSummary(
          p ? `${p.platform} · ASR ${p.asr_model} · LLM ${p.llm_model}` : t("settings.not_configured")
        );
        setHotkey((cfg.stt_hotkey || "ctrl+win").trim().toLowerCase() === "double_ctrl" ? t("voice.double_ctrl") : cfg.stt_hotkey || "ctrl+win");
        setLoaded(true);
      })
      .catch((e) => setStatus(t("settings.load_failed", { msg: (e as Error).message })));
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setPref = async (patch: Partial<AppConfig>) => {
    try {
      await updateConfig(patch);
      setStatus("✅ " + t("settings.saved"));
      refresh();
    } catch (e) {
      setStatus(`❌ ${(e as Error).message}`);
    }
  };

  const onLangChange = (v: string) => {
    setLang(v);
    const map: Record<string, string> = {
      简体中文: "zh-CN",
      繁體中文: "zh-TW",
      English: "en-US",
    };
    const code = map[v] ?? "zh-CN";
    // 立即切换界面语言（本地 + 后端）
    setI18nLang(code as "zh-CN" | "zh-TW" | "en-US");
    void setPref({ ui_lang: code });
  };

  return (
    <div className="flex flex-col gap-3">
      {/* 偏好卡片 */}
      <Card>
        <CardTitle icon={<Palette size={15} className="text-accent2" />}>
          {t("settings.general")}
        </CardTitle>
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <span className="text-[13.8px] text-mid">{t("settings.ui_lang")}</span>
            <Select value={lang} onChange={onLangChange} options={UI_LANGS.map((l) => ({ value: l, label: l }))} />
          </div>
          <div className="flex items-center justify-between">
            <Switch
              checked={autostart}
              onChange={(v) => void setPref({ autostart: v })}
              label={t("settings.autostart")}
            />
          </div>
        </div>
      </Card>

      {/* 服务与功能入口 */}
      <Card>
        <CardTitle icon={<Mic size={15} className="text-accent2" />}>
          {t("settings.service")}
        </CardTitle>
        <div className="flex flex-col gap-2.5">
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <p className="text-[13.8px] text-text">{t("settings.provider")}</p>
              <p className="truncate text-[12.65px] text-muted">{loaded ? providerSummary : t("settings.loading")}</p>
            </div>
            <Btn variant="ghost" onClick={onOpenProvider} className="shrink-0">
              {t("settings.advanced")}
            </Btn>
          </div>
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <p className="text-[13.8px] text-text">{t("settings.hotkey")}</p>
              <p className="text-[12.65px] text-muted">{hotkey}</p>
            </div>
            <Btn variant="ghost" onClick={onOpenHotkey} className="shrink-0">
              {t("settings.customize")}
            </Btn>
          </div>
        </div>
      </Card>

      {/* 关于 */}
      <Card className="flex items-center justify-between">
        <span className="text-[13.8px] text-mid">{t("app.title")}</span>
        <Btn variant="ghost" onClick={onOpenAbout}>
          <Info size={12} />
          {t("settings.about")}
        </Btn>
      </Card>

      {status ? <p className="text-[12.65px] text-mid">{status}</p> : null}
    </div>
  );
}
