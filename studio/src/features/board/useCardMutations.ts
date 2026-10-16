import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { cardsApi, type CardEditBody, type NewCardBody } from "../../api";
import { queryKeys } from "../../api/queries";
import type { TaskThreadDetail } from "../../lib/runEvents.gen";
import { useRunStoreApi } from "../../stores/runStoreContext";
import type { CardWire } from "../../types";

// Human card operations. None of them changes a column: columns move only from TASK_* signals.
// Replies are folded into the run store through `seedCards` (priority and new cards) and the
// thread arrives as a TASK_THREAD signal on the websocket.

/** Add a card (`POST /cards`); the response seeds the store so it shows before the signal lands. */
export function useAddCard(runId: string): UseMutationResult<CardWire, Error, NewCardBody> {
  const store = useRunStoreApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NewCardBody) => cardsApi.add(runId, body),
    onSuccess: (card) => {
      // The thread (the "added by a human" note) arrives as a TASK_THREAD signal; seeding it
      // as well would show it twice. Skip when the TASK_ASSIGNED signal already beat the response.
      if (!store.getState().cards[card.id]) store.getState().seedCards([{ ...card, thread: [] }]);
      void qc.invalidateQueries({ queryKey: queryKeys.cards(runId) });
    },
  });
}

interface PriorityVars {
  taskId: string;
  priority: number;
}

/** Set a card's priority (`PATCH /cards/{id}`). */
export function useSetPriority(runId: string): UseMutationResult<CardWire, Error, PriorityVars> {
  const store = useRunStoreApi();
  return useMutation({
    mutationFn: ({ taskId, priority }: PriorityVars) => cardsApi.setPriority(runId, taskId, priority),
    onSuccess: (card) => store.getState().seedCards([card]),
  });
}

interface CommentVars {
  taskId: string;
  text: string;
}

/** Comment on a card (`POST /cards/{id}/comments`). The entry returns as a TASK_THREAD signal. */
export function useCommentOnCard(
  runId: string,
): UseMutationResult<TaskThreadDetail, Error, CommentVars> {
  return useMutation({
    mutationFn: ({ taskId, text }: CommentVars) => cardsApi.comment(runId, taskId, text),
  });
}

/** Edit a card that has not started (`PATCH /cards/{id}`, worker context plan F2). */
export function useEditCard(runId: string): UseMutationResult<CardWire, Error, { taskId: string; body: CardEditBody }> {
  const store = useRunStoreApi();
  return useMutation({
    mutationFn: ({ taskId, body }) => cardsApi.edit(runId, taskId, body),
    onSuccess: (card) => store.getState().seedCards([card]),
  });
}
