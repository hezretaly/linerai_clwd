import type { QueryClient, QueryKey } from '@tanstack/react-query'

/**
 * One definition of "move the cache the instant a mutation fires" -- a
 * `Switch` reads `checked` straight off query data, and without this it does
 * not move until the PATCH round-trips and the query refetches, which on a
 * slow connection reads as a laggy, unresponsive toggle. Team's Out switch
 * and five more across Inventory and Assistant all need the identical
 * onMutate/onError pair (snapshot, write, roll back on failure), and a
 * hand-rolled copy at each call site is exactly how one of them ends up
 * missing the rollback -- a toggle that flips back to the server's answer on
 * success but sticks in the wrong state forever on a failed request.
 *
 * Options to spread into a `useMutation` call so its effect on the cache is
 * visible the instant the mutation fires, not after the round trip. Rolls
 * back to the pre-mutate snapshot if the request fails; a caller's own
 * `onSuccess` (unaffected by this helper) is still what reconciles with the
 * real server response.
 */
export function optimisticPatch<TData, TVars>(
  queryClient: QueryClient,
  queryKey: QueryKey,
  apply: (old: TData, vars: TVars) => TData,
) {
  return {
    onMutate: async (vars: TVars) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<TData>(queryKey)
      if (previous !== undefined) {
        queryClient.setQueryData<TData>(queryKey, apply(previous, vars))
      }
      return { previous }
    },
    onError: (_err: unknown, _vars: TVars, context: { previous?: TData } | undefined) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(queryKey, context.previous)
      }
    },
  }
}
