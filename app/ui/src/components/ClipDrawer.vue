<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import StateBadge from "@/components/StateBadge.vue";
import { apiFetch } from "@/composables/useApi";
import { jobIdFrom, useJobs } from "@/composables/useJobs";
import { copyText, formatDuration, sourceName } from "@/lib/format";
import type { ActionResult, Clip } from "@/types";

const props = defineProps<{ clip: Clip }>();
const emit = defineEmits<{ close: []; changed: [] }>();

const { follow, refresh: refreshJobs } = useJobs();

const closeButton = ref<HTMLButtonElement | null>(null);
const status = ref("");
const failure = ref("");
const busy = ref(false);
const tags = ref(props.clip.tags.join(", "));
const notes = ref(props.clip.notes);
const destination = ref(props.clip.relative_source_path);
const trashTarget = ref("source");
const deleteTarget = ref("source");

// An unavailable output cannot be trashed or deleted, so it is never offered.
const targets = computed(() =>
  props.clip.output_available
    ? [
        { value: "source", label: "Source" },
        { value: "output", label: "Output" },
        { value: "both", label: "Both" },
      ]
    : [{ value: "source", label: "Source" }],
);

watch(
  () => props.clip,
  (clip) => {
    tags.value = clip.tags.join(", ");
    notes.value = clip.notes;
    destination.value = clip.relative_source_path;
    trashTarget.value = "source";
    deleteTarget.value = "source";
    status.value = "";
    failure.value = "";
  },
);

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}

onMounted(async () => {
  document.addEventListener("keydown", onKeydown);
  await nextTick();
  closeButton.value?.focus();
});

onUnmounted(() => document.removeEventListener("keydown", onKeydown));

async function run(label: string, task: () => Promise<void>): Promise<void> {
  busy.value = true;
  failure.value = "";
  status.value = label;
  try {
    await task();
    emit("changed");
  } catch (cause) {
    failure.value = cause instanceof Error ? cause.message : String(cause);
    status.value = "";
  } finally {
    busy.value = false;
  }
}

async function queued(action: "scan" | "recompile"): Promise<void> {
  await run(`${action === "scan" ? "Scanning" : "Recompiling"}…`, async () => {
    const response = await apiFetch<ActionResult>(`manager/clips/${props.clip.id}/${action}`, {
      method: "POST",
    });
    const jobId = jobIdFrom(response?.details);
    if (jobId) {
      const job = await follow(jobId, (update) => {
        const percent = Math.round(update.progress?.percent ?? 0);
        status.value = `${update.progress?.stage ?? update.state} ${percent}%`;
      });
      if (job?.state === "failed") throw new Error(job.error || `${action} failed.`);
    }
    await refreshJobs();
    status.value = `${action === "scan" ? "Scan" : "Recompile"} complete.`;
  });
}

async function saveMetadata(): Promise<void> {
  await run("Saving…", async () => {
    await apiFetch(`manager/clips/${props.clip.id}/metadata`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tags: tags.value
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        notes: notes.value.trim() || null,
      }),
    });
    status.value = "Metadata saved.";
  });
}

async function move(): Promise<void> {
  await run("Moving…", async () => {
    await apiFetch(`manager/clips/${props.clip.id}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination_relative_path: destination.value }),
    });
    status.value = "Source moved.";
  });
}

async function trash(): Promise<void> {
  await run("Moving to trash…", async () => {
    await apiFetch(`manager/clips/${props.clip.id}/trash`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: trashTarget.value }),
    });
    status.value = "Moved to trash.";
    emit("close");
  });
}

async function remove(): Promise<void> {
  const target = deleteTarget.value;
  const info = await apiFetch<{ warning: string; confirmation: string }>(
    `manager/clips/${props.clip.id}/delete-confirmation?target=${target}`,
  );
  const output = props.clip.relative_output_path || "(no compiled output)";
  const affected =
    target === "source"
      ? `Source: ${props.clip.relative_source_path}`
      : target === "output"
        ? `Compiled output: ${output}`
        : `Source: ${props.clip.relative_source_path}\nCompiled output: ${output}`;
  if (!window.confirm(`${info.warning}\n${affected}`)) return;
  await run("Deleting…", async () => {
    await apiFetch(`manager/clips/${props.clip.id}/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, confirmation: info.confirmation }),
    });
    emit("close");
  });
}

