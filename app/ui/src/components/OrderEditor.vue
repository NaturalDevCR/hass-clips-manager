<script setup lang="ts">
import { computed, ref } from "vue";
import { deterministicClipCompare, moveId } from "@/composables/useOrder";
import { formatDuration, sourceName } from "@/lib/format";
import type { Clip } from "@/types";

const props = defineProps<{ clips: Clip[]; modelValue: string[] }>();
const emit = defineEmits<{ "update:modelValue": [string[]] }>();

const list = ref<HTMLUListElement | null>(null);

const catalogOrder = computed(() => [...props.clips].sort(deterministicClipCompare));

const byId = computed(() => new Map(props.clips.map((clip) => [clip.id, clip])));

// An id the saved order references but the catalog no longer holds still gets a
// row, so saving cannot quietly drop it.
const rows = computed(() =>
  props.modelValue.map((id) => ({ id, clip: byId.value.get(id) ?? null })),
);

function reorder(from: number, to: number, focus = false): void {
  if (to < 0 || to >= props.modelValue.length || from === to) return;
  emit("update:modelValue", moveId(props.modelValue, from, to));
  if (!focus) return;
  requestAnimationFrame(() => {
    const moved = list.value?.children[to];
    moved?.querySelector<HTMLButtonElement>(".order-handle")?.focus();
  });
}

function onHandleKeydown(event: KeyboardEvent, index: number): void {
  const target = event.key === "ArrowUp" ? index - 1 : event.key === "ArrowDown" ? index + 1 : -1;
  if (target < 0 || target >= props.modelValue.length) return;
  event.preventDefault();
  reorder(index, target, true);
}

function onDrop(event: DragEvent, id: string): void {
  event.preventDefault();
  const dragged = event.dataTransfer?.getData("text/plain");
  if (!dragged) return;
  const from = props.modelValue.indexOf(dragged);
  const to = props.modelValue.indexOf(id);
  reorder(from, to);
}

function reset(): void {
  emit(
    "update:modelValue",
    catalogOrder.value.map((clip) => clip.id),
  );
}

function fill(): void {
  const known = new Set(props.modelValue);
  emit("update:modelValue", [
    ...props.modelValue,
    ...catalogOrder.value.map((clip) => clip.id).filter((id) => !known.has(id)),
  ]);
}
</script>

<template>
  <div class="flex flex-col gap-2">
    <p class="text-xs text-muted">
      Drag a clip, or focus its handle and use the arrow keys, to arrange the order.
    </p>
    <ul ref="list" aria-label="Playback order" class="flex flex-col gap-1">
      <li
        v-for="(row, index) in rows"
        :key="row.id"
        draggable="true"
        class="flex items-center gap-3 rounded-lg border border-line bg-ground px-3 py-2 text-sm"
        @dragstart="$event.dataTransfer?.setData('text/plain', row.id)"
        @dragover.prevent
        @drop="onDrop($event, row.id)"
      >
        <button
          type="button"
          class="order-handle cursor-grab rounded px-1 text-muted hover:text-ink"
          :aria-label="`Reorder ${row.clip ? sourceName(row.clip) : row.id}`"
          @keydown="onHandleKeydown($event, index)"
        >
          ⠿
        </button>
        <span class="w-6 text-xs text-muted">{{ index + 1 }}</span>
        <span
          class="min-w-0 flex-1 truncate"
          :class="!row.clip && 'text-muted italic'"
          :title="row.clip?.relative_source_path ?? row.id"
        >
          {{ row.clip ? sourceName(row.clip) : "Not in the current catalog" }}
        </span>
        <span v-if="row.clip" class="text-xs text-muted">
          {{ formatDuration(row.clip.duration_seconds) }}
        </span>
      </li>
    </ul>
    <p v-if="!rows.length" class="text-sm text-muted">
      No clips in this order yet. Add the catalogued clips to start from the path order.
    </p>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="btn" @click="fill">Add catalogued clips</button>
      <button type="button" class="btn" @click="reset">Reset to path order</button>
    </div>
  </div>
</template>
