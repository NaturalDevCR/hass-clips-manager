<script setup lang="ts">
import StateBadge from "@/components/StateBadge.vue";
import { formatDuration, sourceName } from "@/lib/format";
import type { Clip } from "@/types";

defineProps<{ clip: Clip; selected: boolean }>();
defineEmits<{ open: [Clip]; toggle: [string] }>();
</script>

<template>
  <article
    class="flex flex-col gap-3 rounded-panel border bg-surface p-4 transition hover:bg-hover"
    :class="selected ? 'border-accent' : 'border-line hover:border-accent/50'"
  >
    <div class="flex items-start gap-3">
      <input
        type="checkbox"
        class="mt-1 size-4 accent-[var(--color-accent)]"
        :checked="selected"
        :aria-label="`Select ${sourceName(clip)}`"
        @change="$emit('toggle', clip.id)"
      />
      <button type="button" class="min-w-0 flex-1 text-left" @click="$emit('open', clip)">
        <p class="truncate font-medium" :title="clip.relative_source_path">
          {{ sourceName(clip) }}
        </p>
        <p class="truncate text-xs text-muted">{{ clip.collection_id }}</p>
      </button>
    </div>
    <p v-if="clip.failed_reason" class="text-xs text-danger">{{ clip.failed_reason }}</p>
    <div class="mt-auto flex flex-wrap items-center gap-2 text-xs text-muted">
      <StateBadge :state="clip.state" />
      <span>{{ formatDuration(clip.duration_seconds) }}</span>
      <span v-if="clip.output_available" class="text-ok" :title="clip.relative_output_path">
        Output ready
      </span>
      <span v-for="tag in clip.tags" :key="tag" class="rounded bg-raised px-1.5 py-0.5">
        {{ tag }}
      </span>
    </div>
  </article>
</template>
