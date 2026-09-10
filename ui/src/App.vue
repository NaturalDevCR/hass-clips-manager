<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import AppSidebar from "@/components/AppSidebar.vue";
import JobIndicator from "@/components/JobIndicator.vue";
import { useJobs } from "@/composables/useJobs";
import { useSession } from "@/composables/useSession";

const { workerVersion, load } = useSession();
const { start, stop } = useJobs();
const error = ref("");
const ready = ref(false);

onMounted(async () => {
  try {
    await load();
    start();
    ready.value = true;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
});

onUnmounted(stop);
</script>

<template>
  <div class="flex h-full flex-col md:flex-row">
    <AppSidebar />
    <div class="flex min-w-0 flex-1 flex-col">
      <header class="flex items-center justify-between gap-4 border-b border-line px-5 py-3">
        <div class="min-w-0">
          <h1 class="truncate text-base font-semibold">Library Manager</h1>
          <p class="text-xs text-muted">
            Cinema Collections Worker <span v-if="workerVersion">{{ workerVersion }}</span>
          </p>
        </div>
        <JobIndicator />
      </header>
      <p v-if="error" role="alert" class="m-5 rounded-panel bg-danger/15 p-3 text-sm text-danger">
        {{ error }}
      </p>
      <main class="min-h-0 flex-1 overflow-y-auto p-5">
        <RouterView v-if="ready" />
      </main>
    </div>
  </div>
</template>
