import type { ComponentProps } from "react";
import { navigateLink } from "@/lib/navigation";

export function ConsoleLink({
  onClick,
  disabled,
  ...props
}: ComponentProps<"a"> & { disabled?: boolean }) {
  return (
    <a
      {...props}
      aria-disabled={disabled || props["aria-disabled"]}
      tabIndex={disabled ? -1 : props.tabIndex}
      onClick={(event) => {
        if (disabled) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
        navigateLink(event);
      }}
    />
  );
}
