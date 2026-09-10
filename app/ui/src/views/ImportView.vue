<script setup lang="ts">
import { onMounted, ref } from "vue";
import { apiFetch } from "@/composables/useApi";
import { useClips } from "@/composables/useClips";
import { jobIdFrom, useJobs } from "@/composables/useJobs";
import { useSession } from "@/composables/useSession";
import {
  COLLECTION_ID_HINT,
  COLLECTION_ID_PATTERN,
  uploadChunked,
} from "@/composables/useUpload";
import type { ActionResult } from "@/types";

const { load } = useClips();
const { collections } = useSession();
const { follow, refresh: refreshJobs } = useJobs();

const clipCollection = ref("");
const clipFiles = ref<HTMLInputElement | null>(null);
const clipBusy = ref(false);
const clipProgress = ref(0);
const clipLabel = ref("");
const clipStatus = ref("");
const clipError = ref("");

const scanCollection = ref("");
const scanBusy = ref(false);
const scanStatus = ref("");
const scanError = ref("");

const assetFile = ref<HTMLInputElement | null>(null);
const assetBusy = ref(false);
const assetProgress = ref(0);
const assetStatus = ref("");
const assetError = ref("");
const assets = ref<string[]>([]);

async function refreshAssets(): Promise<void> {
  try {
    assets.value = await apiFetch<string[]>("manager/assets");
  } catch (cause) {
    assetError.value = cause instanceof Error ? cause.message : String(cause);
  }
}

onMounted(refreshAssets);

async function uploadClips(): Promise<void> {
  const files = Array.from(clipFiles.value?.files ?? []).filter((file) => file.size > 0);
  if (files.length === 0 || !clipCollection.value) return;
  clipBusy.value = true;
  clipError.value = "";
  clipStatus.value = "";
  clipProgress.value = 0;
  const failures: string[] = [];
  for (const [index, file] of files.entries()) {
    const base = index / files.length;
    const span = 1 / files.length;
    clipLabel.value = `Uploading ${index + 1} of ${files.length}: ${file.name}`;
    try {
      await uploadChunked("clip", file, clipCollection.value, (fraction) => {
        clipProgress.value = Math.round((base + fraction * span) * 100);
      });
    } catch (cause) {
      failures.push(`${file.name}: ${cause instanceof Error ? cause.message : String(cause)}`);
    }
  }
  clipProgress.value = 100;
  clipLabel.value = "";
  clipBusy.value = false;
  const uploaded = files.length - failures.length;
  if (failures.length === 0) {
    clipStatus.value = `${uploaded} uploaded.`;
    if (clipFiles.value) clipFiles.value.value = "";
  } else {
    clipError.value =
      uploaded > 0
        ? `${uploaded} of ${files.length} uploaded. Failed: ${failures.join("; ")}`
        : `Upload failed: ${failures.join("; ")}`;
  }
  await load();
}

async function scan(): Promise<void> {
  const collection = scanCollection.value.trim();
  scanError.value = "";
  scanStatus.value = "";
  if (collection && !COLLECTION_ID_PATTERN.test(collection)) {
    scanError.value = COLLECTION_ID_HINT;
    return;
  }
  scanBusy.value = true;
  scanStatus.value = "Scanning…";
  try {
    const url = collection
      ? `manager/scan?collection_id=${encodeURIComponent(collection)}`
      : "manager/scan";
    const response = await apiFetch<ActionResult>(url, { method: "POST" });
    const jobId = jobIdFrom(response?.details);
    if (jobId) {
      const job = await follow(jobId, (update) => {
        const percent = Math.round(update.progress?.percent ?? 0);
        scanStatus.value = `Scanning… ${update.progress?.stage ?? update.state} ${percent}%`;
      });
      if (job?.state === "failed") throw new Error(job.error || "Scan failed.");
    }
    scanStatus.value = "Scan complete.";
    await Promise.all([load(), refreshJobs()]);
  } catch (cause) {
    scanStatus.value = "";
    scanError.value = cause instanceof Error ? cause.message : String(cause);
  }
  scanBusy.value = false;
}

async function uploadAsset(): Promise<void> {
  const file = assetFile.value?.files?.[0];
  if (!file || file.size === 0) return;
  assetBusy.value = true;
  assetError.value = "";
  assetStatus.value = "";
  assetProgress.value = 0;
  try {
    await uploadChunked("asset", file, null, (fraction) => {
      assetProgress.value = Math.round(fraction * 100);
    });
    assetStatus.value = `Uploaded ${file.name}.`;
    if (assetFile.value) assetFile.value.value = "";
    await refreshAssets();
  } catch (cause) {
    assetError.value = cause instanceof Error ? cause.message : String(cause);
  }
  assetBusy.value = false;
}

