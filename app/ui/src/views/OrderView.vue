<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  bridgeGetOrder,
  bridgeSaveOrder,
  deterministicClipCompare,
  moveId,
} from "@/composables/useOrder";
import { useClips } from "@/composables/useClips";
import { useSession } from "@/composables/useSession";
import { copyText, formatDuration, sourceName } from "@/lib/format";
import type { Clip } from "@/types";

const { clips, load } = useClips();
const { collections, orderCapability } = useSession();

const collection = ref("");
const orderIds = ref<string[]>([]);
const bridgeAvailable = ref(false);
const status = ref("");
const isError = ref(false);
const busy = ref(false);
const list = ref<HTMLUListElement | null>(null);
// Ids the saved order references but the Worker no longer catalogs.
const missing = ref(new Map<string, Clip>());

const options = computed(() => {
  if (collections.value.length > 0) return collections.value;
  return [...new Set(clips.value.map((clip) => clip.collection_id))]
    .sort()
    .map((id) => ({ id, name: id }));
});

const catalogOrder = computed(() =>
  clips.value.filter((clip) => clip.collection_id === collection.value).sort(deterministicClipCompare),
);

const byId = computed(() => {
  const map = new Map(catalogOrder.value.map((clip) => [clip.id, clip]));
  missing.value.forEach((clip, id) => {
    if (!map.has(id)) map.set(id, clip);
  });
  return map;
});

const rows = computed(() =>
  orderIds.value.map((id) => byId.value.get(id)).filter((clip): clip is Clip => Boolean(clip)),
);

function say(message: string, failed = false): void {
  status.value = message;
  isError.value = failed;
}

function placeholder(id: string): Clip {
  return {
    id,
    collection_id: collection.value,
    relative_source_path: "Not in the current Worker catalog",
    relative_output_path: "",
    output_available: false,
    state: "unavailable",
    duration_seconds: 0,
    sequential_rank: Number.MAX_SAFE_INTEGER,
    tags: [],
    notes: "",
    failed_reason: null,
  };
}

async function loadOrder(): Promise<void> {
  if (!collection.value) return;
  missing.value = new Map();
  orderIds.value = catalogOrder.value.map((clip) => clip.id);
  let capability = "";
  try {
    capability = await orderCapability();
  } catch {
    bridgeAvailable.value = false;
    say(
      "The Home Assistant bridge capability could not be refreshed, so Save order is disabled. " +
        "Use Copy IDs and paste the list into the collection's Playback order settings.",
      true,
    );
    return;
  }
  const bridge = await bridgeGetOrder(collection.value, capability);
  bridgeAvailable.value = bridge.available;
  if (bridge.available && bridge.ids.length > 0) {
    const known = new Set(catalogOrder.value.map((clip) => clip.id));
    const unknown = new Map<string, Clip>();
    bridge.ids.forEach((id) => {
      if (!known.has(id)) unknown.set(id, placeholder(id));
    });
    missing.value = unknown;
    const rest = catalogOrder.value.map((clip) => clip.id).filter((id) => !bridge.ids.includes(id));
    orderIds.value = [...bridge.ids, ...rest];
    say("Loaded the saved custom order from Home Assistant.");
    return;
  }
  if (bridge.available) {
    say("No saved custom order; showing the deterministic path order.");
    return;
  }
  const multiple = bridge.error?.includes("Multiple Cinema Collections entries");
  say(
    "The Home Assistant bridge is unavailable here, so Save order is disabled. " +
      (multiple
        ? "Multiple Cinema Collections entries are loaded; select the target entry in Home " +
          "Assistant, then use Copy IDs for manual configuration."
        : "Use Copy IDs and paste the list into the collection's Playback order settings."),
    true,
  );
}

function reorder(from: number, to: number, focus = false): void {
  if (to < 0 || to >= orderIds.value.length || from === to) return;
  orderIds.value = moveId(orderIds.value, from, to);
  if (!focus) return;
  requestAnimationFrame(() => {
    const moved = list.value?.children[to];
    moved?.querySelector<HTMLButtonElement>(".order-handle")?.focus();
  });
}

function onHandleKeydown(event: KeyboardEvent, index: number): void {
  const target = event.key === "ArrowUp" ? index - 1 : event.key === "ArrowDown" ? index + 1 : -1;
  if (target < 0 || target >= orderIds.value.length) return;
  event.preventDefault();
  reorder(index, target, true);
}

function onDragStart(event: DragEvent, id: string): void {
  event.dataTransfer?.setData("text/plain", id);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
}

