<script setup lang="ts">
import { ref, watch } from "vue";
import ProfileField from "@/components/ProfileField.vue";
import { apiFetch } from "@/composables/useApi";
import { useCollections } from "@/composables/useCollections";
import { cloneSettings, withLoudnessMode, withQualityMode, withScalingMode } from "@/lib/profile";
import {
  AUDIO_CHANNELS,
  AUDIO_CODECS,
  CONTAINERS,
  H264_LEVELS,
  H264_PROFILES,
  HELP,
  PIXEL_FORMATS,
  SAMPLE_RATES,
  VIDEO_CODECS,
  VIDEO_PRESETS,
} from "@/lib/profileOptions";
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
    // The asset list is a convenience; a reference can still be typed.
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
  <form v-if="settings" class="flex flex-col gap-6" @submit.prevent="save">
    <section class="grid gap-4 sm:grid-cols-2">
      <ProfileField label="Profile ID">
        <input v-model="id" class="field" required :disabled="Boolean(profile)" />
      </ProfileField>
      <ProfileField label="Name">
        <input v-model="name" class="field" required />
      </ProfileField>
    </section>

    <section class="flex flex-col gap-4">
      <h3 class="text-xs tracking-widest text-muted uppercase">Video</h3>
      <div class="grid gap-4 sm:grid-cols-3">
        <ProfileField label="Width" :help="HELP.width">
          <input v-model.number="settings.video.width" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Height" :help="HELP.height">
          <input v-model.number="settings.video.height" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Frames per second" :help="HELP.fps">
          <input v-model.number="settings.video.fps" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Codec" :help="HELP.codec">
          <select v-model="settings.video.codec" class="field">
            <option v-for="choice in VIDEO_CODECS" :key="choice.value" :value="choice.value">
              {{ choice.label }}
            </option>
          </select>
        </ProfileField>
        <ProfileField label="Preset" :help="HELP.preset">
          <select v-model="settings.video.preset" class="field">
            <option v-for="choice in VIDEO_PRESETS" :key="choice.value" :value="choice.value">
              {{ choice.label }}
            </option>
          </select>
        </ProfileField>
        <ProfileField label="Pixel format" :help="HELP.pixel_format">
          <input v-model="settings.video.pixel_format" class="field" list="pixel-formats" />
          <datalist id="pixel-formats">
            <option v-for="choice in PIXEL_FORMATS" :key="choice.value" :value="choice.value" />
          </datalist>
        </ProfileField>
        <ProfileField label="H.264 profile" :help="HELP.h264_profile">
          <input v-model="settings.video.h264_profile" class="field" list="h264-profiles" />
          <datalist id="h264-profiles">
            <option v-for="choice in H264_PROFILES" :key="choice.value" :value="choice.value" />
          </datalist>
        </ProfileField>
        <ProfileField label="Level" :help="HELP.level">
          <input v-model="settings.video.level" class="field" list="h264-levels" />
          <datalist id="h264-levels">
            <option v-for="choice in H264_LEVELS" :key="choice.value" :value="choice.value" />
          </datalist>
        </ProfileField>
        <ProfileField label="Keyframe interval (seconds)" :help="HELP.keyframe">
          <input
            v-model.number="settings.video.keyframe_interval_seconds"
            type="number"
            step="0.5"
            class="field"
          />
        </ProfileField>
      </div>

      <div class="grid gap-4 sm:grid-cols-3">
        <ProfileField label="Quality" :help="HELP.quality">
          <select
            class="field"
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
        </ProfileField>
        <ProfileField
          v-if="settings.video.quality.mode === 'crf'"
          label="CRF"
          :help="HELP.crf"
        >
          <input v-model.number="settings.video.quality.crf" type="number" step="0.5" class="field" />
        </ProfileField>
        <ProfileField v-else label="Bitrate (kbps)" :help="HELP.bitrate">
          <input v-model.number="settings.video.quality.bitrate_kbps" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Max bitrate (kbps)" :help="HELP.maxrate">
          <input v-model.number="settings.video.maxrate_kbps" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Buffer size (kbps)" :help="HELP.bufsize">
          <input v-model.number="settings.video.bufsize_kbps" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Fast start" :help="HELP.fast_start">
          <select v-model="settings.video.fast_start" class="field">
            <option :value="true">On</option>
            <option :value="false">Off</option>
          </select>
        </ProfileField>
      </div>

      <div class="grid gap-4 sm:grid-cols-3">
        <ProfileField label="Scaling" :help="HELP.scaling">
          <select
            class="field"
            :value="settings.video.scaling.strategy"
            @change="
              settings.video.scaling = withScalingMode(
                settings.video.scaling,
                ($event.target as HTMLSelectElement).value as 'aspect_fit' | 'crop',
              )
            "
          >
            <option value="aspect_fit">Fit and pad</option>
            <option value="crop">Crop to fill</option>
          </select>
        </ProfileField>
        <ProfileField label="Scale width">
          <input v-model.number="settings.video.scaling.width" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Scale height">
          <input v-model.number="settings.video.scaling.height" type="number" class="field" />
        </ProfileField>
        <template v-if="settings.video.scaling.strategy === 'aspect_fit'">
          <ProfileField label="Pixel aspect numerator" :help="HELP.sar">
            <input v-model.number="settings.video.scaling.sar_num" type="number" class="field" />
          </ProfileField>
          <ProfileField label="Pixel aspect denominator">
            <input v-model.number="settings.video.scaling.sar_den" type="number" class="field" />
          </ProfileField>
        </template>
      </div>
    </section>

    <section class="flex flex-col gap-4">
      <h3 class="text-xs tracking-widest text-muted uppercase">Audio</h3>
      <div class="grid gap-4 sm:grid-cols-3">
        <ProfileField label="Codec" :help="HELP.audio_codec">
          <select v-model="settings.audio.codec" class="field">
            <option v-for="choice in AUDIO_CODECS" :key="choice.value" :value="choice.value">
              {{ choice.label }}
            </option>
          </select>
        </ProfileField>
        <ProfileField label="Bitrate (kbps)" :help="HELP.audio_bitrate">
          <input v-model.number="settings.audio.bitrate_kbps" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Channels" :help="HELP.channels">
          <select
            class="field"
            :value="String(settings.audio.channels)"
            @change="
              settings.audio.channels = Number(($event.target as HTMLSelectElement).value)
            "
          >
            <option v-for="choice in AUDIO_CHANNELS" :key="choice.value" :value="choice.value">
              {{ choice.label }}
            </option>
          </select>
        </ProfileField>
        <ProfileField label="Sample rate (Hz)" :help="HELP.sample_rate">
          <input
            v-model.number="settings.audio.sample_rate"
            type="number"
            class="field"
            list="sample-rates"
          />
          <datalist id="sample-rates">
            <option v-for="choice in SAMPLE_RATES" :key="choice.value" :value="choice.value" />
          </datalist>
        </ProfileField>
        <ProfileField label="When a clip has no audio" :help="HELP.missing_policy">
          <select v-model="settings.audio.missing_policy.mode" class="field">
            <option value="required">Refuse the clip</option>
            <option value="silence">Add silence</option>
          </select>
        </ProfileField>
        <ProfileField label="Fallback track" :help="HELP.fallback">
          <select v-model="settings.audio.fallback" class="field">
            <option value="none">None</option>
            <option value="silence">Silence</option>
          </select>
        </ProfileField>
        <ProfileField label="Pad or trim to video" :help="HELP.pad_or_trim">
          <select v-model="settings.audio.pad_or_trim" class="field">
            <option :value="true">On</option>
            <option :value="false">Off</option>
          </select>
        </ProfileField>
      </div>
    </section>

    <section class="flex flex-col gap-4">
      <h3 class="text-xs tracking-widest text-muted uppercase">Loudness</h3>
      <div class="grid gap-4 sm:grid-cols-4">
        <ProfileField label="Analysis" :help="HELP.loudness">
          <select
            class="field"
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
        </ProfileField>
        <template v-if="settings.loudness.mode === 'two_pass'">
          <ProfileField label="Integrated (LUFS)" :help="HELP.integrated_lufs">
            <input
              v-model.number="settings.loudness.integrated_lufs"
              type="number"
              step="0.5"
              class="field"
            />
          </ProfileField>
          <ProfileField label="True peak (dBTP)" :help="HELP.true_peak">
            <input
              v-model.number="settings.loudness.true_peak_dbtp"
              type="number"
              step="0.1"
              class="field"
            />
          </ProfileField>
          <ProfileField label="Loudness range (LU)" :help="HELP.lra">
            <input v-model.number="settings.loudness.lra_lu" type="number" class="field" />
          </ProfileField>
        </template>
      </div>
    </section>

    <section class="flex flex-col gap-4">
      <h3 class="text-xs tracking-widest text-muted uppercase">Intro, outro, and fades</h3>
      <div class="grid gap-4 sm:grid-cols-2">
        <ProfileField label="Intro asset" :help="HELP.intro">
          <select v-model="settings.intro_reference" class="field">
            <option :value="null">None</option>
            <option v-for="asset in assets" :key="asset" :value="asset">{{ asset }}</option>
          </select>
        </ProfileField>
        <ProfileField label="Outro asset" :help="HELP.outro">
          <select v-model="settings.outro_reference" class="field">
            <option :value="null">None</option>
            <option v-for="asset in assets" :key="asset" :value="asset">{{ asset }}</option>
          </select>
        </ProfileField>
        <ProfileField label="Fade in (seconds)" :help="HELP.fade_in">
          <input v-model.number="settings.fade_in_seconds" type="number" step="0.1" class="field" />
        </ProfileField>
        <ProfileField label="Fade out (seconds)" :help="HELP.fade_out">
          <input
            v-model.number="settings.fade_out_seconds"
            type="number"
            step="0.1"
            class="field"
          />
        </ProfileField>
      </div>
    </section>

    <section class="flex flex-col gap-4">
      <h3 class="text-xs tracking-widest text-muted uppercase">Output</h3>
      <div class="grid gap-4 sm:grid-cols-3">
        <ProfileField label="Container" :help="HELP.container">
          <select v-model="settings.output.container" class="field">
            <option v-for="choice in CONTAINERS" :key="choice.value" :value="choice.value">
              {{ choice.label }}
            </option>
          </select>
        </ProfileField>
        <ProfileField label="Damaged source" :help="HELP.decode_error_policy">
          <select v-model="settings.decode_error_policy" class="field">
            <option value="warn">Warn and keep going</option>
            <option value="fail">Fail the job</option>
          </select>
        </ProfileField>
        <ProfileField label="Hardware acceleration">
          <select v-model="settings.hardware_acceleration" class="field">
            <option :value="false">Off</option>
            <option :value="true">On</option>
          </select>
        </ProfileField>
        <ProfileField label="Timeout (seconds)" :help="HELP.timeout">
          <input v-model.number="settings.timeout_seconds" type="number" class="field" />
        </ProfileField>
        <ProfileField label="Timeout per source minute" :help="HELP.timeout_per_minute">
          <input
            v-model.number="settings.timeout_seconds_per_minute"
            type="number"
            class="field"
          />
        </ProfileField>
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
