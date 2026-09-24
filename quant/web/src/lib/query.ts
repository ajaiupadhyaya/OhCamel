/**
 * React Query setup + the two hooks pages should use.
 *
 *   const q = useApiQuery<Overview>("/market/overview", { universe });           // GET
 *   const r = useApiPost<RiskOut>("/risk/var", body, { enabled: body != null }); // POST, cached by body
 *
 * POST endpoints in this API are pure computations, so they are modelled as *queries*
 * keyed on the request body (identical inputs are served from cache; changing inputs
 * refetches). Pass the returned query straight to <Panel query={q}> for loading/error UI.
 */
import { QueryClient, keepPreviousData, useQuery, type UseQueryOptions, type UseQueryResult } from "@tanstack/react-query";
import { ApiError, api, type QueryParams } from "./api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60_000, // market data is daily; 5 min is plenty
      gcTime: 30 * 60_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => {
        // Never retry deterministic failures (4xx, 503 data-unavailable).
        if (err instanceof ApiError && err.status > 0) return false;
        return count < 1;
      },
    },
  },
});

type Extra<T, D = T> = Omit<UseQueryOptions<T, ApiError, D>, "queryKey" | "queryFn">;

/**
 * GET query. `T` is the wire type; pass `select` to transform (result type `D`).
 * Previous data is kept while new params load (Panel shows a thin progress bar);
 * pass `placeholderData: undefined` to opt out.
 */
export function useApiQuery<T, D = T>(path: string, params?: QueryParams, options?: Extra<T, D>): UseQueryResult<D, ApiError> {
  return useQuery<T, ApiError, D>({
    queryKey: ["GET", path, params ?? {}],
    queryFn: ({ signal }) => api.get<T>(path, params, signal),
    placeholderData: keepPreviousData,
    ...options,
  });
}

export function useApiPost<T, D = T>(path: string, body: unknown, options?: Extra<T, D> & { params?: QueryParams }): UseQueryResult<D, ApiError> {
  const { params, ...rest } = options ?? {};
  return useQuery<T, ApiError, D>({
    queryKey: ["POST", path, body ?? null, params ?? {}],
    queryFn: ({ signal }) => api.post<T>(path, body, params, signal),
    placeholderData: keepPreviousData,
    staleTime: 30 * 60_000,
    ...rest,
  });
}
