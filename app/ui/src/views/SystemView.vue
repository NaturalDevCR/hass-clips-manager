<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { apiFetch } from "@/composables/useApi";
import { useClips } from "@/composables/useClips";
import { useJobs } from "@/composables/useJobs";
import { useSession } from "@/composables/useSession";
import { DEFAULT_TIME_ZONE, formatDateTime, jobTarget } from "@/lib/format";
import { COLLECTION_ID_HINT, COLLECTION_ID_PATTERN } from "@/composables/useUpload";
import type { LogEntry, TrashEntry } from "@/types";

const { jobs, refresh: refreshJobs } = useJobs();
const { load: reloadClips } = useClips();
const { collections } = useSession();

const folderCollection = ref("");
const folderPath = ref("");
const folderBusy = ref(false);
const folderStatus = ref("");
const folderError = ref("");

const trash = ref<TrashEntry[]>([]);
const trashError = ref("");

const logs = ref<LogEntry[]>([]);
const logError = ref("");

const TIMEZONE_KEY = "manager-timezone";
const ZONE_OPTIONS = computed(() => {
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const options = [
    { value: "America/Costa_Rica", label: "Costa Rica" },
    { value: "UTC", label: "UTC" },
    { value: browserZone, label: `Browser (${browserZone})` },
  ];
  return options.filter(
    (option, index) => options.findIndex((candidate) => candidate.value === option.value) === index,
  );
});
const timezone = ref(DEFAULT_TIME_ZONE);

function rememberTimezone(next: string): void {
  timezone.value = next;
  try {
    localStorage.setItem(TIMEZONE_KEY, next);
  } catch {
    // Private windows and blocked site data make storage throw; the choice
    // simply does not survive a reload.
  }
}

async function refreshTrash(): Promise<void> {
  trashError.value = "";
  try {
    trash.value = await apiFetch<TrashEntry[]>("manager/trash");
  } catch (cause) {
    trashError.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function refreshLogs(): Promise<void> {
  logError.value = "";
  try {
    logs.value = await apiFetch<LogEntry[]>("manager/logs");
  } catch (cause) {
    logError.value = cause instanceof Error ? cause.message : String(cause);
  }
}

onMounted(async () => {
  try {
    const stored = localStorage.getItem(TIMEZONE_KEY);
    if (stored) timezone.value = stored;
  } catch {
    // Same fallback as above: keep the default when storage is unavailable.
  }
  await Promise.all([refreshTrash(), refreshLogs(), refreshJobs()]);
});

async function createFolder(): Promise<void> {
  const collection = folderCollection.value.trim();
  folderError.value = "";
  folderStatus.value = "";
  if (!COLLECTION_ID_PATTERN.test(collection)) {
    folderError.value = COLLECTION_ID_HINT;
    return;
  }
  folderBusy.value = true;
  try {
    await apiFetch(`manager/collections/${encodeURIComponent(collection)}/directories`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ relative_path: folderPath.value.trim() }),
    });
    folderStatus.value = `Folder "${folderPath.value.trim()}" created.`;
    folderPath.value = "";
  } catch (cause) {
    folderError.value = cause instanceof Error ? cause.message : String(cause);
  }
  folderBusy.value = false;
}

async function restore(entry: TrashEntry): Promise<void> {
  trashError.value = "";
  try {
    await apiFetch(`manager/trash/${entry.id}/restore`, { method: "POST" });
    await Promise.all([refreshTrash(), reloadClips()]);
  } catch (cause) {
    trashError.value = cause instanceof Error ? cause.message : String(cause);
  }
}
</script>

