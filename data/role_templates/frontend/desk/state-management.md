---
name: State Management Patterns
triggers: [state, store, zustand, context, redux, global, cache, query]
priority: 5
kind: reference
tags: [frontend, state]
---

# State Management Patterns

## State Ownership Hierarchy
1. **Server state** — use React Query / SWR: caching, invalidation, background refresh
2. **Global UI state** — use Zustand: theme, sidebar open, active run id
3. **Local UI state** — `useState`: form values, hover, open/closed
4. **URL state** — search params: filters, selected tab, page

Don't reach for global state until the data genuinely needs to cross multiple component trees.

## React Query Patterns
```tsx
// Prefer query keys as arrays — enables targeted invalidation
const { data } = useQuery({
  queryKey: ["runs", projectId],
  queryFn: () => api.listRuns(projectId),
  staleTime: 30_000,
});

// After mutation, invalidate only what changed
queryClient.invalidateQueries({ queryKey: ["runs", projectId] });
```

## Zustand Slice Pattern
```ts
interface RunSlice {
  activeRunId: string | null;
  setActiveRun: (id: string) => void;
}
```
One slice per domain. Keep actions next to state — avoid action files.

## WebSocket + Query Sync
For live data (board events, quips):
1. Open WebSocket subscription
2. On message, update React Query cache directly via `queryClient.setQueryData`
3. Don't poll — WebSocket IS the invalidation

## Avoid
- Prop drilling past 2 levels — lift to context or global store
- Storing derived data in state — compute with `useMemo`
- Multiple sources of truth for the same data
