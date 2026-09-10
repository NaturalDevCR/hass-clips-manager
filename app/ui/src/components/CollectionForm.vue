<script setup lang="ts">
import { computed, ref, watch } from "vue";
import OrderEditor from "@/components/OrderEditor.vue";
import { useClips } from "@/composables/useClips";
import { useCollections } from "@/composables/useCollections";
import type { Collection, PlaybackMode, Profile } from "@/types";

const props = defineProps<{ collection: Collection | null; profiles: Profile[] }>();
const emit = defineEmits<{ saved: [Collection]; cancel: [] }>();

const { createCollection, patchCollection } = useCollections();
const { clips } = useClips();

const draft = ref(blank());
const busy = ref(false);
const failure = ref("");

function blank() {
  return {
    id: "",
    name: "",
    source_directory: "",
    processing_profile_id: props.profiles[0]?.id ?? "",
    enabled: true,
    priority: 0,
    is_default: false,
    allow_manual_override: true,
    tags: "",
    notes: "",
    starts_at: "",
    ends_at: "",
    schedule_enabled: false,
    schedule_time: "00:00",
    playback_mode: "random" as PlaybackMode,
    ordered_clip_ids: [] as string[],
  };
}

function fromRecord(record: Collection) {
  const schedule = record.schedule ?? {};
  return {
    id: record.id,
    name: record.name,
    source_directory: record.source_directory,
    processing_profile_id: record.processing_profile_id,
    enabled: record.enabled,
    priority: record.priority,
    is_default: record.is_default,
    allow_manual_override: record.allow_manual_override,
    tags: record.tags.join(", "),
    notes: record.notes ?? "",
    starts_at: record.starts_at ?? "",
    ends_at: record.ends_at ?? "",
    schedule_enabled: Boolean(schedule.enabled),
    schedule_time: String(schedule.local_time ?? "00:00"),
    playback_mode: record.playback_mode,
    ordered_clip_ids: [...record.ordered_clip_ids],
  };
}

watch(
  () => props.collection,
  (record) => {
    draft.value = record ? fromRecord(record) : blank();
    failure.value = "";
  },
  { immediate: true },
);

const collectionClips = computed(() =>
  clips.value.filter((clip) => clip.collection_id === draft.value.id),
);

// The Worker refuses custom playback with no order, so the form does not offer
// to send one.
const orderMissing = computed(
  () => draft.value.playback_mode === "custom" && draft.value.ordered_clip_ids.length === 0,
);

function payload(): Record<string, unknown> {
  const draftValue = draft.value;
  return {
    name: draftValue.name,
    source_directory: draftValue.source_directory,
    processing_profile_id: draftValue.processing_profile_id,
    enabled: draftValue.enabled,
    priority: Number(draftValue.priority),
    is_default: draftValue.is_default,
    allow_manual_override: draftValue.allow_manual_override,
    tags: draftValue.tags
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
    notes: draftValue.notes.trim() || null,
    starts_at: draftValue.starts_at.trim() || null,
    ends_at: draftValue.ends_at.trim() || null,
    schedule: draftValue.schedule_enabled
      ? { enabled: true, local_time: draftValue.schedule_time }
      : {},
    playback_mode: draftValue.playback_mode,
    ordered_clip_ids: draftValue.ordered_clip_ids,
  };
}

async function save(): Promise<void> {
  busy.value = true;
  failure.value = "";
  try {
    const record = props.collection
      ? await patchCollection(props.collection.id, props.collection.revision, payload())
      : await createCollection({ id: draft.value.id, ...payload() });
    emit("saved", record);
  } catch (cause) {
    // The Worker owns these rules; report its refusal instead of restating them.
    failure.value = cause instanceof Error ? cause.message : String(cause);
  }
  busy.value = false;
}
</script>