<template>
  <section class="flex flex-col gap-5">
    <article class="panel flex flex-col gap-3">
      <h2 class="font-semibold">Create a collection folder</h2>
      <p class="text-sm text-muted">
        Creates an empty folder inside the collection's configured source directory.
      </p>
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Collection ID</span>
          <input
            v-model="folderCollection"
            class="field w-48"
            placeholder="e.g. regular"
            list="system-collection-ids"
          />
          <datalist id="system-collection-ids">
            <option v-for="entry in collections" :key="entry.id" :value="entry.id" />
          </datalist>
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Folder</span>
          <input v-model="folderPath" class="field w-48" placeholder="e.g. organized" />
        </label>
        <button
          type="button"
          class="btn"
          :disabled="folderBusy || !folderPath.trim()"
          @click="createFolder"
        >
          Create folder
        </button>
      </div>
      <p v-if="folderError" role="alert" class="text-sm text-danger">{{ folderError }}</p>
      <p v-else-if="folderStatus" role="status" class="text-sm text-muted">{{ folderStatus }}</p>
    </article>

    <article class="panel flex flex-col gap-3">
      <div class="flex items-center justify-between gap-3">
        <h2 class="font-semibold">Trash</h2>
        <button type="button" class="btn px-2 py-1 text-xs" @click="refreshTrash">Refresh</button>
      </div>
      <p v-if="trashError" role="alert" class="text-sm text-danger">{{ trashError }}</p>
      <p v-else-if="!trash.length" class="text-sm text-muted">Trash is empty.</p>
      <ul v-else class="flex flex-col gap-1">
        <li
          v-for="entry in trash"
          :key="entry.id"
          class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line px-3 py-2 text-sm"
        >
          <code class="text-xs" :title="entry.clip_id">{{ entry.clip_id.slice(0, 8) }}</code>
          <span class="text-muted">{{ entry.target }}</span>
          <span class="text-xs text-muted">{{ formatDateTime(entry.created_at, timezone) }}</span>
          <button type="button" class="btn px-2 py-1 text-xs" @click="restore(entry)">
            Restore
          </button>
        </li>
      </ul>
    </article>

    <article class="panel flex flex-col gap-3">
      <div class="flex items-center justify-between gap-3">
        <h2 class="font-semibold">Recent jobs</h2>
        <div class="flex items-center gap-2">
          <label class="flex items-center gap-2 text-xs text-muted">
            <span>Timezone</span>
            <select
              class="field w-auto"
              :value="timezone"
              @change="rememberTimezone(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="zone in ZONE_OPTIONS" :key="zone.value" :value="zone.value">
                {{ zone.label }}
              </option>
            </select>
          </label>
          <button type="button" class="btn px-2 py-1 text-xs" @click="refreshJobs">Refresh</button>
        </div>
      </div>
      <p v-if="!jobs.length" class="text-sm text-muted">No jobs yet.</p>
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[42rem] border-collapse text-sm">
          <thead class="text-left text-xs tracking-wide text-muted uppercase">
            <tr>
              <th class="py-2 pr-3">Target</th>
              <th class="py-2 pr-3">Kind</th>
              <th class="py-2 pr-3">State</th>
              <th class="py-2 pr-3">Created</th>
              <th class="py-2 pr-3">Finished</th>
              <th class="py-2">Error</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="job in jobs"
              :key="job.id"
              class="border-t border-line"
              :class="job.state === 'failed' && 'text-danger'"
            >
              <td class="max-w-64 truncate py-2 pr-3" :title="job.target || job.id">
                {{ jobTarget(job) || "—" }}
              </td>
              <td class="py-2 pr-3">{{ job.kind }}</td>
              <td class="py-2 pr-3">{{ job.state }}</td>
              <td class="py-2 pr-3 text-xs text-muted">{{ formatDateTime(job.created_at, timezone) }}</td>
              <td class="py-2 pr-3 text-xs text-muted">{{ formatDateTime(job.finished_at, timezone) }}</td>
              <td class="py-2 text-xs">{{ job.error || "" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-xs text-muted">The most recent 50 jobs, newest first.</p>
    </article>

    <article class="panel flex flex-col gap-3">
      <div class="flex items-center justify-between gap-3">
        <h2 class="font-semibold">Worker log</h2>
        <button type="button" class="btn px-2 py-1 text-xs" @click="refreshLogs">Refresh</button>
      </div>
      <p v-if="logError" role="alert" class="text-sm text-danger">{{ logError }}</p>
      <p v-else-if="!logs.length" class="text-sm text-muted">No log entries yet.</p>
      <div v-else class="max-h-96 overflow-auto">
        <table class="w-full min-w-[40rem] border-collapse text-sm">
          <thead class="text-left text-xs tracking-wide text-muted uppercase">
            <tr>
              <th class="py-2 pr-3">Timestamp</th>
              <th class="py-2 pr-3">Level</th>
              <th class="py-2">Message</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(entry, index) in logs"
              :key="`${entry.timestamp}-${index}`"
              class="border-t border-line"
              :class="entry.level === 'error' && 'text-danger'"
            >
              <td class="py-2 pr-3 text-xs whitespace-nowrap text-muted">
                {{ formatDateTime(entry.timestamp, timezone) }}
              </td>
              <td class="py-2 pr-3 text-xs">{{ entry.level }}</td>
              <td class="py-2 text-xs">{{ entry.message }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-xs text-muted">The most recent 200 entries, newest first.</p>
    </article>
  </section>
</template>
