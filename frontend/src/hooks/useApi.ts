/**
 * Custom React Hook for API Calls
 * Manages loading, error, and data states
 */

import { useEffect, useState, useCallback } from 'react';
import { apiRequest, get, post, put, delete_ } from '../api/client';

interface UseApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

interface UseApiOptions {
  skip?: boolean;
  dependencies?: unknown[];
}

/**
 * Hook for GET requests with automatic loading
 */
export function useGet<T = unknown>(
  endpoint: string,
  options?: UseApiOptions
): UseApiState<T> & { refetch: () => Promise<void> } {
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  const fetchData = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    const response = await get<T>(endpoint);

    if (response.error) {
      setState({
        data: null,
        loading: false,
        error: response.error,
      });
    } else {
      setState({
        data: response.data || null,
        loading: false,
        error: null,
      });
    }
  }, [endpoint]);

  useEffect(() => {
    if (!options?.skip) {
      fetchData();
    }
  }, options?.dependencies ?? [endpoint]);

  return { ...state, refetch: fetchData };
}

/**
 * Hook for manual API calls (POST, PUT, DELETE)
 */
export function useApiCall<T = unknown>() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const call = useCallback(
    async (
      method: 'POST' | 'PUT' | 'DELETE',
      endpoint: string,
      body?: unknown
    ): Promise<T | null> => {
      setLoading(true);
      setError(null);

      let response;
      switch (method) {
        case 'POST':
          response = await post<T>(endpoint, body);
          break;
        case 'PUT':
          response = await put<T>(endpoint, body);
          break;
        case 'DELETE':
          response = await delete_<T>(endpoint);
          break;
      }

      if (response.error) {
        setError(response.error);
        setLoading(false);
        return null;
      }

      setLoading(false);
      return response.data || null;
    },
    []
  );

  return { call, loading, error };
}
