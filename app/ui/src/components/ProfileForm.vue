<script setup lang="ts">
import { ref, watch } from "vue";
import { apiFetch } from "@/composables/useApi";
import { useCollections } from "@/composables/useCollections";
import { cloneSettings, withLoudnessMode, withQualityMode, withScalingMode } from "@/lib/profile";
import type { Profile } from "@/types";

const props = defineProps<{ profile: Profile | null; template: Profile | null }>();
const emit = defineEmits<{ saved: [Profile]; cancel: [] }>();

const { createProfile, patchProfile } = useCollections();

const id = ref("");
const name = ref("");
const settings = ref(blankSettings());
const assets = ref<string[]>([]);
const busy = ref(false);
const failure = ref("");

function blankSettings() {
  // A new profile starts from an existing one rather than from a copy of the
  // Worker's defaults restated here, so the two can never drift.
  const source = props.profile ?? props.template;
  return source ? cloneSettings(source.settings) : null;
}

watch(
  () => [props.profile, props.template],
  () => {
    id.value = props.profile?.id ?? "";
    name.value = props.profile?.name ?? "";
    settings.value = blankSettings();
    failure.value = "";
  },
  { immediate: true },
);

void apiFetch<string[]>("manager/assets")
  .then((names) => {
    assets.value = names;
  })
  .catch(() => {
    // The asset list is a convenience; the reference can still be typed.
  });

async function save(): Promise<void> {
  if (!settings.value) return;
  busy.value = true;
  failure.value = "";
  try {
    const record = props.profile
      ? await patchProfile(props.profile.id, props.profile.revision, {
          name: name.value,
          settings: settings.value,
        })
      : await createProfile({ id: id.value, name: name.value, settings: settings.value });
    emit("saved", record);
  } catch (cause) {
    failure.value = cause instanceof Error ? cause.message : String(cause);
  }
  busy.value = false;
}
</script>