async function copyId(): Promise<void> {
  try {
    await copyText(props.clip.id);
    status.value = "Clip ID copied.";
  } catch {
    failure.value = "Copy failed — select the ID and copy it manually.";
  }
}
</script>

<template>
  <div class="fixed inset-0 z-30 flex justify-end">
    <div class="flex-1 bg-black/50" @click="emit('close')" />
    <aside
      role="dialog"
      aria-modal="true"
      :aria-label="`Details for ${sourceName(clip)}`"
      class="flex w-full max-w-md flex-col overflow-y-auto border-l border-line bg-surface"
    >
      <header class="flex items-start gap-3 border-b border-line p-4">
        <div class="min-w-0 flex-1">
          <h2 class="truncate font-semibold" :title="clip.relative_source_path">
            {{ sourceName(clip) }}
          </h2>
          <p class="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <StateBadge :state="clip.state" />
            <span>{{ clip.collection_id }}</span>
            <span>{{ formatDuration(clip.duration_seconds) }}</span>
          </p>
        </div>
        <button ref="closeButton" type="button" class="btn px-2 py-1" @click="emit('close')">
          Close
        </button>
      </header>

      <div class="flex flex-col gap-5 p-4 text-sm">
        <p v-if="clip.failed_reason" class="rounded-lg bg-danger/15 p-3 text-danger">
          {{ clip.failed_reason }}
        </p>
        <p v-if="failure" role="alert" class="rounded-lg bg-danger/15 p-3 text-danger">
          {{ failure }}
        </p>
        <p v-else-if="status" role="status" class="text-muted">{{ status }}</p>

        <section class="flex flex-col gap-2">
          <h3 class="text-xs tracking-widest text-muted uppercase">Identity</h3>
          <p class="break-all text-muted">
            <code>{{ clip.id }}</code>
          </p>
          <p class="break-all text-muted">Source: {{ clip.relative_source_path }}</p>
          <p class="break-all text-muted">
            Output: {{ clip.output_available ? clip.relative_output_path : "not compiled" }}
          </p>
          <button type="button" class="btn self-start" @click="copyId">Copy clip ID</button>
        </section>

        <section class="flex flex-col gap-2">
          <h3 class="text-xs tracking-widest text-muted uppercase">Processing</h3>
          <div class="flex gap-2">
            <button type="button" class="btn" :disabled="busy" @click="queued('scan')">
              Re-scan source
            </button>
            <button type="button" class="btn-primary" :disabled="busy" @click="queued('recompile')">
              Recompile
            </button>
          </div>
        </section>

        <section class="flex flex-col gap-2">
          <h3 class="text-xs tracking-widest text-muted uppercase">Metadata</h3>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-muted">Tags</span>
            <input v-model="tags" class="field" placeholder="tags, comma, separated" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-muted">Notes</span>
            <textarea v-model="notes" class="field" rows="3" />
          </label>
          <button type="button" class="btn self-start" :disabled="busy" @click="saveMetadata">
            Save metadata
          </button>
        </section>

        <section class="flex flex-col gap-2">
          <h3 class="text-xs tracking-widest text-muted uppercase">Move the source file</h3>
          <input v-model="destination" class="field" aria-label="Destination relative path" />
          <button type="button" class="btn self-start" :disabled="busy" @click="move">Move</button>
        </section>

        <section class="flex flex-col gap-2 border-t border-line pt-4">
          <h3 class="text-xs tracking-widest text-muted uppercase">Remove</h3>
          <div class="flex flex-wrap items-center gap-2">
            <label class="sr-only" for="trash-target">Trash target</label>
            <select id="trash-target" v-model="trashTarget" class="field w-auto">
              <option v-for="target in targets" :key="target.value" :value="target.value">
                {{ target.label }}
              </option>
            </select>
            <button type="button" class="btn" :disabled="busy" @click="trash">Move to trash</button>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <label class="sr-only" for="delete-target">Delete target</label>
            <select id="delete-target" v-model="deleteTarget" class="field w-auto">
              <option v-for="target in targets" :key="target.value" :value="target.value">
                {{ target.label }}
              </option>
            </select>
            <button
              type="button"
              class="btn border-danger text-danger"
              :disabled="busy"
              @click="remove"
            >
              Delete permanently
            </button>
          </div>
        </section>
      </div>
    </aside>
  </div>
</template>
