<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import ClipCard from "@/components/ClipCard.vue";
import ClipDrawer from "@/components/ClipDrawer.vue";
import ClipTable from "@/components/ClipTable.vue";
import { useClips } from "@/composables/useClips";
import { useSession } from "@/composables/useSession";

const {
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
} = useClips();
const { collections } = useSession();

const LAYOUT_KEY = "manager-layout";
const layout = ref<"grid" | "table">("grid");
const error = ref("");
// The drawer tracks an id rather than an object, so a catalog reload keeps it
// pointed at the live record instead of a stale copy.
const openClipId = ref<string | null>(null);
const openClip = computed(() => clips.value.find((clip) => clip.id === openClipId.value) ?? null);

async function reload(): Promise<void> {
  try {
    await load();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function rememberLayout(next: "grid" | "table"): void {
  layout.value = next;
  try {
    localStorage.setItem(LAYOUT_KEY, next);
  } catch {
    // Private windows and blocked site data make storage throw; the choice
    // simply does not survive a reload.
  }
}

onMounted(async () => {
  try {
    const stored = localStorage.getItem(LAYOUT_KEY);
    if (stored === "grid" || stored === "table") layout.value = stored;
  } catch {
    // Same as above: fall back to the default layout.
  }
  try {
    await load();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
});
</script>

<template>
  <section class="flex flex-col gap-4">
    <div
      class="sticky -top-5 z-10 -mx-5 -mt-5 flex flex-wrap items-center gap-2 border-b border-line bg-ground/95 px-5 py-3 backdrop-blur"
    >
      <label class="flex-1 min-w-52">
        <span class="sr-only">Search clips by filename</span>
        <input v-model="query" class="field" type="search" placeholder="Search filenames…" />
      </label>
      <label>
        <span class="sr-only">Filter by collection</span>
        <select v-model="collectionFilter" class="field">
          <option value="">Every collection</option>
          <option v-for="entry in collections" :key="entry.id" :value="entry.id">
            {{ entry.name }}
          </option>
        </select>
      </label>
      <label>
        <span class="sr-only">Filter by state</span>
        <select v-model="stateFilter" class="field">
          <option value="">Any state</option>
          <option v-for="state in states" :key="state" :value="state">{{ state }}</option>
        </select>
      </label>
      <label>
        <span class="sr-only">Sort clips</span>
        <select v-model="sortKey" class="field">
          <option value="name">Name</option>
          <option value="duration">Duration</option>
          <option value="state">State</option>
        </select>
      </label>
      <div class="flex overflow-hidden rounded-lg border border-line" role="group" aria-label="Layout">
        <button
          type="button"
          class="px-3 py-2 text-sm"
          :class="layout === 'grid' ? 'bg-accent text-accent-ink' : 'text-muted hover:bg-hover'"
          :aria-pressed="layout === 'grid'"
          @click="rememberLayout('grid')"
        >
          Cards
        </button>
        <button
          type="button"
          class="px-3 py-2 text-sm"
          :class="layout === 'table' ? 'bg-accent text-accent-ink' : 'text-muted hover:bg-hover'"
          :aria-pressed="layout === 'table'"
          @click="rememberLayout('table')"
        >
          Table
        </button>
      </div>
    </div>

    <p v-if="error" role="alert" class="rounded-panel bg-danger/15 p-3 text-sm text-danger">
      {{ error }}
    </p>

    <p class="text-xs text-muted">
      {{ filtered.length }} of {{ clips.length }} clips
      <span v-if="selected.size"> · {{ selected.size }} selected</span>
    </p>

    <p v-if="!clips.length" class="panel text-sm text-muted">
      No catalogued clips yet. Add some from the Import section.
    </p>
    <p v-else-if="!filtered.length" class="panel text-sm text-muted">
      No clips match these filters.
    </p>

    <div
      v-else-if="layout === 'grid'"
      class="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(15rem,1fr))]"
    >
      <ClipCard
        v-for="clip in filtered"
        :key="clip.id"
        :clip="clip"
        :selected="selected.has(clip.id)"
        @toggle="toggle"
        @open="openClipId = $event.id"
      />
    </div>
    <ClipTable
      v-else
      :clips="filtered"
      :selected="selected"
      @toggle="toggle"
      @open="openClipId = $event.id"
    />

    <ClipDrawer
      v-if="openClip"
      :clip="openClip"
      @close="openClipId = null"
      @changed="reload"
    />
  </section>
</template>
