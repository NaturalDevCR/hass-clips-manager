import { shallowRef } from "vue";
import { apiFetch, setCsrfToken } from "@/composables/useApi";
import type { CollectionSummary } from "@/types";

interface SessionPayload {
  csrf: string;
  worker_version: string;
  order_bridge_capability: string;
}

const csrf = shallowRef("");
const workerVersion = shallowRef("");
const collections = shallowRef<CollectionSummary[]>([]);

export function useSession() {
  async function refresh(): Promise<SessionPayload> {
    const payload = await apiFetch<SessionPayload>("manager/session");
    csrf.value = payload.csrf;
    workerVersion.value = payload.worker_version;
    setCsrfToken(payload.csrf);
    return payload;
  }

  async function load(): Promise<void> {
    await refresh();
    collections.value = await apiFetch<CollectionSummary[]>("manager/collections");
  }

  /**
   * The bridge capability expires five minutes after it is minted, so it is
   * fetched at the moment of use rather than cached from page load.
   */
  async function orderCapability(): Promise<string> {
    return (await refresh()).order_bridge_capability;
  }

  return { csrf, workerVersion, collections, load, orderCapability };
}
