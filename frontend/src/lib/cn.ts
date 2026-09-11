import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Class names where a later conflicting utility wins (`cn('h-8', 'h-7')` → `h-7`), so props can override variants. */
export const cn = (...inputs: ClassValue[]): string => twMerge(clsx(inputs))
