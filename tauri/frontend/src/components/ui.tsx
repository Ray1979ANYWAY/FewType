/**
 * FewType 通用 UI 组件（Moss Black 主题）
 * 现代暗黑质感：圆角卡片、Switch 开关、精致下拉框、分段控件
 */
import React, { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

/* ---------------------------------------------------------------- 卡片 */
export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl border border-border bg-card px-5 py-4 ${className}`}
    >
      {children}
    </div>
  );
}

export function CardTitle({
  icon,
  children,
  className = "",
}: {
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex items-center gap-2 mb-3 ${className}`}>
      {icon}
      <span className="text-[14.95px] font-bold text-text">{children}</span>
    </div>
  );
}

/* ---------------------------------------------------------------- 开关 */
export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex items-center gap-2.5 select-none group ${
        disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"
      }`}
    >
      <span
        className={`relative h-[20px] w-[36px] shrink-0 rounded-full transition-colors duration-200 ${
          checked ? "bg-accent" : "bg-input border border-border"
        }`}
      >
        <span
          className={`absolute top-[2px] left-[2px] h-4 w-4 rounded-full bg-white shadow transition-transform duration-200 ${
            checked ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </span>
      {label ? (
        <span className="text-[13.8px] text-mid group-hover:text-text transition-colors">
          {label}
        </span>
      ) : null}
    </button>
  );
}

/* ---------------------------------------------------------------- 下拉框 */
export function Select<T extends string>({
  value,
  onChange,
  options,
  placeholder,
  className = "",
  disabled = false,
  title,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <div className={`relative inline-flex items-center ${className}`}>
      <select
        value={value}
        disabled={disabled}
        title={title}
        onChange={(e) => onChange(e.target.value as T)}
        className={`w-full min-w-0 appearance-none rounded-lg border border-border bg-input px-3 py-1.5 pr-7 text-[13.8px] text-text outline-none transition-colors focus:border-accent disabled:opacity-40 ${
          value === "" ? "text-muted" : ""
        }`}
      >
        {placeholder ? (
          <option value="" disabled>
            {placeholder}
          </option>
        ) : null}
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-card text-text">
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-accent2" />
    </div>
  );
}

/** 可编辑下拉（Combobox）：既可从阶梯选项选择，也可手动输入任意值；下拉为自绘深色菜单（与全局配色一致）。
 * editable=false 时等价于纯下拉（只读 input + 自绘深色菜单），与可编辑版视觉完全一致。 */
