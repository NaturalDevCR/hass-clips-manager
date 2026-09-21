<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { apiFetch } from "@/composables/useApi";
import { jobIdFrom, useJobs } from "@/composables/useJobs";
import { applyAspectRatio, clampToBounds, toSourceRect } from "@/lib/cropMath";
import type { CropRect, Size } from "@/lib/cropMath";
import { formatDuration, sourceName } from "@/lib/format";
import type { ActionResult, Clip } from "@/types";

const props = defineProps<{ clip: Clip }>();
const emit = defineEmits<{ close: []; changed: [] }>();

const { follow } = useJobs();

const ASPECT_PRESETS: { label: string; ratio: number | null }[] = [
  { label: "Free", ratio: null },
  { label: "16:9", ratio: 16 / 9 },
  { label: "9:16", ratio: 9 / 16 },
  { label: "1:1", ratio: 1 },
  { label: "4:3", ratio: 4 / 3 },
];

const video = ref<HTMLVideoElement | null>(null);
const closeButton = ref<HTMLButtonElement | null>(null);

const duration = computed(() => props.clip.duration_seconds || 0);
const trimStart = ref(0);
const trimEnd = ref(duration.value);
const cropEnabled = ref(false);
const aspectRatio = ref<number | null>(null);
const displaySize = ref<Size>({ width: 0, height: 0 });
const naturalSize = ref<Size>({ width: 0, height: 0 });
const cropRect = ref<CropRect>({ x: 0, y: 0, width: 0, height: 0 });
const metadataLoaded = computed(() => naturalSize.value.width > 0 && naturalSize.value.height > 0);

const status = ref("");
const failure = ref("");
const busy = ref(false);
const previewVersion = ref(0);
const previewError = ref(false);

const sourceUrl = computed(() => `manager/clips/${props.clip.id}/source?v=${previewVersion.value}`);

function resetCropRect(): void {
  const { width, height } = displaySize.value;
  cropRect.value = clampToBounds(
    { x: width * 0.1, y: height * 0.1, width: width * 0.8, height: height * 0.8 },
    { width, height },
  );
}

function updateDisplaySize(): void {
  const element = video.value;
  if (!element) return;
  const rect = element.getBoundingClientRect();
  displaySize.value = { width: rect.width, height: rect.height };
  if (cropRect.value.width === 0) resetCropRect();
}

function onLoadedMetadata(): void {
  const element = video.value;
  if (!element) return;
  naturalSize.value = { width: element.videoWidth, height: element.videoHeight };
  updateDisplaySize();
}

function onVideoError(): void {
  previewError.value = true;
}

function toggleCrop(next: boolean): void {
  cropEnabled.value = next;
  if (next && cropRect.value.width === 0) resetCropRect();
}

function pickAspect(ratio: number | null): void {
  aspectRatio.value = ratio;
  if (ratio !== null) {
    cropRect.value = applyAspectRatio(cropRect.value, ratio, displaySize.value);
  }
}

function seekTo(seconds: number): void {
  if (video.value) video.value.currentTime = seconds;
}

function onTrimStartInput(value: number): void {
  trimStart.value = Math.min(value, trimEnd.value - 0.1);
  seekTo(trimStart.value);
}

function onTrimEndInput(value: number): void {
  trimEnd.value = Math.max(value, trimStart.value + 0.1);
  seekTo(trimEnd.value);
}

type DragMode = "move" | "resize";
let dragMode: DragMode | null = null;
let dragOrigin = { x: 0, y: 0 };
let dragStartRect: CropRect = { x: 0, y: 0, width: 0, height: 0 };

function startDrag(event: PointerEvent, mode: DragMode): void {
  dragMode = mode;
  dragOrigin = { x: event.clientX, y: event.clientY };
  dragStartRect = { ...cropRect.value };
  (event.target as HTMLElement).setPointerCapture(event.pointerId);
}

function onDragMove(event: PointerEvent): void {
  if (dragMode === null) return;
  const deltaX = event.clientX - dragOrigin.x;
  const deltaY = event.clientY - dragOrigin.y;
  if (dragMode === "move") {
    cropRect.value = clampToBounds(
      { ...dragStartRect, x: dragStartRect.x + deltaX, y: dragStartRect.y + deltaY },
      displaySize.value,
    );
    return;
  }
  const proposed: CropRect = {
    ...dragStartRect,
    width: Math.max(20, dragStartRect.width + deltaX),
    height: Math.max(20, dragStartRect.height + deltaY),
  };
  cropRect.value =
    aspectRatio.value !== null
      ? applyAspectRatio(proposed, aspectRatio.value, displaySize.value)
      : clampToBounds(proposed, displaySize.value);
}

function endDrag(): void {
  dragMode = null;
}