function onDrop(event: DragEvent, id: string): void {
  event.preventDefault();
  const dragged = event.dataTransfer?.getData("text/plain");
  if (!dragged) return;
  const from = orderIds.value.indexOf(dragged);
  const to = orderIds.value.indexOf(id);
  if (from < 0 || to < 0 || from === to) return;
  orderIds.value = moveId(orderIds.value, from, to);
}

async function save(): Promise<void> {
  busy.value = true;
  try {
    // The capability expires five minutes after it is minted, so it is taken
    // fresh here rather than reused from when the view loaded.
    const capability = await orderCapability();
    await bridgeSaveOrder(collection.value, orderIds.value, capability);
    say("Order saved to Home Assistant.");
  } catch (cause) {
    // Never claim a silent success: the local arrangement is intact and the
    // copy fallback is one click away.
    const message = cause instanceof Error ? cause.message : String(cause);
    say(
      `The order was not saved (${message}). Your arrangement is unchanged — use Copy IDs to keep it.`,
      true,
    );
  }
  busy.value = false;
}

async function copyIds(): Promise<void> {
  try {
    await copyText(orderIds.value.join("\n"));
    say(`Copied ${orderIds.value.length} clip IDs, one per line.`);
  } catch {
    say("Copy failed — select the IDs and copy them manually.", true);
  }
}

function reset(): void {
  missing.value = new Map();
  orderIds.value = catalogOrder.value.map((clip) => clip.id);
  say("Reset to the deterministic path order. Save to apply it.");
}

watch(collection, () => void loadOrder());

onMounted(async () => {
  if (clips.value.length === 0) await load();
  collection.value = options.value[0]?.id ?? "";
  if (!collection.value) {
    say("No collections yet. Add clips from the Import section first.");
  }
});
</script>

<template>
  <section class="flex flex-col gap-4">
    <header class="flex flex-col gap-2">
      <h2 class="text-lg font-semibold">Playback order</h2>
      <p class="max-w-2xl text-sm text-muted">
        Drag clips, or focus a drag handle and use the arrow keys, to arrange the custom playback
        order for one collection. Saving sends the order to Home Assistant; when the bridge is
        unavailable, use Copy IDs and paste the list into the collection's Playback order settings
        instead.
      </p>
    </header>

    <label class="flex items-center gap-2">
      <span class="text-sm text-muted">Collection</span>
      <select v-model="collection" class="field w-auto" :disabled="!options.length">
        <option v-for="entry in options" :key="entry.id" :value="entry.id">{{ entry.name }}</option>
      </select>
    </label>

    <ul ref="list" aria-label="Playback order" class="flex flex-col gap-1">
      <li
        v-for="(clip, index) in rows"
        :key="clip.id"
        draggable="true"
        class="flex items-center gap-3 rounded-lg border border-line bg-surface px-3 py-2 text-sm"
        @dragstart="onDragStart($event, clip.id)"
        @dragover.prevent
        @drop="onDrop($event, clip.id)"
      >
        <button
          type="button"
          class="order-handle cursor-grab rounded px-1 text-muted hover:text-ink"
          :aria-label="`Reorder ${sourceName(clip)}`"
          @keydown="onHandleKeydown($event, index)"
        >
          ⠿
        </button>
        <span class="w-6 text-xs text-muted">{{ index + 1 }}</span>
        <span class="min-w-0 flex-1 truncate" :title="clip.relative_source_path">
          {{ sourceName(clip) }}
        </span>
        <span class="text-xs text-muted">{{ formatDuration(clip.duration_seconds) }}</span>
        <span class="text-xs text-muted">{{ clip.state }}</span>
        <code class="max-w-40 truncate text-xs text-muted">{{ clip.id }}</code>
      </li>
    </ul>

    <p v-if="collection && !rows.length" class="panel text-sm text-muted">
      No catalogued clips for this collection yet.
    </p>

    <div class="flex flex-wrap gap-2">
      <button
        type="button"
        class="btn-primary"
        :disabled="busy || !bridgeAvailable || !orderIds.length"
        @click="save"
      >
        Save order
      </button>
      <button type="button" class="btn" :disabled="!orderIds.length" @click="copyIds">
        Copy IDs
      </button>
      <button type="button" class="btn" :disabled="!orderIds.length" @click="reset">
        Reset to path order
      </button>
    </div>

    <p
      role="status"
      aria-live="polite"
      class="text-sm"
      :class="isError ? 'text-danger' : 'text-muted'"
    >
      {{ status }}
    </p>
  </section>
</template>
