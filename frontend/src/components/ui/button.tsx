import { cva, type VariantProps } from 'class-variance-authority';
import { useId, type ButtonHTMLAttributes, type Ref } from 'react';
import { cn } from '@/lib/utils';

export const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-2 whitespace-normal break-words text-center rounded-[var(--radius-sm)] border text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 cursor-pointer select-none',
  {
    variants: {
      variant: {
        primary:
          'border-accent-strong bg-accent text-accent-fg hover:bg-accent-strong hover:border-accent',
        secondary: 'border-line-strong bg-raised text-fg hover:border-fg/60 hover:bg-line',
        ghost: 'border-transparent bg-transparent text-muted hover:bg-raised hover:text-fg',
        danger: 'border-bad/40 bg-bad/10 text-bad hover:bg-bad/20 hover:border-bad',
        outline: 'border-line-strong bg-transparent text-fg hover:border-fg/60',
      },
      size: {
        sm: 'min-h-11 px-2.5 py-2 text-xs',
        md: 'min-h-11 px-3.5 py-2',
        lg: 'min-h-11 px-5 py-2',
        icon: 'min-h-11 min-w-11 p-0',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  ref?: Ref<HTMLButtonElement>;
  disabledReason?: string;
}

export function Button({ className, variant, size, type = 'button', ref, disabledReason, ...props }: ButtonProps) {
  const id = useId();
  const reason = props.disabled ? disabledReason : undefined;
  return (
    <>
      <button ref={ref} type={type} data-variant={variant ?? 'secondary'} className={cn(buttonVariants({ variant, size }), className)} {...props} aria-describedby={[props['aria-describedby'], reason ? id : ''].filter(Boolean).join(' ') || undefined} />
      {reason && <span id={id} className="max-w-64 text-xs font-normal text-muted">{reason}</span>}
    </>
  );
}
