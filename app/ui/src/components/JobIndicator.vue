<script setup lang="ts">
import { ref } from "vue";
import { useJobs } from "@/composables/useJobs";

const { jobs, active } = useJobs();
const open = ref(false);
</script>

<template>
  <div class="relative">
    <button
      type="button"
      class="btn"
      :aria-expanded="open"
      aria-label="Queue status"
      @click="open = !open"
    >
      <span
        class="size-2 rounded-full"
        :class="active.length ? 'animate-pulse bg-accent' : 'bg-ok'"
        aria-hidden="true"
      />
      {{ active.length ? `${active.length} running` : "Queue idle" }}
    </button>
    <div
      v-if="open"
      class="absolute right-0 z-20 mt-2 w-80 rounded-panel border border-line bg-raised p-2 shadow-2xl"
    >
      <p v-if="!jobs.length" class="p-2 text-sm text-muted">No jobs yet.</p>
      <ul v-else class="max-h-80 space-y-1 overflow-y-auto">
        <li v-for="job in jobs.slice(0, 12)" :key="job.id" class="rounded px-2 py-1.5 text-sm">
          <div class="flex items-baseline justify-between gap-2">
            <span class="truncate text-ink">{{ job.kind }}</span>
            <span class="shrink-0 text-xs text-muted">{{ job.state }}</span>
          </div>
          <p v-if="job.error" class="text-xs text-danger">{{ job.error }}</p>
        </li>
      </ul>
    </div>
  </div>
</template>
