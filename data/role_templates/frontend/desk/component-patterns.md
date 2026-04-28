---
name: React Component Patterns
triggers: [component, react, jsx, tsx, render, props, hook]
priority: 5
kind: reference
tags: [frontend, react]
---

# React Component Patterns

## Component Rules
- One component per file, named same as file
- Co-locate styles, tests, and sub-components in the same directory
- Export named, not default (enables better tree-shaking and refactors)
- Prefer composition over configuration — avoid boolean prop explosion

## Props Discipline
```tsx
// Bad: boolean explosion
<Card loading error compact readonly />

// Good: variant + status
<Card variant="compact" status="error" />
```
Keep props minimal. If a component needs >6 props, split it.

## Custom Hooks
Extract complex state into `use*` hooks:
```tsx
// Component stays declarative
const { runs, isLoading, refresh } = useRuns(projectId);
```
Hooks own side effects; components own rendering.

## Optimistic Updates (required for all mutations)
```tsx
// 1. Update local state immediately
setItems(prev => [...prev, optimisticItem]);

// 2. Fire mutation
const created = await api.createItem(data);

// 3. Swap temp id for real one
setItems(prev => prev.map(i => i.id === tempId ? created : i));
```
Never wait for server response before updating UI.

## Memoization — use sparingly
- `useMemo` only when computation is measurably expensive
- `React.memo` only when profiling shows unnecessary re-renders
- Premature memoization adds cognitive overhead without benefit