async function deleteAsset(name: string): Promise<void> {
  if (!window.confirm(`Permanently delete asset ${name}?`)) return;
  assetError.value = "";
  try {
    await apiFetch(`manager/assets/${encodeURIComponent(name)}/delete`, { method: "POST" });
    assetStatus.value = `Deleted ${name}.`;
    await refreshAssets();
  } catch (cause) {
    assetError.value = cause instanceof Error ? cause.message : String(cause);
  }
}
</script>

<template>
  <section class="flex flex-col gap-5">
    <article class="panel flex flex-col gap-3">
      <h2 class="font-semibold">Upload source clips</h2>
      <p class="text-sm text-muted">
        Large files are sent in chunks, so they pass through reverse proxies that limit request
        size. To skip uploading entirely, copy the files into the collection's source folder and
        scan for them below.
      </p>
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Collection</span>
          <select v-model="clipCollection" class="field w-auto" required>
            <option value="" disabled>Choose a collection</option>
            <option v-for="entry in collections" :key="entry.id" :value="entry.id">
              {{ entry.name }}
            </option>
          </select>
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Video files</span>
          <input ref="clipFiles" type="file" accept="video/*" multiple class="field w-auto" />
        </label>
        <button
          type="button"
          class="btn-primary"
          :disabled="clipBusy || !clipCollection"
          @click="uploadClips"
        >
          Upload
        </button>
      </div>
      <div v-if="clipBusy || clipProgress > 0" class="flex flex-col gap-1">
        <div class="h-2 overflow-hidden rounded-full bg-raised">
          <div class="h-full bg-accent transition-all" :style="{ width: `${clipProgress}%` }" />
        </div>
        <p class="text-xs text-muted">{{ clipLabel }}</p>
      </div>
      <p v-if="clipError" role="alert" class="text-sm text-danger">{{ clipError }}</p>
      <p v-else-if="clipStatus" role="status" class="text-sm text-muted">{{ clipStatus }}</p>
    </article>

    <article class="panel flex flex-col gap-3">
      <h2 class="font-semibold">Scan for files already on disk</h2>
      <p class="text-sm text-muted">
        If your clips are already inside a collection's configured source folder — copied there
        directly, over Samba, or with the Home Assistant Files add-on — scan instead of uploading
        them again.
      </p>
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Collection ID</span>
          <input
            v-model="scanCollection"
            class="field w-64"
            placeholder="e.g. regular — empty scans every collection"
          />
        </label>
        <button type="button" class="btn" :disabled="scanBusy" @click="scan">Scan library</button>
      </div>
      <p v-if="scanError" role="alert" class="text-sm text-danger">{{ scanError }}</p>
      <p v-else-if="scanStatus" role="status" class="text-sm text-muted">{{ scanStatus }}</p>
    </article>

    <article class="panel flex flex-col gap-3">
      <h2 class="font-semibold">Intro and outro assets</h2>
      <p class="text-sm text-muted">
        Assets live in the Worker's private storage. Upload them here, then reference the exact
        filename shown below in the processing profile's intro/outro fields.
      </p>
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Video file</span>
          <input ref="assetFile" type="file" accept="video/*" class="field w-auto" />
        </label>
        <button type="button" class="btn" :disabled="assetBusy" @click="uploadAsset">
          Upload asset
        </button>
      </div>
      <div v-if="assetBusy" class="h-2 overflow-hidden rounded-full bg-raised">
        <div class="h-full bg-accent transition-all" :style="{ width: `${assetProgress}%` }" />
      </div>
      <p v-if="assetError" role="alert" class="text-sm text-danger">{{ assetError }}</p>
      <p v-else-if="assetStatus" role="status" class="text-sm text-muted">{{ assetStatus }}</p>
      <ul v-if="assets.length" class="flex flex-col gap-1">
        <li
          v-for="name in assets"
          :key="name"
          class="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2 text-sm"
        >
          <code class="truncate">{{ name }}</code>
          <button type="button" class="btn px-2 py-1 text-xs" @click="deleteAsset(name)">
            Delete
          </button>
        </li>
      </ul>
      <p v-else class="text-sm text-muted">No assets uploaded yet.</p>
    </article>
  </section>
</template>