async function apply(): Promise<void> {
  busy.value = true;
  failure.value = "";
  status.value = "Encoding…";
  try {
    const crop = cropEnabled.value
      ? toSourceRect(cropRect.value, displaySize.value, naturalSize.value)
      : null;
    const response = await apiFetch<ActionResult>(`manager/clips/${props.clip.id}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trim_start_seconds: trimStart.value,
        trim_end_seconds: trimEnd.value,
        crop,
      }),
    });
    const jobId = jobIdFrom(response?.details);
    if (jobId) {
      const job = await follow(jobId, (update) => {
        const percent = Math.round(update.progress?.percent ?? 0);
        status.value = `${update.progress?.stage ?? update.state} ${percent}%`;
      });
      if (job === null) {
        status.value = "";
        failure.value =
          "Lost track of the edit job — check the System view's job list for its outcome before trying again.";
        return;
      }
      if (job.state === "failed") throw new Error(job.error || "Edit failed.");
    }
    previewVersion.value += 1;
    status.value = "Clip updated.";
    emit("changed");
  } catch (cause) {
    failure.value = cause instanceof Error ? cause.message : String(cause);
    status.value = "";
  } finally {
    busy.value = false;
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}

let resizeObserver: ResizeObserver | null = null;

onMounted(async () => {
  document.addEventListener("keydown", onKeydown);
  await nextTick();
  closeButton.value?.focus();
  if (video.value) {
    resizeObserver = new ResizeObserver(() => updateDisplaySize());
    resizeObserver.observe(video.value);
  }
});

onUnmounted(() => {
  document.removeEventListener("keydown", onKeydown);
  resizeObserver?.disconnect();
});

watch(
  () => props.clip.id,
  () => {
    trimStart.value = 0;
    trimEnd.value = duration.value;
    cropEnabled.value = false;
    aspectRatio.value = null;
    previewVersion.value = 0;
    previewError.value = false;
  },
);
</script>

<template>
  <div class="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4">
    <div
      role="dialog"
      aria-modal="true"
      :aria-label="`Trim and crop ${sourceName(clip)}`"
      class="flex max-h-full w-full max-w-3xl flex-col gap-4 overflow-y-auto rounded-panel border border-line bg-surface p-4"
    >
      <header class="flex items-start justify-between gap-3">
        <div>
          <h2 class="font-semibold">Trim / Crop — {{ sourceName(clip) }}</h2>
          <p class="text-xs text-muted">{{ formatDuration(duration) }} original length</p>
        </div>
        <button ref="closeButton" type="button" class="btn px-2 py-1" @click="emit('close')">
          Close
        </button>
      </header>

      <p v-if="failure" role="alert" class="rounded-lg bg-danger/15 p-3 text-sm text-danger">
        {{ failure }}
      </p>
      <p v-else-if="status" role="status" class="text-sm text-muted">{{ status }}</p>
      <p v-if="previewError" role="alert" class="rounded-lg bg-danger/15 p-3 text-sm text-danger">
        Preview unavailable — the source file may be missing.
      </p>

      <div
        class="relative mx-auto w-full max-w-xl select-none"
        @pointermove="onDragMove"
        @pointerup="endDrag"
        @pointercancel="endDrag"
      >
        <video
          ref="video"
          :src="sourceUrl"
          class="w-full rounded-lg bg-black"
          controls
          muted
          @loadedmetadata="onLoadedMetadata"
          @error="onVideoError"
        />
        <div
          v-if="cropEnabled"
          class="absolute border-2 border-accent bg-accent/10"
          :style="{
            left: `${cropRect.x}px`,
            top: `${cropRect.y}px`,
            width: `${cropRect.width}px`,
            height: `${cropRect.height}px`,
          }"
          @pointerdown="startDrag($event, 'move')"
        >
          <div
            class="absolute right-0 bottom-0 size-4 translate-x-1/2 translate-y-1/2 cursor-nwse-resize rounded-full bg-accent"
            @pointerdown.stop="startDrag($event, 'resize')"
          />
        </div>
      </div>

      <section class="flex flex-col gap-2">
        <h3 class="text-xs tracking-widest text-muted uppercase">Trim</h3>
        <label class="flex items-center gap-2 text-sm">
          <span class="w-12 text-muted">Start</span>
          <input
            type="range"
            min="0"
            :max="duration"
            step="0.1"
            :value="trimStart"
            class="flex-1"
            @input="onTrimStartInput(Number(($event.target as HTMLInputElement).value))"
          />
          <span class="w-14 text-right text-xs text-muted">{{ formatDuration(trimStart) }}</span>
        </label>
        <label class="flex items-center gap-2 text-sm">
          <span class="w-12 text-muted">End</span>
          <input
            type="range"
            min="0"
            :max="duration"
            step="0.1"
            :value="trimEnd"
            class="flex-1"
            @input="onTrimEndInput(Number(($event.target as HTMLInputElement).value))"
          />
          <span class="w-14 text-right text-xs text-muted">{{ formatDuration(trimEnd) }}</span>
        </label>
      </section>

      <section class="flex flex-col gap-2">
        <h3 class="text-xs tracking-widest text-muted uppercase">Crop</h3>
        <label class="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            :checked="cropEnabled"
            :disabled="!metadataLoaded"
            @change="toggleCrop(($event.target as HTMLInputElement).checked)"
          />
          Crop this clip
        </label>
        <p v-if="!metadataLoaded" class="text-xs text-muted">Loading preview…</p>
        <div v-if="cropEnabled" class="flex flex-wrap gap-2">
          <button
            v-for="preset in ASPECT_PRESETS"
            :key="preset.label"
            type="button"
            class="btn px-2 py-1 text-xs"
            :class="aspectRatio === preset.ratio && 'border-accent text-accent'"
            @click="pickAspect(preset.ratio)"
          >
            {{ preset.label }}
          </button>
        </div>
      </section>

      <div class="flex justify-end gap-2">
        <button type="button" class="btn" :disabled="busy" @click="emit('close')">Cancel</button>
        <button type="button" class="btn-primary" :disabled="busy || previewError" @click="apply">
          Apply
        </button>
      </div>
    </div>
  </div>
</template>
