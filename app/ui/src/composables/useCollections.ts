import { shallowRef } from "vue";
import { apiFetch } from "@/composables/useApi";
import type { Collection, Profile } from "@/types";

const collections = shallowRef<Collection[]>([]);
const profiles = shallowRef<Profile[]>([]);

export function useCollections() {
  async function load(): Promise<void> {
    [collections.value, profiles.value] = await Promise.all([
      apiFetch<Collection[]>("manager/collections"),
      apiFetch<Profile[]>("manager/profiles"),
    ]);
  }

  async function createCollection(payload: Record<string, unknown>): Promise<Collection> {
    const record = await apiFetch<Collection>("manager/collections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await load();
    return record;
  }

  /**
   * Patches carry the revision they were built from, so a concurrent edit is
   * refused by the Worker rather than silently overwritten.
   */
  async function patchCollection(
    id: string,
    revision: number,
    changes: Record<string, unknown>,
  ): Promise<Collection> {
    const record = await apiFetch<Collection>(`manager/collections/${encodeURIComponent(id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "If-Match-Revision": String(revision) },
      body: JSON.stringify(changes),
    });
    await load();
    return record;
  }

  async function createProfile(payload: Record<string, unknown>): Promise<Profile> {
    const record = await apiFetch<Profile>("manager/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await load();
    return record;
  }

  async function patchProfile(
    id: string,
    revision: number,
    changes: Record<string, unknown>,
  ): Promise<Profile> {
    const record = await apiFetch<Profile>(`manager/profiles/${encodeURIComponent(id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "If-Match-Revision": String(revision) },
      body: JSON.stringify(changes),
    });
    await load();
    return record;
  }

  return {
    collections,
    profiles,
    load,
    createCollection,
    patchCollection,
    createProfile,
    patchProfile,
  };
}
