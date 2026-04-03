import type { KeyboardEventHandler, ReactNode } from "react";

import { cn } from "./cn";

type AppInputProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  prefix?: ReactNode;
  suffix?: ReactNode;
  multiline?: boolean;
  rows?: number;
  disabled?: boolean;
  onKeyDown?: KeyboardEventHandler<HTMLInputElement | HTMLTextAreaElement>;
};

function AppInput({
  value,
  onChange,
  placeholder,
  className,
  prefix,
  suffix,
  multiline = false,
  rows = 4,
  disabled = false,
  onKeyDown,
}: AppInputProps) {
  return (
    <div className={cn("app-input", className)}>
      <div className="app-input__shell">
        {prefix ? <span className="app-input__adornment">{prefix}</span> : null}
        {multiline ? (
          <textarea
            className="app-input__textarea"
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            rows={rows}
            value={value}
          />
        ) : (
          <input
            className="app-input__control"
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            value={value}
          />
        )}
        {suffix ? <span className="app-input__adornment">{suffix}</span> : null}
      </div>
    </div>
  );
}

export default AppInput;
