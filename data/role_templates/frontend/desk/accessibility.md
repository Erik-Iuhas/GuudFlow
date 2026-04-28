---
name: Accessibility Patterns
triggers: [accessibility, a11y, aria, screen reader, keyboard, focus, wcag]
priority: 4
kind: reference
tags: [frontend, accessibility]
---

# Accessibility Patterns

## Semantic HTML First
Use the right element before reaching for ARIA:
- `<button>` for clickable actions (not `<div onClick>`)
- `<a href>` for navigation
- `<nav>`, `<main>`, `<aside>`, `<header>` for landmarks
- `<h1>`–`<h6>` in document order (no skipping levels)

## ARIA Rules
- Never use ARIA to override native semantics unnecessarily
- `aria-label` for icon buttons with no visible text
- `aria-live="polite"` for status updates (run progress, toast messages)
- `aria-live="assertive"` only for critical errors that need immediate attention
- `aria-busy="true"` on loading regions

## Keyboard Navigation
- All interactive elements reachable via Tab
- Custom components (dropdown, dialog) must implement full keyboard pattern
- `Escape` closes modals, dropdowns, popovers
- Arrow keys navigate within composite widgets (menus, listboxes)
- Visible focus indicator — never `outline: none` without a custom style

## Focus Management
```tsx
// Trap focus inside modal
useEffect(() => {
  dialogRef.current?.focus();
  return () => triggerRef.current?.focus(); // restore on close
}, [isOpen]);
```

## Color & Contrast
- Text contrast ratio ≥ 4.5:1 (normal), ≥ 3:1 (large)
- Never rely on color alone to convey status — add icon or text
- Test with forced colors (Windows High Contrast) and dark mode

## Testing
- `axe-core` / `jest-axe` for automated checks in unit tests
- Manual keyboard walkthrough before shipping interactive components