<template>
  <form class="flex flex-col gap-5" @submit.prevent="save">
    <section class="flex flex-col gap-3">
      <h3 class="text-xs tracking-widest text-muted uppercase">Identity</h3>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Collection ID</span>
          <input
            v-model="draft.id"
            class="field"
            required
            :disabled="Boolean(collection)"
            placeholder="e.g. regular"
          />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Name</span>
          <input v-model="draft.name" class="field" required />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Source folder</span>
          <input v-model="draft.source_directory" class="field" required />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Processing profile</span>
          <select v-model="draft.processing_profile_id" class="field" required>
            <option v-for="profile in profiles" :key="profile.id" :value="profile.id">
              {{ profile.name }}
            </option>
          </select>
        </label>
      </div>
    </section>

    <section class="flex flex-col gap-3">
      <h3 class="text-xs tracking-widest text-muted uppercase">Selection</h3>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Priority</span>
          <input v-model="draft.priority" type="number" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Playback order</span>
          <select v-model="draft.playback_mode" class="field">
            <option value="random">Random</option>
            <option value="sequential">Sequential</option>
            <option value="custom">Custom</option>
          </select>
        </label>
      </div>
      <div class="flex flex-wrap gap-4 text-sm">
        <label class="flex items-center gap-2">
          <input v-model="draft.enabled" type="checkbox" class="size-4 accent-[var(--color-accent)]" />
          Enabled
        </label>
        <label class="flex items-center gap-2">
          <input
            v-model="draft.is_default"
            type="checkbox"
            class="size-4 accent-[var(--color-accent)]"
          />
          Default collection
        </label>
        <label class="flex items-center gap-2">
          <input
            v-model="draft.allow_manual_override"
            type="checkbox"
            class="size-4 accent-[var(--color-accent)]"
          />
          Allow manual override
        </label>
      </div>
      <OrderEditor
        v-if="draft.playback_mode === 'custom'"
        v-model="draft.ordered_clip_ids"
        :clips="collectionClips"
      />
    </section>

    <section class="flex flex-col gap-3">
      <h3 class="text-xs tracking-widest text-muted uppercase">Schedule</h3>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Active from</span>
          <input v-model="draft.starts_at" class="field" placeholder="2026-01-01T00:00:00+00:00" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Active until</span>
          <input v-model="draft.ends_at" class="field" placeholder="2026-12-31T00:00:00+00:00" />
        </label>
      </div>
      <div class="flex flex-wrap items-end gap-4">
        <label class="flex items-center gap-2 text-sm">
          <input
            v-model="draft.schedule_enabled"
            type="checkbox"
            class="size-4 accent-[var(--color-accent)]"
          />
          Compile on a daily schedule
        </label>
        <label v-if="draft.schedule_enabled" class="flex flex-col gap-1">
          <span class="text-xs text-muted">Local time</span>
          <input v-model="draft.schedule_time" class="field w-32" placeholder="02:30" />
        </label>
      </div>
      <p class="text-xs text-muted">Home Assistant dispatches the schedule it reads here.</p>
    </section>

    <section class="flex flex-col gap-3">
      <h3 class="text-xs tracking-widest text-muted uppercase">Notes</h3>
      <label class="flex flex-col gap-1">
        <span class="text-xs text-muted">Tags</span>
        <input v-model="draft.tags" class="field" placeholder="tags, comma, separated" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-xs text-muted">Notes</span>
        <textarea v-model="draft.notes" class="field" rows="3" />
      </label>
    </section>

    <p v-if="failure" role="alert" class="rounded-lg bg-danger/15 p-3 text-sm text-danger">
      {{ failure }}
    </p>
    <p v-else-if="orderMissing" class="text-sm text-warn">
      Custom playback needs an order. Add the catalogued clips above.
    </p>

    <div class="flex gap-2">
      <button type="submit" class="btn-primary" :disabled="busy || orderMissing">
        {{ collection ? "Save collection" : "Create collection" }}
      </button>
      <button type="button" class="btn" @click="emit('cancel')">Cancel</button>
    </div>
  </form>
</template>