<template>
  <form v-if="settings" class="flex flex-col gap-5" @submit.prevent="save">
    <section class="grid gap-3 sm:grid-cols-2">
      <label class="flex flex-col gap-1">
        <span class="text-xs text-muted">Profile ID</span>
        <input v-model="id" class="field" required :disabled="Boolean(profile)" />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-xs text-muted">Name</span>
        <input v-model="name" class="field" required />
      </label>
    </section>

    <section class="flex flex-col gap-3">
      <h3 class="text-xs tracking-widest text-muted uppercase">Video</h3>
      <div class="grid gap-3 sm:grid-cols-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Width</span>
          <input v-model.number="settings.video.width" type="number" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Height</span>
          <input v-model.number="settings.video.height" type="number" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Frames per second</span>
          <input v-model.number="settings.video.fps" type="number" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Codec</span>
          <input v-model="settings.video.codec" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Preset</span>
          <input v-model="settings.video.preset" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Pixel format</span>
          <input v-model="settings.video.pixel_format" class="field" />
        </label>
      </div>

      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Quality</span>
          <select
            class="field w-40"
            :value="settings.video.quality.mode"
            @change="
              settings.video.quality = withQualityMode(
                settings.video.quality,
                ($event.target as HTMLSelectElement).value as 'crf' | 'bitrate',
              )
            "
          >
            <option value="crf">Constant quality</option>
            <option value="bitrate">Target bitrate</option>
          </select>
        </label>
        <label v-if="settings.video.quality.mode === 'crf'" class="flex flex-col gap-1">
          <span class="text-xs text-muted">CRF</span>
          <input v-model.number="settings.video.quality.crf" type="number" step="0.5" class="field w-28" />
        </label>
        <label v-else class="flex flex-col gap-1">
          <span class="text-xs text-muted">Bitrate (kbps)</span>
          <input
            v-model.number="settings.video.quality.bitrate_kbps"
            type="number"
            class="field w-32"
          />
        </label>
      </div>

      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Scaling</span>
          <select
            class="field w-40"
            :value="settings.video.scaling.mode"
            @change="
              settings.video.scaling = withScalingMode(
                settings.video.scaling,
                ($event.target as HTMLSelectElement).value as 'aspect_fit' | 'crop',
              )
            "
          >
            <option value="aspect_fit">Fit, keep aspect</option>
            <option value="crop">Crop to fill</option>
          </select>
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Scale width</span>
          <input v-model.number="settings.video.scaling.width" type="number" class="field w-32" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Scale height</span>
          <input v-model.number="settings.video.scaling.height" type="number" class="field w-32" />
        </label>
      </div>
    </section>

    <section class="flex flex-col gap-3">
      <h3 class="text-xs tracking-widest text-muted uppercase">Audio</h3>
      <div class="grid gap-3 sm:grid-cols-4">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Codec</span>
          <input v-model="settings.audio.codec" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Bitrate (kbps)</span>
          <input v-model.number="settings.audio.bitrate_kbps" type="number" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Channels</span>
          <input v-model.number="settings.audio.channels" type="number" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Sample rate</span>
          <input v-model.number="settings.audio.sample_rate" type="number" class="field" />
        </label>
      </div>
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">When a clip has no audio</span>
          <select v-model="settings.audio.missing_policy.mode" class="field w-44">
            <option value="required">Refuse the clip</option>
            <option value="silence">Add silence</option>
          </select>
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Fallback</span>
          <select v-model="settings.audio.fallback" class="field w-36">
            <option value="none">None</option>
            <option value="silence">Silence</option>
          </select>
        </label>
      </div>
    </section>

    <section class="flex flex-col gap-3">
      <h3 class="text-xs tracking-widest text-muted uppercase">Loudness</h3>
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Analysis</span>
          <select
            class="field w-40"
            :value="settings.loudness.mode"
            @change="
              settings.loudness = withLoudnessMode(
                settings.loudness,
                ($event.target as HTMLSelectElement).value as 'two_pass' | 'disabled',
              )
            "
          >
            <option value="two_pass">Two pass</option>
            <option value="disabled">Disabled</option>
          </select>
        </label>
        <template v-if="settings.loudness.mode === 'two_pass'">
          <label class="flex flex-col gap-1">
            <span class="text-xs text-muted">Integrated (LUFS)</span>
            <input
              v-model.number="settings.loudness.integrated_lufs"
              type="number"
              step="0.5"
              class="field w-32"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-muted">True peak (dBTP)</span>
            <input
              v-model.number="settings.loudness.true_peak_dbtp"
              type="number"
              step="0.1"
              class="field w-32"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs text-muted">Range (LU)</span>
            <input v-model.number="settings.loudness.lra_lu" type="number" class="field w-28" />
          </label>
        </template>
      </div>
    </section>

    <section class="flex flex-col gap-3">
      <h3 class="text-xs tracking-widest text-muted uppercase">Intro and outro</h3>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Intro asset</span>
          <select v-model="settings.intro_reference" class="field">
            <option :value="null">None</option>
            <option v-for="asset in assets" :key="asset" :value="asset">{{ asset }}</option>
          </select>
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Outro asset</span>
          <select v-model="settings.outro_reference" class="field">
            <option :value="null">None</option>
            <option v-for="asset in assets" :key="asset" :value="asset">{{ asset }}</option>
          </select>
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Fade in (seconds)</span>
          <input v-model.number="settings.fade_in_seconds" type="number" step="0.1" class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs text-muted">Fade out (seconds)</span>
          <input v-model.number="settings.fade_out_seconds" type="number" step="0.1" class="field" />
        </label>
      </div>
    </section>

    <p v-if="failure" role="alert" class="rounded-lg bg-danger/15 p-3 text-sm text-danger">
      {{ failure }}
    </p>

    <div class="flex gap-2">
      <button type="submit" class="btn-primary" :disabled="busy">
        {{ profile ? "Save profile" : "Create profile" }}
      </button>
      <button type="button" class="btn" @click="emit('cancel')">Cancel</button>
    </div>
  </form>
  <p v-else class="text-sm text-muted">No profile to start from yet.</p>
</template>
