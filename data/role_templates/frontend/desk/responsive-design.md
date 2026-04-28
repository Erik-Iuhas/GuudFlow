---
name: Responsive Design Patterns
triggers: [responsive, mobile, breakpoint, layout, grid, flex, viewport]
priority: 4
kind: reference
tags: [frontend, css, layout]
---

# Responsive Design Patterns

## Breakpoint System (Tailwind defaults)
- `sm`: 640px — large phones
- `md`: 768px — tablets
- `lg`: 1024px — laptops
- `xl`: 1280px — desktops
- `2xl`: 1536px — wide screens

Mobile-first: base styles are mobile, breakpoints add complexity.

## Layout Primitives
```tsx
// Stack (vertical rhythm)
<div className="flex flex-col gap-4">

// Cluster (horizontal wrap)
<div className="flex flex-wrap gap-2">

// Sidebar (sidebar + main, collapses to stack)
<div className="flex flex-col lg:flex-row gap-6">
  <aside className="w-full lg:w-64 shrink-0">
  <main className="flex-1 min-w-0">  // min-w-0 prevents overflow
```

## Container Queries (prefer over breakpoints for components)
```css
.card-container { container-type: inline-size; }
@container (min-width: 400px) {
  .card { flex-direction: row; }
}
```
Components should respond to their container, not the viewport.

## Image Handling
- Always specify `width` and `height` to prevent layout shift
- Use `srcset` for art direction at different sizes
- `loading="lazy"` for below-fold images
- SVG for icons — never PNG at multiple sizes

## Touch Targets
- Minimum 44×44px tap target on mobile
- Add padding rather than increasing visual size
- Avoid hover-only interactions — no `:hover` tooltips without `:focus` equivalent
