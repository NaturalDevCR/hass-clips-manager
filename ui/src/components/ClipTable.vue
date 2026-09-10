<script setup lang="ts">
import StateBadge from "@/components/StateBadge.vue";
import { formatDuration, sourceName } from "@/lib/format";
import type { Clip } from "@/types";

defineProps<{ clips: Clip[]; selected: Set<string> }>();
defineEmits<{ open: [Clip]; toggle: [string] }>();
</script>

<template>
  <div class="overflow-x-auto rounded-panel border border-line">
    <table class="w-full min-w-[48rem] border-collapse text-sm">
      <thead class="bg-surface text-left text-xs tracking-wide text-muted uppercase">
        <tr>
          <th class="w-10 px-3 py-2"><span class="sr-only">Select</span></th>
          <th class="px-3 py-2">Source</th>
          <th class="px-3 py-2">Collection</th>
          <th class="px-3 py-2">State</th>
          <th class="px-3 py-2">Output</th>
          <th class="px-3 py-2">Duration</th>
          <th class="px-3 py-2">Tags</th>
          <th class="w-24 px-3 py-2"><span class="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="clip in clips"
          :key="clip.id"
          class="border-t border-line hover:bg-hover"
          :class="selected.has(clip.id) && 'bg-raised'"
        >
          <td class="px-3 py-2">
            <input
              type="checkbox"
              class="size-4 accent-[var(--color-accent)]"
              :checked="selected.has(clip.id)"
              :aria-label="`Select ${sourceName(clip)}`"
              @change="$emit('toggle', clip.id)"
            />
          </td>
          <td class="max-w-[18rem] truncate px-3 py-2" :title="clip.relative_source_path">
            {{ sourceName(clip) }}
            <span v-if="clip.failed_reason" class="block text-xs text-danger">
              {{ clip.failed_reason }}
            </span>
          </td>
          <td class="px-3 py-2 text-muted">{{ clip.collection_id }}</td>
          <td class="px-3 py-2"><StateBadge :state="clip.state" /></td>
          <td class="px-3 py-2 text-muted" :title="clip.relative_output_path">
            {{ clip.output_available ? "Ready" : "—" }}
          </td>
          <td class="px-3 py-2 whitespace-nowrap text-muted">
            {{ formatDuration(clip.duration_seconds) }}
          </td>
          <td class="max-w-[12rem] truncate px-3 py-2 text-muted">{{ clip.tags.join(", ") }}</td>
          <td class="px-3 py-2">
            <button type="button" class="btn px-2 py-1 text-xs" @click="$emit('open', clip)">
              Details
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