export function EditableSelect({
  value,
  onChange,
  options,
  onBlur,
  className = "",
  disabled = false,
  title = "",
  editable = true,
  menuAlign = "left",
  placement = "bottom",
  placeholder = "",
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  onBlur?: () => void;
  className?: string;
  disabled?: boolean;
  title?: string;
  editable?: boolean;
  menuAlign?: "left" | "right";
  placement?: "top" | "bottom";
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // 非编辑模式：输入框显示选中项对应的翻译 label（下拉选项已随界面语言翻译，选中值必须一致显示）
  const displayValue = editable
    ? value
    : (options.find((o) => o.value === value)?.label ?? value);

  // 点击组件外部时收起菜单
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div
      ref={wrapRef}
      className={`relative inline-flex items-center ${className}`}
      // 点击组件任意位置都打开菜单（mousedown 比 focus 可靠；菜单选项点击时 open 仍为 true，不会重复触发）
      onMouseDown={() => {
        if (!disabled && !open) setOpen(true);
      }}
    >
      <input
        type="text"
        value={displayValue}
        disabled={disabled}
        title={title}
        placeholder={placeholder}
        onChange={(e) => {
          // 非编辑模式（editable=false）忽略键盘输入，仅保留点击聚焦打开菜单
          if (editable) onChange(e.target.value);
        }}
        onFocus={() => setOpen(true)}
        onBlur={onBlur}
        className={`w-full min-w-0 appearance-none rounded-lg border border-border bg-input px-3 py-1.5 pr-7 text-[13.8px] text-text outline-none transition-colors focus:border-accent disabled:opacity-40 ${
          value === "" ? "text-muted" : ""
        }`}
      />
      <button
        type="button"
        tabIndex={-1}
        disabled={disabled}
        onClick={() => setOpen(true)}
        className="pointer-events-auto absolute right-0 top-0 flex h-full w-6 items-center justify-center text-accent2 transition-colors hover:text-accent"
        title="Select"
      >
        <ChevronDown className="h-3.5 w-3.5" />
      </button>
      {open ? (
        <div className={`absolute z-50 ${placement === "top" ? "bottom-full mb-1" : "top-full mt-1"} min-w-full w-max max-h-64 ${menuAlign === "right" ? "right-0" : "left-0"} overflow-y-auto rounded-lg border border-border bg-card py-1 shadow-2xl`}>
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              onMouseDown={(e) => {
                e.preventDefault(); // 阻止 input 失焦，避免触发 onBlur 规范化
                onChange(o.value);
                setOpen(false);
              }}
              className={`block w-full whitespace-nowrap px-3 py-1.5 text-left text-[13.8px] transition-colors ${
                o.value === value
                  ? "bg-accent/15 text-accent2"
                  : "text-text hover:bg-input"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- 可编辑下拉框 */
export function ComboInput<T extends string>({
  value,
  onChange,
  options,
  placeholder,
  className = "",
  disabled = false,
}: {
  value: T;
  onChange: (v: T) => void;
  options: string[];
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}) {
  const listId = React.useId();
  return (
    <div className={`relative ${className}`}>
      <input
        list={listId}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as T)}
        placeholder={placeholder}
        className={`w-full rounded-lg border border-border bg-input px-3 py-1.5 pr-7 text-[13.8px] text-text outline-none transition-colors focus:border-accent disabled:opacity-40 placeholder:text-muted ${
          value === "" ? "text-muted" : ""
        }`}
      />
      <datalist id={listId}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </div>
  );
}

/* ---------------------------------------------------------------- 分段控件 */
export function Segmented<T extends string>({
  value,
  onChange,
  items,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: string }[];
}) {
  return (
    <div className="inline-flex gap-1 rounded-xl border border-border bg-input p-1">
      {items.map((it) => {
        const active = value === it.value;
        return (
          <button
            key={it.value}
            type="button"
            onClick={() => onChange(it.value)}
            className={`rounded-lg px-3.5 py-1.5 text-[13.8px] transition-all duration-150 ${
              active
                ? "bg-accent font-bold text-bg shadow-[0_0_12px_var(--t-glow-btn)]"
                : "text-mid hover:text-text"
            }`}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- 按钮 */
export function Btn({
  children,
  onClick,
  variant = "ghost",
  className = "",
  disabled = false,
  title,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger";
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const styles: Record<string, string> = {
    primary:
      "bg-accent text-bg font-bold hover:bg-accent2 shadow-[0_0_14px_var(--t-glow-btn)]",
    ghost: "bg-input text-mid border border-border hover:border-accent hover:text-text",
    danger: "bg-input text-warn border border-border hover:border-warn",
  };
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13.8px] transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed ${styles[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/* ---------------------------------------------------------------- 弹窗容器 */
export function Modal({
  open,
  title,
  onClose,
  children,
  width = 520,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  width?: number;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-[2px]"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="flex max-h-[86vh] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_8px_40px_rgba(0,0,0,0.6)]"
        style={{ width }}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3">
          <span className="text-[14.95px] font-bold text-text">{title}</span>
          <button
            type="button"
            onClick={onClose}
            className="flex h-6 w-6 items-center justify-center rounded-md text-muted hover:bg-input hover:text-text"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- 状态点 */
export function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span className="relative inline-flex h-2 w-2">
      {ok ? (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
      ) : null}
      <span
        className={`relative inline-flex h-2 w-2 rounded-full ${
          ok ? "bg-accent shadow-[0_0_6px_var(--color-accent)]" : "bg-warn"
        }`}
      />
    </span>
  );
}
