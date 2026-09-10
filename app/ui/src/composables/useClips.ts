import { computed, ref, shallowRef } from "vue";
import { apiFetch } from "@/composables/useApi";
import { sourceName } from "@/lib/format";
import type { Clip } from "@/types";

export type SortKey = "name" | "duration" | "state";

const clips = shallowRef<Clip[]>([]);
const query = ref("");
const collectionFilter = ref("");
const stateFilter = ref("");
const sortKey = ref<SortKey>("name");
const selected = ref(new Set<string>());

export function useClips() {
  const filtered = computed(() => {
    const needle = query.value.trim().toLowerCase();
    const rows = clips.value.filter((clip) => {
      if (collectionFilter.value && clip.collection_id !== collectionFilter.value) return false;
      if (stateFilter.value && clip.state !== stateFilter.value) return false;
      if (needle && !sourceName(clip).toLowerCase().includes(needle)) return false;
      return true;
    });
    if (sortKey.value === "duration") {
      rows.sort((a, b) => a.duration_seconds - b.duration_seconds);
    } else if (sortKey.value === "state") {
      rows.sort(
        (a, b) => a.state.localeCompare(b.state) || sourceName(a).localeCompare(sourceName(b)),
      );
    } else {
      rows.sort((a, b) => sourceName(a).localeCompare(sourceName(b)));
    }
    return rows;
  });

  const states = computed(() => [...new Set(clips.value.map((clip) => clip.state))].sort());

  async function load(): Promise<void> {
    clips.value = await apiFetch<Clip[]>("manager/clips");
    // Drop selections whose clip left the catalog, so bulk actions never
    // address an id the Worker no longer knows.
    const live = new Set(clips.value.map((clip) => clip.id));
    selected.value = new Set([...selected.value].filter((id) => live.has(id)));
  }

  function toggle(id: string): void {
    const next = new Set(selected.value);
    if (!next.delete(id)) next.add(id);
    selected.value = next;
  }

  function selectAll(ids: readonly string[]): void {
    selected.value = new Set(ids);
  }

  function clearSelection(): void {
    selected.value = new Set();
  }

  return {
    clips,
    query,
    collectionFilter,
    stateFilter,
    sortKey,
    selected,
    filtered,
    states,
    load,
    toggle,
    selectAll,
    clearSelection,
  };
}
