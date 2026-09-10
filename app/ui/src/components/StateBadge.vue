<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ state: string }>();

const tone = computed(() => {
  if (props.state === "compiled") return "bg-ok/15 text-ok";
  if (props.state === "failed" || props.state === "invalid") return "bg-danger/15 text-danger";
  return "bg-warn/15 text-warn";
});
</script>

<template>
  <span class="rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap" :class="tone">
    {{ state }}
  </span>
</template>
