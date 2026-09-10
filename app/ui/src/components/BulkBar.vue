<script setup lang="ts">
import { computed, ref } from "vue";
import { apiFetch } from "@/composables/useApi";
import { useJobs } from "@/composables/useJobs";
import { runBulk } from "@/lib/bulk";
import type { Clip } from "@/types";

const props = defineProps<{ clips: Clip[]; selected: Set<string> }>();
const emit = defineEmits<{ done: []; clear: [] }>();

const { refresh: refreshJobs } = useJobs();

const busy = ref(false);
const summary = ref("");
const failures = ref<string[]>([]);
const showFailures = ref(false);
const tagInput = ref("");

const ids = computed(() => [...props.selected]);
const chosen = computed(() => props.clips.filter((clip) => props.selected.has(clip.id)));
// "both" is only offered when every selected clip actually has an output to remove.
const trashTarget = computed(() =>
  chosen.value.length > 0 && chosen.value.every((clip) => clip.output_available)
    ? "both"
    : "source",
);

async function apply(verb: string, task: (id: string) => Promise<unknown>): Promise<void> {
  busy.value = true;
  summary.value = "";
  failures.value = [];
  showFailures.value = false;
  const total = ids.value.length;
  const outcome = await runBulk(ids.value, task);
  failures.value = outcome.failures;
  summary.value =
    outcome.failures.length === 0
      ? `${outcome.ok} of ${total} ${verb}.`
      : `${outcome.ok} of ${total} ${verb}, ${outcome.failures.length} failed.`;
  busy.value = false;
  await refreshJobs();
  emit("done");
  if (outcome.failures.length === 0) emit("clear");
}

function recompile(): void {
  void apply("recompiled", (id) => apiFetch(`manager/clips/${id}/recompile`, { method: "POST" }));
}

function trash(): void {
  const target = trashTarget.value;
  void apply("moved to trash", (id) =>
    apiFetch(`manager/clips/${id}/trash`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target }),
    }),
  );
}

function addTags(): void {
  const added = tagInput.value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
  if (added.length === 0) return;
  const byId = new Map(props.clips.map((clip) => [clip.id, clip]));
  void apply("tagged", (id) => {
    const clip = byId.get(id);
    const merged = [...new Set([...(clip?.tags ?? []), ...added])];
    return apiFetch(`manager/clips/${id}/metadata`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags: merged, notes: clip?.notes ? clip.notes : null }),
    });
  }).then(() => {
    tagInput.value = "";
  });
}
</script>

<template>
  <div
    class="sticky bottom-0 z-20 -mx-5 -mb-5 flex flex-wrap items-center gap-3 border-t border-line bg-raised px-5 py-3"
  >
    <span class="text-sm font-medium">{{ selected.size }} selected</span>
    <button type="button" class="btn-primary" :disabled="busy" @click="recompile">Recompile</button>
    <label class="flex items-center gap-2">
      <span class="sr-only">Tags to add to the selected clips</span>
      <input v-model="tagInput" class="field w-44" placeholder="add tags…" />
    </label>
    <button type="button" class="btn" :disabled="busy || !tagInput.trim()" @click="addTags">
      Add tags
    </button>
    <button type="button" class="btn border-danger text-danger" :disabled="busy" @click="trash">
      Move to trash ({{ trashTarget }})
    </button>
    <button type="button" class="btn" @click="emit('clear')">Clear selection</button>
    <p v-if="summary" role="status" class="text-sm text-muted">
      {{ summary }}
      <button
        v-if="failures.length"
        type="button"
        class="ml-2 underline"
        :aria-expanded="showFailures"
        @click="showFailures = !showFailures"
      >
        {{ showFailures ? "Hide" : "Show" }} failures
      </button>
    </p>
    <ul v-if="showFailures" class="w-full space-y-1 text-xs text-danger">
      <li v-for="failure in failures" :key="failure">{{ failure }}</li>
    </ul>
  </div>
</template>
