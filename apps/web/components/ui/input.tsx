import * as React from "react";
import { cn } from "@/lib/utils";

export const inputClass =
  "h-7 w-full min-w-0 rounded border border-border-strong bg-bg px-2 text-sm text-fg placeholder:text-fg-faint focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-ring disabled:opacity-50";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(inputClass, className)} {...props} />,
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(inputClass, "h-auto min-h-16 py-1.5 leading-5", className)}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export function NativeSelect({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(inputClass, "pr-6", className)} {...props} />;
}
