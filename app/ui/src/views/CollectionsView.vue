<script setup lang="ts">
import { onMounted, ref } from "vue";
import CollectionForm from "@/components/CollectionForm.vue";
import ProfileForm from "@/components/ProfileForm.vue";
import { useClips } from "@/composables/useClips";
import { useCollections } from "@/composables/useCollections";
import type { Collection, Profile } from "@/types";

const { collections, profiles, load } = useCollections();
const { clips, load: loadClips } = useClips();

type Editing =
  | { kind: "none" }
  | { kind: "collection"; record: Collection | null }
  | { kind: "profile"; record: Profile | null };

const editing = ref<Editing>({ kind: "none" });
const error = ref("");

onMounted(async () => {
  try {
    await load();
    if (clips.value.length === 0) await loadClips();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
});

function close(): void {
  editing.value = { kind: "none" };
}
</script>

<template>
  <section class="flex flex-col gap-5">
    <p v-if="error" role="alert" class="rounded-panel bg-danger/15 p-3 text-sm text-danger">
      {{ error }}
    </p>

    <article v-if="editing.kind === 'collection'" class="panel">
      <h2 class="mb-4 font-semibold">
        {{ editing.record ? `Edit ${editing.record.name}` : "New collection" }}
      </h2>
      <CollectionForm
        :collection="editing.record"
        :profiles="profiles"
        @saved="close"
        @cancel="close"
      />
    </article>

    <article v-else-if="editing.kind === 'profile'" class="panel">
      <h2 class="mb-4 font-semibold">
        {{ editing.record ? `Edit ${editing.record.name}` : "New processing profile" }}
      </h2>
      <ProfileForm
        :profile="editing.record"
        :template="profiles[0] ?? null"
        @saved="close"
        @cancel="close"
      />
    </article>

    <template v-else>
      <article class="panel flex flex-col gap-3">
        <div class="flex items-center justify-between gap-3">
          <h2 class="font-semibold">Collections</h2>
          <button type="button" class="btn-primary" @click="editing = { kind: 'collection', record: null }">
            New collection
          </button>
        </div>
        <p v-if="!collections.length" class="text-sm text-muted">
          No collections yet. Create one to tell the Worker where its clips live.
        </p>
        <ul v-else class="flex flex-col gap-2">
          <li
            v-for="collection in collections"
            :key="collection.id"
            class="flex flex-wrap items-center gap-3 rounded-lg border border-line px-3 py-2 text-sm"
          >
            <button
              type="button"
              class="min-w-0 flex-1 text-left"
              @click="editing = { kind: 'collection', record: collection }"
            >
              <span class="font-medium">{{ collection.name }}</span>
              <span class="ml-2 text-xs text-muted">{{ collection.id }}</span>
            </button>
            <span class="text-xs text-muted">{{ collection.playback_mode }}</span>
            <span class="text-xs text-muted">priority {{ collection.priority }}</span>
            <span v-if="collection.is_default" class="text-xs text-accent">default</span>
            <span v-if="!collection.enabled" class="text-xs text-warn">disabled</span>
          </li>
        </ul>
      </article>

      <article class="panel flex flex-col gap-3">
        <div class="flex items-center justify-between gap-3">
          <h2 class="font-semibold">Processing profiles</h2>
          <button type="button" class="btn" @click="editing = { kind: 'profile', record: null }">
            New profile
          </button>
        </div>
        <ul class="flex flex-col gap-2">
          <li
            v-for="profile in profiles"
            :key="profile.id"
            class="flex flex-wrap items-center gap-3 rounded-lg border border-line px-3 py-2 text-sm"
          >
            <button
              type="button"
              class="min-w-0 flex-1 text-left"
              @click="editing = { kind: 'profile', record: profile }"
            >
              <span class="font-medium">{{ profile.name }}</span>
              <span class="ml-2 text-xs text-muted">{{ profile.id }}</span>
            </button>
            <span class="text-xs text-muted">
              {{ profile.settings.video.width }}×{{ profile.settings.video.height }}
              at {{ profile.settings.video.fps }} fps
            </span>
          </li>
        </ul>
      </article>
    </template>
  </section>
</template>
